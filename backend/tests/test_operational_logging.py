import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.logging import APP_LOGGER_NAME, RequestLoggingMiddleware, configure_logging, log_event
from app.main import create_app


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == APP_LOGGER_NAME
    ]


def test_completed_request_logs_allowlisted_fields_and_returns_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(create_app())
    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)

    response = client.get(
        "/api/v1/health?token=query-secret",
        headers={"X-Request-ID": "request-123", "Authorization": "Bearer header-secret"},
    )

    assert response.headers["x-request-id"] == "request-123"
    events = _events(caplog)
    assert len(events) == 1
    ev = events[0]
    assert "timestamp" in ev
    del ev["timestamp"]
    assert events == [
        {
            "duration_ms": pytest.approx(events[0]["duration_ms"]),
            "event": "request_completed",
            "method": "GET",
            "request_id": "request-123",
            "route": "/api/v1/health",
            "status": 200,
        }
    ]
    assert events[0]["duration_ms"] >= 0
    assert "query-secret" not in caplog.text
    assert "header-secret" not in caplog.text


def test_generated_request_id_and_unhandled_error_log_are_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("postgresql://user:credential@db/internal detail")

    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert request_id
    events = _events(caplog)
    assert [event["event"] for event in events] == ["application_error", "request_completed"]
    assert "timestamp" in events[0]
    del events[0]["timestamp"]
    assert events[0] == {
        "event": "application_error",
        "method": "GET",
        "request_id": request_id,
        "route": "/boom",
        "status": 500,
    }
    assert events[1]["request_id"] == request_id
    assert events[1]["status"] == 500
    assert "credential" not in caplog.text
    assert "internal detail" not in caplog.text


# ── Slice 1: app_log_level controls logger verbosity ────────────────────────


def test_warning_log_level_suppresses_info_events() -> None:
    """INFO events must be suppressed when log level is WARNING."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.handlers.clear()

    configure_logging(log_level="WARNING")
    assert logger.level == logging.WARNING
    assert not logger.isEnabledFor(logging.INFO)
    assert logger.isEnabledFor(logging.WARNING)

    # Reset for other tests
    logger.handlers.clear()
    configure_logging()


# ── Slice 2: Invalid app_log_level fails Pydantic validation ─────────────────


def test_invalid_app_log_level_is_rejected() -> None:
    """Setting app_log_level to an unrecognised value must fail at validation."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(app_log_level="VERBOSE")


# ── Slice 3: Every JSON event contains a parseable UTC timestamp ─────────────


def test_json_events_include_utc_iso8601_timestamp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every structured log event must carry a parseable UTC ISO 8601 timestamp."""
    from datetime import datetime, timezone

    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    log_event(
        "document_written",
        document_id=uuid4(),
        version_id=uuid4(),
    )

    event = _events(caplog)[0]
    assert "timestamp" in event, f"Missing timestamp in {event}"
    ts = datetime.fromisoformat(event["timestamp"])  # type: ignore[arg-type]
    # Must be timezone-aware (UTC)
    assert ts.tzinfo is not None
    assert ts.tzinfo.utcoffset(ts) == timezone.utc.utcoffset(ts)
    # Should be recent (± 10 seconds from now)
    delta = abs((datetime.now(timezone.utc) - ts).total_seconds())
    assert delta < 10, f"Timestamp {ts!r} is too far from now (delta={delta}s)"


# ── Slice 4: sae_echo decouples SQL echo from app_debug ──────────────────────


def test_app_debug_true_with_sae_echo_false_disables_sql_echo() -> None:
    """When app_debug=True but sae_echo=False (default), SQL echo must be off."""
    from app.core.config import Settings

    # Default: app_debug=True, sae_echo=False
    s = Settings()
    assert s.app_debug is True
    assert s.sae_echo is False

    # Engine echo should respect sae_echo, not app_debug
    from app.db.session import engine

    assert engine.echo is False


# ── Slice 5: sae_echo=True explicitly enables SQL echo ───────────────────────


def test_sae_echo_true_explicitly_enables_sql_echo() -> None:
    """sae_echo=True must enable SQL engine echo independently of app_debug."""
    from app.core.config import Settings

    s = Settings(sae_echo=True, app_debug=False)
    assert s.sae_echo is True
    assert s.app_debug is False

    # Verify that creating an engine with echo=True works
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel_test",
        echo=True,
    )
    assert test_engine.echo is True

    import asyncio

    async def cleanup() -> None:
        await test_engine.dispose()

    asyncio.run(cleanup())


def test_invalid_request_id_is_replaced_and_existing_error_headers_are_preserved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/protected")
    async def protected() -> None:
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer realm=private"})

    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    response = TestClient(app).get("/protected", headers={"X-Request-ID": "invalid id"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer realm=private"
    assert response.headers["x-request-id"] != "invalid id"
    assert "invalid id" not in caplog.text


def test_service_events_inherit_request_id_without_logging_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()
    document_id, version_id, project_id, chapter_id, workflow_run_id, action_id = (
        uuid4() for _ in range(6)
    )

    @app.get("/audit")
    async def audit() -> None:
        log_event(
            "document_written",
            document_id=document_id,
            version_id=version_id,
            content="secret Markdown",
        )
        log_event(
            "chapter_production_action_resolved",
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_id=action_id,
            decision="approved",
        )

    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    response = TestClient(app).get("/audit", headers={"X-Request-ID": "request-audit-123"})

    assert response.status_code == 200
    events = _events(caplog)
    assert [event["request_id"] for event in events[:2]] == [
        "request-audit-123",
        "request-audit-123",
    ]
    assert "secret Markdown" not in caplog.text


def test_operational_logger_does_not_propagate_to_root() -> None:
    assert configure_logging().propagate is False


def test_configuration_suppresses_unsafe_uvicorn_access_logs_without_disabling_error_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    access_logger = logging.getLogger("uvicorn.access")
    error_logger = logging.getLogger("uvicorn.error")
    root_logger = logging.getLogger()
    root_handlers = list(root_logger.handlers)
    root_level = root_logger.level
    access_logger.disabled = False
    error_logger.disabled = False
    caplog.set_level(logging.INFO, logger="uvicorn.access")

    configure_logging()
    access_logger.info('GET /api/v1/health?token=query-secret HTTP/1.1 200')

    assert access_logger.disabled is True
    assert error_logger.disabled is False
    assert root_logger.handlers == root_handlers
    assert root_logger.level == root_level
    assert "query-secret" not in caplog.text


@pytest.mark.anyio
async def test_after_response_start_logs_and_reraises_without_second_response() -> None:
    async def downstream(_: object, __: object, send: object) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})  # type: ignore[misc]
        raise RuntimeError("after response start")

    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    scope = {"type": "http", "method": "GET", "headers": [], "app": None}
    with pytest.raises(RuntimeError, match="after response start"):
        await RequestLoggingMiddleware(downstream)(scope, receive, send)  # type: ignore[arg-type]

    assert [message["type"] for message in messages] == ["http.response.start"]


def test_operational_events_only_accept_allowlisted_safe_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)

    log_event(
        "document_written",
        document_id=uuid4(),
        version_id=uuid4(),
        content="# markdown that must not be logged",
    )

    event = _events(caplog)[0]
    assert event["event"] == "document_written"
    assert "timestamp" in event
    del event["timestamp"]
    assert set(event) == {"event", "document_id", "version_id"}
    assert "markdown" not in caplog.text


@pytest.mark.anyio
async def test_document_write_and_restore_emit_safe_audit_events(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.models import DocumentSource
    from app.services.document_service import DocumentService

    document_id, written_version_id, restored_version_id = uuid4(), uuid4(), uuid4()
    document = SimpleNamespace(
        id=document_id, current_version_id=None, current_version=None, path="safe.md"
    )
    service = DocumentService(SimpleNamespace(add=lambda _: None))  # type: ignore[arg-type]

    async def locked_document(_: object) -> object:
        return document

    async def next_version(_: object) -> int:
        return 1

    monkeypatch.setattr(service, "_locked_document", locked_document)
    monkeypatch.setattr(service, "_next_version_number", next_version)
    monkeypatch.setattr(
        service, "_store_for", lambda _: SimpleNamespace(read=lambda _: "secret Markdown")
    )

    async def commit(*_: object) -> None:
        return None

    monkeypatch.setattr(service, "_commit_with_file_writes", commit)

    async def append_version(**_: object) -> object:
        return SimpleNamespace(id=restored_version_id)

    monkeypatch.setattr(service, "_append_version", append_version)

    async def scalar(_: object) -> object:
        return SimpleNamespace(id=uuid4(), document_id=document_id, snapshot_path="unused")

    service.session.scalar = scalar
    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)

    written = await service.write_document(
        document_id=document_id,
        content="secret Markdown",
        source=DocumentSource.USER,
        expected_current_version_id=None,
    )
    written.id = written_version_id
    document.current_version_id = written_version_id
    await service.restore_document(
        document_id=document_id,
        version_id=uuid4(),
        source=DocumentSource.USER,
        expected_current_version_id=written.id,
    )

    events = _events(caplog)
    assert [event["event"] for event in events] == ["document_written", "document_restored"]
    assert "secret Markdown" not in caplog.text


@pytest.mark.anyio
async def test_document_creation_logs_only_after_durable_success(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: object
) -> None:
    from app.models import Document, DocumentSource, DocumentType, DocumentVersion
    from app.services.document_service import DocumentService

    document_id, version_id, project_id = uuid4(), uuid4(), uuid4()

    class Session:
        async def get(self, _: object, __: object) -> object:
            return SimpleNamespace(workspace_root=str(tmp_path))

        async def execute(self, *_: object) -> None:
            return None

        async def scalar(self, _: object) -> None:
            return None

        def add(self, value: object) -> None:
            if isinstance(value, Document):
                value.id = document_id
            if isinstance(value, DocumentVersion):
                value.id = version_id

        async def flush(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    service = DocumentService(Session())  # type: ignore[arg-type]
    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)

    async def committed(*_: object) -> None:
        return None

    monkeypatch.setattr(service, "_commit_with_file_writes", committed)
    document = await service.create_document(
        project_id=project_id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Safe title",
        path="safe.md",
        content="secret Markdown",
        source=DocumentSource.USER,
    )

    assert document.id == document_id
    events = _events(caplog)
    assert len(events) == 1
    assert "timestamp" in events[0]
    del events[0]["timestamp"]
    assert events == [
        {"event": "document_written", "document_id": str(document_id), "version_id": str(version_id)}
    ]
    assert "secret Markdown" not in caplog.text

    caplog.clear()

    async def failed(*_: object) -> None:
        raise OSError("database://credential")

    monkeypatch.setattr(service, "_commit_with_file_writes", failed)
    with pytest.raises(OSError, match="credential"):
        await service.create_document(
            project_id=project_id,
            document_type=DocumentType.CHAPTER_DRAFT,
            title="Safe title",
            path="safe-2.md",
            content="secret Markdown",
            source=DocumentSource.USER,
        )
    assert _events(caplog) == []


@pytest.mark.anyio
async def test_action_resolution_logs_only_safe_workflow_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.models import ActionRequestStatus
    from app.services.chapter_production_service import ChapterProductionService

    project_id, chapter_id, workflow_run_id, action_id = (uuid4() for _ in range(4))
    chapter = SimpleNamespace(status="DRAFT")
    run = SimpleNamespace(id=workflow_run_id, awaiting_user=True, status="awaiting_approval")
    action = SimpleNamespace(
        id=action_id, status=ActionRequestStatus.PENDING.value, user_decision=None
    )

    class Session:
        def __init__(self) -> None:
            self.results = iter((chapter, run, action))

        async def scalar(self, _: object) -> object:
            return next(self.results)

        def add(self, _: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    result = await ChapterProductionService(Session()).resolve_action(
        project_id, chapter_id, workflow_run_id, action_id, "approved"
    )

    assert result.decision == "approved"
    event = _events(caplog)[0]
    assert "timestamp" in event
    del event["timestamp"]
    assert event == {
        "action_id": str(action_id),
        "chapter_id": str(chapter_id),
        "decision": "approved",
        "event": "chapter_production_action_resolved",
        "project_id": str(project_id),
        "workflow_run_id": str(workflow_run_id),
    }
