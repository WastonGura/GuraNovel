from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.main import create_app
from app.models import Chapter, Document, DocumentSource, DocumentType, DocumentVersion, Project, StudioRestorePoint
from app.services import DocumentService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def seed(session: AsyncSession, path: Path):
    project = Project(slug=f"studio-{uuid4()}", title="Novel", workspace_root=str(path))
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1, title="Chapter")
    session.add(chapter)
    await session.commit()
    document = await DocumentService(session).create_document(
        project_id=project.id, chapter_id=chapter.id, document_type=DocumentType.CHAPTER_DRAFT,
        title="Draft", path="draft.md", content="original", source=DocumentSource.USER,
    )
    chapter.current_draft_document_id = document.id
    await session.commit()
    return chapter, document, f"/api/v1/projects/{project.id}/chapters/{chapter.id}/restore-points"


async def test_manual_points_are_distinct_from_autosave_and_retries(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    _, document, url = await seed(async_session, tmp_path)
    version = document.current_version_id
    payload = {"request_id": str(uuid4()), "expected_current_version_id": str(version)}
    point = await client.post(url, json=payload)
    assert point.status_code == 200
    assert point.json()["version_id"] == str(version)
    assert (await client.post(url, json=payload)).json() == point.json()
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    saved = await DocumentService(async_session).write_document(
        document_id=document.id, content="auto saved", source=DocumentSource.USER,
        expected_current_version_id=version,
    )
    assert len((await client.get(url)).json()) == 1
    assert (await client.post(url, json=payload)).json() == point.json()
    assert (await client.post(url, json={**payload, "request_id": str(uuid4())})).status_code == 409
    assert (await client.post(url, json={**payload, "expected_current_version_id": str(saved.id)})).status_code == 409
    assert (await client.post(url, json={**payload, "source": "writer_agent"})).status_code == 422


async def test_restore_retries_and_conflicts_preserve_history_and_cross_document_points(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, source, url = await seed(async_session, tmp_path)
    point = (await client.post(url, json={"request_id": str(uuid4()),
                                        "expected_current_version_id": str(source.current_version_id)})).json()
    draft = await DocumentService(async_session).create_document(
        project_id=chapter.project_id, chapter_id=chapter.id, document_type=DocumentType.CHAPTER_DRAFT,
        title="New draft", path="new-draft.md", content="new document", source=DocumentSource.USER,
    )
    chapter.current_draft_document_id = draft.id
    await async_session.commit()
    endpoint = f"{url}/{point['id']}/restore"
    payload = {"request_id": str(uuid4()), "expected_current_version_id": str(draft.current_version_id)}
    response = await client.post(endpoint, json=payload)
    assert response.status_code == 200
    assert response.json()["document_id"] == str(draft.id)
    assert response.json()["parent_version_id"] == payload["expected_current_version_id"]
    assert (await DocumentService(async_session).read_version_content(source.id, source.current_version_id)) == "original"
    assert (await DocumentService(async_session).read_version_content(draft.id, draft.current_version_id)) == "new document"
    assert (await client.post(endpoint, json=payload)).json() == response.json()
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == 3
    assert (await client.post(endpoint, json={**payload, "request_id": str(uuid4())})).status_code == 409
    await async_session.refresh(draft)
    assert draft.current_version_id.hex == response.json()["id"].replace("-", "")
    assert (await DocumentService(async_session).read_current_content(draft.id)).content == "original"


async def test_restore_point_scope_and_readonly_are_enforced(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, url = await seed(async_session, tmp_path)
    payload = {"request_id": str(uuid4()), "expected_current_version_id": str(document.current_version_id)}
    point = (await client.post(url, json=payload)).json()
    wrong = url.replace(str(chapter.project_id), str(uuid4()))
    assert (await client.get(wrong)).status_code == 404
    assert (await client.post(wrong, json=payload)).status_code == 404
    assert (await client.post(f"{wrong}/{point['id']}/restore", json=payload)).status_code == 404
    chapter.final_document_id = document.id
    await async_session.commit()
    assert (await client.post(url, json={**payload, "request_id": str(uuid4())})).status_code == 409
    assert (await client.post(f"{url}/{point['id']}/restore", json=payload)).status_code == 409
    assert len((await client.get(url)).json()) == 1


async def test_concurrent_duplicate_requests_create_one_point_and_one_restored_version(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    import asyncio

    _, document, url = await seed(async_session, tmp_path)
    payload = {"request_id": str(uuid4()), "expected_current_version_id": str(document.current_version_id)}
    results = await asyncio.gather(*(client.post(url, json=payload) for _ in range(2)))
    assert [result.status_code for result in results] == [200, 200]
    assert results[0].json() == results[1].json()
    endpoint = f"{url}/{payload['request_id']}/restore"
    restore = {**payload, "request_id": str(uuid4())}
    results = await asyncio.gather(*(client.post(endpoint, json=restore) for _ in range(2)))
    assert [result.status_code for result in results] == [200, 200]
    assert results[0].json() == results[1].json()
    assert await async_session.scalar(select(func.count()).select_from(StudioRestorePoint)) == 2
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == 2
    assert await async_session.scalar(select(func.count()).select_from(Document)) == 1


async def test_migration_preserves_legacy_manual_points_and_all_document_versions(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
):
    import os
    import subprocess
    import sys

    chapter, document, _ = await seed(async_session, tmp_path)
    manual = await DocumentService(async_session).write_document(
        document_id=document.id, content="legacy manual point", source=DocumentSource.USER,
        expected_current_version_id=document.current_version_id, change_summary="手动存档",
    )
    await async_session.close()

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url,
                              "PGOPTIONS": "-c lock_timeout=5000 -c statement_timeout=15000"},
            check=True, capture_output=True, timeout=60,
        )

    try:
        migrate("downgrade", "0005_reader_panel_recovery")
    finally:
        migrate("upgrade", "head")
    point = await async_session.get(StudioRestorePoint, manual.id)
    assert point is not None and point.chapter_id == chapter.id
    assert point.document_id == document.id and point.version_id == manual.id
    assert point.feedback_snapshot is None
    assert await async_session.scalar(select(func.count()).select_from(StudioRestorePoint)) == 1
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == 2
