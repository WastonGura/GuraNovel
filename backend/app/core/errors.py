"""Application errors and their FastAPI response handlers."""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


HTTP_ERROR_CODES: dict[int, tuple[str, str]] = {
    status.HTTP_400_BAD_REQUEST: ("bad_request", "The request is invalid."),
    status.HTTP_401_UNAUTHORIZED: ("unauthorized", "Authentication is required."),
    status.HTTP_403_FORBIDDEN: ("forbidden", "You do not have permission to access this resource."),
    status.HTTP_404_NOT_FOUND: ("not_found", "The requested resource was not found."),
    status.HTTP_405_METHOD_NOT_ALLOWED: (
        "method_not_allowed",
        "The request method is not allowed.",
    ),
    status.HTTP_409_CONFLICT: (
        "conflict",
        "The request conflicts with the current resource state.",
    ),
    status.HTTP_422_UNPROCESSABLE_CONTENT: (
        "unprocessable_entity",
        "The request could not be processed.",
    ),
    status.HTTP_429_TOO_MANY_REQUESTS: (
        "rate_limited",
        "Too many requests were made. Please try again later.",
    ),
}


class AppError(Exception):
    """Base exception for errors that are safe to expose to API clients."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "app_error"
    default_message = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = dict(details) if details is not None else None
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    default_message = "The request conflicts with the current resource state."


class WorkflowStateError(ConflictError):
    code = "workflow_state_error"
    default_message = "The workflow is not in a state that allows this operation."


class AgentOutputInvalidError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "agent_output_invalid"
    default_message = "The agent returned an invalid output."


def error_response(
    *, status_code: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    """Build the shared API error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def request_validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        message="The request validation failed.",
        details=exc.errors(),
    )


async def http_exception_handler(
    _: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Normalize framework and explicitly raised HTTP exceptions."""
    if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        return error_response(
            status_code=exc.status_code,
            code="internal_server_error",
            message="An unexpected error occurred.",
        )

    code, message = HTTP_ERROR_CODES.get(
        exc.status_code, ("http_error", "The request could not be completed.")
    )
    return error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        details=exc.detail,
    )


async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
    """Avoid exposing implementation details for unhandled server errors."""
    return error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_server_error",
        message="An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register the API-wide exception handlers on an application instance."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
