from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import (
    AgentOutputInvalidError,
    ConflictError,
    NotFoundError,
    WorkflowStateError,
    register_exception_handlers,
)


class Payload(BaseModel):
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


def test_validation_errors_use_standard_envelope(client: TestClient) -> None:
    response = client.post("/payload", json={})

    assert response.status_code == 422
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "The request validation failed."
    assert isinstance(body["error"]["details"], list)


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
