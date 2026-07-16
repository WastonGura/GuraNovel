import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db_session
from app.main import create_app
from app.models import Document, DocumentSource, Project
from app.services import DocumentService


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def create_project(async_session: AsyncSession, workspace_root: Path) -> Project:
    project = Project(
        slug=f"document-routes-{workspace_root.name}",
        title="Document Routes Test",
        workspace_root=str(workspace_root),
    )
    async_session.add(project)
    await async_session.commit()
    return project


async def create_document(
    client: httpx.AsyncClient, project: Project, content: str = "first draft"
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/documents",
        json={
            "project_id": str(project.id),
            "type": "chapter_draft",
            "title": "Chapter one",
            "path": "drafts/chapter-01.md",
            "content": content,
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_document_returns_stable_document_and_version_metadata(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)

    response = await client.post(
        "/api/v1/documents",
        json={
            "project_id": str(project.id),
            "type": "chapter_draft",
            "title": "Chapter one",
            "path": "drafts/chapter-01.md",
            "content": "# One\n\nHello world",
            "source": "user",
            "change_summary": "Initial draft",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["project_id"] == str(project.id)
    assert body["type"] == "chapter_draft"
    assert body["title"] == "Chapter one"
    assert body["path"] == "drafts/chapter-01.md"
    assert body["current_version"]["id"]
    assert body["current_version"]["document_id"] == body["id"]
    assert body["current_version"]["version_number"] == 1
    assert body["current_version"]["parent_version_id"] is None
    assert body["current_version"]["source"] == "user"
    assert body["current_version"]["change_summary"] == "Initial draft"
    assert (tmp_path / "drafts/chapter-01.md").read_text() == "# One\n\nHello world"


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_document_returns_document_and_current_version_metadata(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    created = await create_document(client, project)

    response = await client.get(f"/api/v1/documents/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.integration
@pytest.mark.anyio
async def test_reads_current_content_and_version_history(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    created = await create_document(client, project)
    document_id = str(created["id"])
    first_version_id = str(created["current_version"]["id"])  # type: ignore[index]

    current = await client.get(f"/api/v1/documents/{document_id}/content")
    versions = await client.get(f"/api/v1/documents/{document_id}/versions")
    historical = await client.get(
        f"/api/v1/documents/{document_id}/versions/{first_version_id}/content"
    )

    assert current.status_code == 200
    assert current.json() == {
        "document_id": document_id,
        "version_id": first_version_id,
        "content": "first draft",
    }
    assert versions.status_code == 200
    assert [version["id"] for version in versions.json()] == [first_version_id]
    assert historical.status_code == 200
    assert historical.json() == current.json()


@pytest.mark.integration
@pytest.mark.anyio
async def test_current_content_never_labels_a_concurrent_version_as_the_previous_one(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await create_project(async_session, tmp_path)
    created = await create_document(client, project, content="first draft")
    document_id = str(created["id"])
    first_version_id = str(created["current_version"]["id"])  # type: ignore[index]
    current_version_captured = asyncio.Event()
    resume_read = asyncio.Event()
    original_document = DocumentService._document

    async def pause_after_capturing_current_version(
        service: DocumentService, requested_document_id: UUID
    ) -> Document:
        document = await original_document(service, requested_document_id)
        if str(requested_document_id) == document_id:
            current_version_captured.set()
            await resume_read.wait()
        return document

    monkeypatch.setattr(DocumentService, "_document", pause_after_capturing_current_version)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        reading = asyncio.create_task(client.get(f"/api/v1/documents/{document_id}/content"))
        await asyncio.wait_for(current_version_captured.wait(), timeout=2)
        async with session_factory() as writer_session:
            written = await DocumentService(writer_session).write_document(
                document_id=UUID(document_id),
                content="second draft",
                source=DocumentSource.USER,
                expected_current_version_id=UUID(first_version_id),
            )
        resume_read.set()
        response = await reading
    finally:
        await engine.dispose()

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document_id,
        "version_id": first_version_id,
        "content": "first draft",
    }
    assert str(written.id) != first_version_id


@pytest.mark.integration
@pytest.mark.anyio
async def test_write_creates_a_version_and_rejects_stale_current_version(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    created = await create_document(client, project)
    document_id = str(created["id"])
    first_version_id = str(created["current_version"]["id"])  # type: ignore[index]

    written = await client.put(
        f"/api/v1/documents/{document_id}/content",
        json={
            "content": "second draft",
            "expected_current_version_id": first_version_id,
            "change_summary": "Expand draft",
        },
    )
    stale = await client.put(
        f"/api/v1/documents/{document_id}/content",
        json={
            "content": "stale overwrite",
            "expected_current_version_id": first_version_id,
        },
    )
    missing_expected = await client.put(
        f"/api/v1/documents/{document_id}/content", json={"content": "missing"}
    )

    assert written.status_code == 200
    assert written.json()["version_number"] == 2
    assert written.json()["parent_version_id"] == first_version_id
    assert written.json()["change_summary"] == "Expand draft"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "document_version_conflict"
    assert missing_expected.status_code == 422
    assert missing_expected.json()["error"]["code"] == "validation_error"


@pytest.mark.integration
@pytest.mark.anyio
async def test_restore_creates_a_new_version_from_historical_content(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    created = await create_document(client, project)
    document_id = str(created["id"])
    first_version_id = str(created["current_version"]["id"])  # type: ignore[index]
    written = await client.put(
        f"/api/v1/documents/{document_id}/content",
        json={"content": "second draft", "expected_current_version_id": first_version_id},
    )
    second_version_id = written.json()["id"]

    restored = await client.post(
        f"/api/v1/documents/{document_id}/versions/{first_version_id}/restore",
        json={
            "expected_current_version_id": second_version_id,
            "change_summary": "Restore first draft",
        },
    )
    current = await client.get(f"/api/v1/documents/{document_id}/content")
    missing_expected = await client.post(
        f"/api/v1/documents/{document_id}/versions/{first_version_id}/restore", json={}
    )

    assert restored.status_code == 200
    assert restored.json()["version_number"] == 3
    assert restored.json()["parent_version_id"] == second_version_id
    assert restored.json()["change_summary"] == "Restore first draft"
    assert current.json()["version_id"] == restored.json()["id"]
    assert current.json()["content"] == "first draft"
    assert missing_expected.status_code == 422
    assert missing_expected.json()["error"]["code"] == "validation_error"


@pytest.mark.integration
@pytest.mark.anyio
async def test_cross_document_version_content_and_restore_return_not_found_envelope(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    first = await create_document(client, project)
    second_response = await client.post(
        "/api/v1/documents",
        json={
            "project_id": str(project.id),
            "type": "chapter_draft",
            "title": "Chapter two",
            "path": "drafts/chapter-02.md",
            "content": "second document",
        },
    )
    assert second_response.status_code == 201
    second = second_response.json()
    document_id = str(first["id"])
    first_version_id = str(first["current_version"]["id"])
    other_version_id = str(second["current_version"]["id"])

    historical = await client.get(
        f"/api/v1/documents/{document_id}/versions/{other_version_id}/content"
    )
    restored = await client.post(
        f"/api/v1/documents/{document_id}/versions/{other_version_id}/restore",
        json={"expected_current_version_id": first_version_id},
    )

    expected_error = {
        "error": {
            "code": "not_found",
            "message": "Document version not found.",
            "details": None,
        }
    }
    assert historical.status_code == 404
    assert historical.json() == expected_error
    assert restored.status_code == 404
    assert restored.json() == expected_error
