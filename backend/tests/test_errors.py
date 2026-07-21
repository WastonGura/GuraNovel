from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.core.errors import (
    AgentOutputInvalidError,
    ConflictError,
    NotFoundError,
    WorkflowStateError,
    register_exception_handlers,
)
from app.llm import (
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/not-found")
    async def not_found() -> None:
        raise NotFoundError("Novel was not found.", details={"novel_id": "missing"})

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError()

    @app.get("/workflow-state")
    async def workflow_state() -> None:
        raise WorkflowStateError()

    @app.get("/agent-output")
    async def agent_output() -> None:
        raise AgentOutputInvalidError()

    @app.get("/provider-unavailable")
    async def provider_unavailable() -> None:
        raise ProviderUnavailableError()

    @app.get("/provider-timeout")
    async def provider_timeout() -> None:
        raise ProviderTimeoutError()

    @app.get("/provider-rate-limited")
    async def provider_rate_limited() -> None:
        raise ProviderRateLimitedError()

    @app.get("/provider-invalid-output")
    async def provider_invalid_output() -> None:
        raise ProviderInvalidOutputError()

    @app.post("/payload")
    async def payload(_: Payload) -> None:
        return None

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this novel.",
        )

    @app.get("/unauthorized")
    async def unauthorized() -> None:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("database credentials: secret")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status_code", "code", "message"),
    [
        ("/not-found", 404, "not_found", "Novel was not found."),
        ("/conflict", 409, "conflict", "The request conflicts with the current resource state."),
        (
            "/workflow-state",
            409,
            "workflow_state_error",
            "The workflow is not in a state that allows this operation.",
        ),
        ("/agent-output", 422, "agent_output_invalid", "The agent returned an invalid output."),
        (
            "/provider-unavailable",
            503,
            "provider_unavailable",
            "The generation provider is temporarily unavailable. Please try again later.",
        ),
        (
            "/provider-timeout",
            504,
            "provider_timeout",
            "The generation provider timed out. Please try again later.",
        ),
        (
            "/provider-rate-limited",
            429,
            "provider_rate_limited",
            "The generation provider is rate limited. Please try again later.",
        ),
        (
            "/provider-invalid-output",
            422,
            "provider_invalid_output",
            "The generation provider returned invalid output.",
        ),
    ],
)
def test_application_errors_use_standard_envelope(
    client: TestClient, path: str, status_code: int, code: str, message: str
) -> None:
    response = client.get(path)

    assert response.status_code == status_code
    assert response.json()["error"] == {
        "code": code,
        "message": message,
        "details": {"novel_id": "missing"} if path == "/not-found" else None,
    }


@pytest.mark.parametrize(
    "error_type",
    [
        ProviderUnavailableError,
        ProviderTimeoutError,
        ProviderRateLimitedError,
        ProviderInvalidOutputError,
    ],
)
def test_provider_errors_never_accept_or_expose_upstream_details(error_type: type[Exception]) -> None:
    upstream_text = "upstream secret: Bearer sk-not-for-clients"
    error = error_type()

    assert upstream_text not in str(error)
    assert getattr(error, "details") is None
    with pytest.raises(TypeError):
        error_type(upstream_text)


def test_validation_errors_use_standard_envelope(client: TestClient) -> None:
    response = client.post("/payload", json={})

    assert response.status_code == 422
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "The request validation failed."
    assert isinstance(body["error"]["details"], list)


def test_validation_errors_do_not_echo_untrusted_input_or_context(client: TestClient) -> None:
    secret = "seed-with-secret-sk-test-value"
    unsafe_field = f"unsafe-{secret}"

    response = client.post("/payload", json={"name": [secret], unsafe_field: secret})

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert secret not in response.text
    assert details == [{"type": "validation_error"}, {"type": "validation_error"}]


def test_unmatched_routes_use_standard_envelope(client: TestClient) -> None:
    response = client.get("/missing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "The requested resource was not found.",
            "details": "Not Found",
        }
    }


def test_http_exceptions_use_standard_envelope(client: TestClient) -> None:
    response = client.get("/forbidden")

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "forbidden",
            "message": "You do not have permission to access this resource.",
            "details": "You do not have access to this novel.",
        }
    }


def test_http_exceptions_preserve_headers(client: TestClient) -> None:
    response = client.get("/unauthorized")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "unauthorized",
            "message": "Authentication is required.",
            "details": "Invalid credentials.",
        }
    }


def test_unexpected_errors_do_not_leak_details(client: TestClient) -> None:
    response = client.get("/unexpected")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected error occurred.",
            "details": None,
        }
    }
