"""Safe, structured operational logging for the application."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import UUID, uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

APP_LOGGER_NAME = "guranovel"
_REQUEST_ID = "x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_request_id_context: ContextVar[str | None] = ContextVar("guranovel_request_id", default=None)

_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "request_completed": frozenset({"method", "route", "status", "request_id", "duration_ms"}),
    "application_error": frozenset({"method", "route", "status", "request_id"}),
    "document_written": frozenset({"document_id", "version_id", "request_id"}),
    "document_restored": frozenset(
        {"document_id", "version_id", "restored_from_version_id", "request_id"}
    ),
    "chapter_production_started": frozenset(
        {"project_id", "chapter_id", "workflow_run_id", "action_id", "request_id"}
    ),
    "chapter_production_action_resolved": frozenset(
        {"project_id", "chapter_id", "workflow_run_id", "action_id", "decision", "request_id"}
    ),
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def _resolve_log_level(level: str) -> int:
    """Convert a log-level string to a ``logging`` level constant."""
    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        raise ValueError(f"Invalid log level: {level!r}")
    return numeric


def configure_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure only the dedicated application logger, without changing root logging."""
    # request_completed replaces Uvicorn's unsafe access log, which includes raw paths and queries.
    logging.getLogger("uvicorn.access").disabled = True
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.propagate = False
    if not any(getattr(handler, "_guranovel_operational", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler._guranovel_operational = True  # type: ignore[attr-defined]
        handler.setLevel(logging.INFO)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(_resolve_log_level(log_level))
    return logger


def log_event(event: str, **fields: object) -> None:
    """Emit an allowlisted JSON event; unexpected fields are discarded.

    This intentionally accepts no free-form message, exception, body, or response data.
    """
    allowed = _EVENT_FIELDS.get(event)
    if allowed is None:
        raise ValueError("Unknown operational log event")
    record: dict[str, object] = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for name in allowed:
        value = fields.get(name)
        if name == "request_id" and value is None:
            value = _request_id_context.get()
        safe_value = _safe_field(name, value)
        if safe_value is not None:
            record[name] = safe_value
    configure_logging().info(json.dumps(record, sort_keys=True, separators=(",", ":")))


def _safe_field(name: str, value: object) -> str | int | float | None:
    if value is None:
        return None
    if name in {"method", "route", "decision"}:
        return value if isinstance(value, str) else None
    if name == "request_id":
        return value if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value) else None
    if name in {"status"}:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if name == "duration_ms":
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if name.endswith("_id"):
        return str(value) if isinstance(value, UUID) else None
    return None


class RequestLoggingMiddleware:
    """Attach a correlation ID and emit one completion event per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        scope["guranovel_request_id"] = request_id
        request_id_token = _request_id_context.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _REQUEST_ID.encode()
                ]
                headers.append((_REQUEST_ID.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            log_event(
                "application_error",
                method=scope["method"],
                route=_route_template(scope),
                status=status_code if response_started else 500,
                request_id=request_id,
            )
            if response_started:
                raise
            # ServerErrorMiddleware sits outside user middleware, so letting this
            # exception escape would bypass the correlation response header.
            from app.core.errors import unexpected_error_handler

            response = await unexpected_error_handler(Request(scope), Exception())
            await response(scope, receive, send_with_request_id)
        finally:
            log_event(
                "request_completed",
                method=scope["method"],
                route=_route_template(scope),
                status=status_code,
                request_id=request_id,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            _request_id_context.reset(request_id_token)


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == _REQUEST_ID.encode():
            candidate = value.decode("latin-1")
            if _SAFE_REQUEST_ID.fullmatch(candidate):
                return candidate
            break
    return str(uuid4())


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    app = scope.get("app")
    return _configured_route_template(getattr(app, "router", None), endpoint) or "unmatched"


def _configured_route_template(router: object, endpoint: object, prefix: str = "") -> str | None:
    for configured_route in getattr(router, "routes", []):
        path = getattr(configured_route, "path", None)
        if getattr(configured_route, "endpoint", None) is endpoint:
            if isinstance(path, str):
                return f"{prefix}{path}"
        nested_router = getattr(configured_route, "original_router", configured_route)
        nested_prefix = prefix
        # FastAPI's top-level included router retains its prefix on the original
        # router. Nested included routers already retain their prefixes in their
        # leaf route templates, so do not concatenate them a second time.
        if getattr(configured_route, "original_router", None) is not None and not prefix:
            router_prefix = getattr(nested_router, "prefix", "")
            if isinstance(router_prefix, str):
                nested_prefix += router_prefix
        nested = _configured_route_template(nested_router, endpoint, nested_prefix)
        if nested is not None:
            return nested
    return None
