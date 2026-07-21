from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.logging import APP_LOGGER_NAME
from app.main import create_app
from app.models import ActionRequest, WorkflowCheckpoint, WorkflowEvent, WorkflowRun
from app.services import ProjectService
from app.services.project_creation_service import ProjectCreationService
from app.workspace import ProjectWorkspace


@pytest.fixture
async def project_creation_client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def create_project(async_session: AsyncSession, workspace_base: Path):
    return await ProjectService(async_session, ProjectWorkspace(workspace_base)).create_project(
        slug=f"creation-routes-{workspace_base.name}", title="The Amber Archive"
    )


def creation_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/creation"


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_read_resolve_and_replay_project_creation(
    project_creation_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project = await create_project(async_session, tmp_path / "workflow")
    opaque = "do-not-persist-sk-project-creation-secret"
    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    started = await project_creation_client.post(
        f"{creation_url(str(project.id))}/start",
        json={
            "user_seed": opaque,
            "target_platform": "web",
            "preferred_genres": ["mystery"],
            "disliked_elements": ["gore"],
            "style_preference": "spare",
        },
    )

    assert started.status_code == 201, started.text
    body = started.json()
    assert body == {
        "id": body["id"],
        "type": "project_creation",
        "status": "user_idea",
        "current_node": "user_idea",
        "next_node": None,
        "awaiting_user": False,
        "pending_action": None,
    }
    assert opaque not in started.text
    assert opaque not in caplog.text
    run_id = body["id"]
    run_uuid = UUID(run_id)
    fetched = await project_creation_client.get(f"{creation_url(str(project.id))}/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    # Models the trusted #65 transition; the public start endpoint must not perform it.
    waiting = await ProjectCreationService(async_session).request_concept_review(run_uuid)
    waiting_read = await project_creation_client.get(f"{creation_url(str(project.id))}/{run_id}")
    assert waiting_read.status_code == 200
    assert waiting_read.json()["pending_action"] == {
        "id": str(waiting.action_request_id),
        "type": "project_creation_concept_review",
        "status": "pending",
        "allowed_decisions": ["approved", "rejected"],
    }
    assert opaque not in waiting_read.text

    resolved = await project_creation_client.post(
        f"{creation_url(str(project.id))}/{run_id}/actions/{waiting.action_request_id}/resolve",
        json={"decision": "approved"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "concept_reviewed"
    assert resolved.json()["pending_action"] is None
    replay = await project_creation_client.post(
        f"{creation_url(str(project.id))}/{run_id}/actions/{waiting.action_request_id}/resolve",
        json={"decision": "approved"},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "workflow_state_error"

    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.workflow_run_id == run_uuid)
        )
    )
    events = list(
        await async_session.scalars(
            select(WorkflowEvent).where(WorkflowEvent.workflow_run_id == run_uuid)
        )
    )
    actions = list(
        await async_session.scalars(
            select(ActionRequest).where(ActionRequest.workflow_run_id == run_uuid)
        )
    )
    run = await async_session.get(WorkflowRun, run_uuid)
    assert run is not None
    assert opaque not in str(run.metadata_)
    assert all(opaque not in str(item.state_json) for item in checkpoints)
    assert all(opaque not in str(item.payload) for item in events)
    assert all(opaque not in str(item.metadata_) and opaque not in item.prompt for item in actions)
    assert actions[0].status == "approved"

    rejected_project = await create_project(async_session, tmp_path / "rejected")
    rejected = await project_creation_client.post(
        f"{creation_url(str(rejected_project.id))}/start", json={"user_seed": "another seed"}
    )
    rejected_run_id = UUID(rejected.json()["id"])
    rejected_action = await ProjectCreationService(async_session).request_concept_review(rejected_run_id)
    rejected_response = await project_creation_client.post(
        f"{creation_url(str(rejected_project.id))}/{rejected_run_id}/actions/"
        f"{rejected_action.action_request_id}/resolve",
        json={"decision": "rejected"},
    )
    assert rejected_response.status_code == 200
    assert rejected_response.json()["status"] == "rejected"


@pytest.mark.integration
@pytest.mark.anyio
async def test_project_creation_scopes_foreign_and_wrong_ids_as_not_found(
    project_creation_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    first = await create_project(async_session, tmp_path / "first")
    second = await create_project(async_session, tmp_path / "second")
    started = await project_creation_client.post(
        f"{creation_url(str(first.id))}/start", json={"user_seed": "seed"}
    )
    run_id = started.json()["id"]
    response = await project_creation_client.get(f"{creation_url(str(second.id))}/{run_id}")
    assert response.status_code == 404
    action = uuid4()
    response = await project_creation_client.post(
        f"{creation_url(str(first.id))}/{run_id}/actions/{action}/resolve", json={"decision": "approved"}
    )
    assert response.status_code == 404

    first_waiting = await ProjectCreationService(async_session).request_concept_review(UUID(run_id))
    second_started = await project_creation_client.post(
        f"{creation_url(str(second.id))}/start", json={"user_seed": "second seed"}
    )
    second_run_id = UUID(second_started.json()["id"])
    foreign_action = await ProjectCreationService(async_session).request_concept_review(second_run_id)
    response = await project_creation_client.post(
        f"{creation_url(str(first.id))}/{run_id}/actions/{foreign_action.action_request_id}/resolve",
        json={"decision": "approved"},
    )
    assert response.status_code == 404
    still_waiting = await async_session.get(ActionRequest, first_waiting.action_request_id)
    assert still_waiting is not None
    assert still_waiting.status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
async def test_project_creation_rejects_validation_bypass_and_corrupt_state_fails_closed(
    project_creation_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "validation")
    secret = "opaque-invalid-seed"
    invalid = await project_creation_client.post(
        f"{creation_url(str(project.id))}/start",
        json={"user_seed": secret, "status": "completed"},
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    assert invalid.json()["error"]["details"] == [{"type": "validation_error"}]

    unsafe_field = f"unsafe-{secret}"
    invalid_value = await project_creation_client.post(
        f"{creation_url(str(project.id))}/start",
        json={"user_seed": 1, unsafe_field: secret},
    )
    assert invalid_value.status_code == 422
    assert secret not in invalid_value.text
    assert invalid_value.json()["error"]["details"] == [
        {"type": "validation_error"},
        {"type": "validation_error"},
    ]

    started = await project_creation_client.post(
        f"{creation_url(str(project.id))}/start", json={"user_seed": "safe"}
    )
    run_id = started.json()["id"]
    run = await async_session.get(WorkflowRun, UUID(run_id))
    assert run is not None
    run.metadata_ = {"secret": secret}
    run.status = "corrupt"
    await async_session.commit()
    response = await project_creation_client.get(f"{creation_url(str(project.id))}/{run_id}")
    assert response.status_code == 409
    assert secret not in response.text


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["revise", "cancel", "force_approved"])
async def test_project_creation_rejects_non_confirmation_decisions_without_transition(
    project_creation_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    decision: str,
) -> None:
    project = await create_project(async_session, tmp_path / decision)
    started = await project_creation_client.post(
        f"{creation_url(str(project.id))}/start", json={"user_seed": "safe"}
    )
    run_id = UUID(started.json()["id"])
    waiting = await ProjectCreationService(async_session).request_concept_review(run_id)
    opaque = f"opaque-{decision}-secret"

    response = await project_creation_client.post(
        f"{creation_url(str(project.id))}/{run_id}/actions/{waiting.action_request_id}/resolve",
        json={"decision": decision, "opaque": opaque},
    )

    assert response.status_code == 422
    assert opaque not in response.text
    assert response.json()["error"]["details"] == [
        {"type": "validation_error"},
        {"type": "validation_error"},
    ]
    action = await async_session.get(ActionRequest, waiting.action_request_id)
    run = await async_session.get(WorkflowRun, run_id)
    assert action is not None and action.status == "pending"
    assert run is not None and run.status == "concept_options" and run.awaiting_user is True
