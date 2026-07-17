from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.main import create_app
from app.services import ChapterService, ProjectService
from app.workspace import ProjectWorkspace


@pytest.fixture
async def production_client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def create_project_and_chapter(async_session: AsyncSession, workspace_base: Path):
    project = await ProjectService(async_session, ProjectWorkspace(workspace_base)).create_project(
        slug=f"production-routes-{workspace_base.name}", title="Archive of Ash"
    )
    chapter = await ChapterService(async_session).create_chapter(
        project_id=project.id, title="The Locked Door"
    )
    return project, chapter


def production_url(project_id: str, chapter_id: str) -> str:
    return f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-runs"


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_get_and_resolve_chapter_production_run(
    production_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "workspace")
    url = production_url(str(project.id), str(chapter.id))

    started = await production_client.post(url)

    assert started.status_code == 201
    started_body = started.json()
    assert started_body["type"] == "chapter_production"
    assert started_body["status"] == "awaiting_approval"
    assert started_body["current_node"] == "approval"
    assert started_body["next_node"] is None
    assert started_body["awaiting_user"] is True
    assert "workspace_root" not in started_body
    assert "content" not in str(started_body)
    assert len(started_body["actions"]) == 1
    action = started_body["actions"][0]
    assert action == {
        "id": action["id"],
        "type": "chapter_production_approval",
        "status": "pending",
        "options": ["approved", "rejected"],
        "default_option": "approved",
        "user_decision": None,
    }
    assert [event["event_type"] for event in started_body["events"]] == [
        "production_started",
        "fake_output_stored",
        "awaiting_approval",
    ]
    assert all(set(event) == {"event_type", "node_name", "message", "payload"} for event in started_body["events"])
    assert started_body["outline_document_id"]
    assert started_body["draft_document_id"]

    fetched = await production_client.get(f"{url}/{started_body['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == started_body

    resolved = await production_client.post(
        f"{url}/{started_body['id']}/actions/{action['id']}/resolve", json={"decision": "approved"}
    )

    assert resolved.status_code == 200
    resolved_body = resolved.json()
    assert resolved_body["status"] == "completed"
    assert resolved_body["awaiting_user"] is False
    assert resolved_body["actions"][0]["status"] == "approved"
    assert resolved_body["actions"][0]["user_decision"] == "approved"
    assert [event["event_type"] for event in resolved_body["events"]] == [
        "production_started",
        "fake_output_stored",
        "awaiting_approval",
        "approval_approved",
    ]

    replay = await production_client.post(
        f"{url}/{started_body['id']}/actions/{action['id']}/resolve", json={"decision": "approved"}
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "workflow_state_error"


@pytest.mark.integration
@pytest.mark.anyio
async def test_chapter_production_routes_scope_ids_and_reject_invalid_input(
    production_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "first")
    other_project, other_chapter = await create_project_and_chapter(async_session, tmp_path / "second")
    validation_project, validation_chapter = await create_project_and_chapter(
        async_session, tmp_path / "validation"
    )
    url = production_url(str(project.id), str(chapter.id))
    other_url = production_url(str(other_project.id), str(other_chapter.id))
    validation_url = production_url(str(validation_project.id), str(validation_chapter.id))
    started = await production_client.post(url, json={})
    other_started = await production_client.post(other_url, json={})
    assert started.status_code == other_started.status_code == 201
    run_id = started.json()["id"]
    action_id = started.json()["actions"][0]["id"]

    duplicate = await production_client.post(url, json={})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    for payload in (
        {"generated_content": "nope"},
        {"source": "agent"},
        {"project_id": str(uuid4())},
        {"chapter_id": str(uuid4())},
        {"workflow_run_id": str(uuid4())},
        {"status": "completed"},
        {"document_id": str(uuid4())},
        {"workspace_root": "/tmp/nope"},
        {"unexpected": True},
    ):
        response = await production_client.post(validation_url, json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    malformed = await production_client.get(f"{url}/not-a-uuid")
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "validation_error"

    for foreign_url in (
        f"{other_url}/{run_id}",
        f"{url}/{other_started.json()['id']}",
        f"{url}/{run_id}/actions/{other_started.json()['actions'][0]['id']}/resolve",
    ):
        response = (
            await production_client.post(foreign_url, json={"decision": "approved"})
            if "/resolve" in foreign_url
            else await production_client.get(foreign_url)
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    resolve_url = f"{url}/{run_id}/actions/{action_id}/resolve"
    for payload in (
        {},
        {"decision": "revise"},
        {"decision": "approved", "status": "completed"},
        {"decision": "approved", "document_id": str(uuid4())},
    ):
        response = await production_client.post(resolve_url, json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
