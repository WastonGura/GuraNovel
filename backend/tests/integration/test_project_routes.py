from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_project_workspace
from app.main import create_app
from app.models import Project
from app.services import ProjectCommitIndeterminateError, ProjectService
from app.workspace import ProjectWorkspace


@pytest.fixture
async def project_client(
    async_session: AsyncSession, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, ProjectWorkspace]]:
    app: FastAPI = create_app()
    workspace = ProjectWorkspace(tmp_path / "workspaces")

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_project_workspace] = lambda: workspace
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, workspace


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_project_derives_private_workspace_and_returns_metadata(
    project_client: tuple[httpx.AsyncClient, ProjectWorkspace]
) -> None:
    client, workspace = project_client

    response = await client.post(
        "/api/v1/projects",
        json={
            "slug": "the-tide",
            "title": "The Tide",
            "genre": "fantasy",
            "target_platform": "web",
            "metadata": {"language": "en", "chapters": 12},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "the-tide"
    assert body["title"] == "The Tide"
    assert body["genre"] == "fantasy"
    assert body["target_platform"] == "web"
    assert body["metadata"] == {"language": "en", "chapters": 12}
    assert body["workspace_root"] == str(workspace.root_for("the-tide"))
    assert workspace.root_for("the-tide").is_dir()
    assert {entry.name for entry in workspace.root_for("the-tide").iterdir()} == {
        "outline", "lore", "characters", "chapters", ".versions"
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_and_list_projects_are_creation_ordered(
    project_client: tuple[httpx.AsyncClient, ProjectWorkspace]
) -> None:
    client, _ = project_client
    first = await client.post("/api/v1/projects", json={"slug": "first", "title": "First"})
    second = await client.post("/api/v1/projects", json={"slug": "second", "title": "Second"})

    fetched = await client.get(f"/api/v1/projects/{first.json()['id']}")
    listed = await client.get("/api/v1/projects")

    assert fetched.status_code == 200
    assert fetched.json() == first.json()
    assert listed.status_code == 200
    assert [project["id"] for project in listed.json()] == [first.json()["id"], second.json()["id"]]


@pytest.mark.integration
@pytest.mark.anyio
async def test_rejects_client_workspace_root_and_unexpected_fields_with_422_envelope(
    project_client: tuple[httpx.AsyncClient, ProjectWorkspace]
) -> None:
    client, _ = project_client
    for payload in (
        {"slug": "forbidden-root", "title": "Forbidden", "workspace_root": "/tmp/nope"},
        {"slug": "unexpected", "title": "Unexpected", "surprise": True},
    ):
        response = await client.post("/api/v1/projects", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.integration
@pytest.mark.anyio
async def test_duplicate_slug_has_conflict_envelope_and_no_new_workspace(
    project_client: tuple[httpx.AsyncClient, ProjectWorkspace]
) -> None:
    client, workspace = project_client
    first = await client.post("/api/v1/projects", json={"slug": "duplicate", "title": "First"})
    duplicate = await client.post("/api/v1/projects", json={"slug": "duplicate", "title": "Second"})

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == {
        "code": "conflict",
        "message": "A project with this slug already exists.",
        "details": None,
    }
    assert [entry.name for entry in workspace.workspace_base_dir.iterdir()] == ["duplicate"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_missing_project_returns_not_found_envelope(
    project_client: tuple[httpx.AsyncClient, ProjectWorkspace]
) -> None:
    client, _ = project_client

    response = await client.get(f"/api/v1/projects/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.integration
@pytest.mark.anyio
async def test_precommit_failure_rolls_back_project_and_removes_new_workspace(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = ProjectWorkspace(tmp_path / "workspaces")
    service = ProjectService(async_session, workspace)

    async def fail_flush() -> None:
        raise RuntimeError("database write failed")

    monkeypatch.setattr(async_session, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="database write failed"):
        await service.create_project(slug="flush-failure", title="Flush failure")

    assert not workspace.root_for("flush-failure").exists()
    assert await async_session.scalar(select(Project.id).where(Project.slug == "flush-failure")) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_workspace_failure_after_allocation_is_compensated(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = ProjectWorkspace(tmp_path / "workspaces")
    service = ProjectService(async_session, workspace)
    original_create = workspace.create

    def create_then_fail(slug: str) -> Path:
        original_create(slug)
        raise OSError("workspace allocation failed")

    monkeypatch.setattr(workspace, "create", create_then_fail)
    with pytest.raises(OSError, match="workspace allocation failed"):
        await service.create_project(slug="workspace-failure", title="Workspace failure")

    assert not workspace.root_for("workspace-failure").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_unknown_commit_outcome_preserves_workspace_and_is_named(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = ProjectWorkspace(tmp_path / "workspaces")
    service = ProjectService(async_session, workspace)

    async def fail_commit() -> None:
        raise ConnectionError("commit acknowledgement lost")

    monkeypatch.setattr(async_session, "commit", fail_commit)
    with pytest.raises(ProjectCommitIndeterminateError):
        await service.create_project(slug="unknown-commit", title="Unknown commit")

    assert workspace.root_for("unknown-commit").is_dir()
