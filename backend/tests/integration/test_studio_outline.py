import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chapter, DocumentSource, DocumentType, DocumentVersion, Project, WorkflowRun
from app.services import DocumentService
from app.services.chapter_production_repository import (
    ChapterProductionRepository, _ChapterProductionRepositoryValidationError,
)
from app.services.provider_attempt_contracts import CONTRACT_VERSION
from tests.integration import test_studio_restore_points as restore_points

client = restore_points.client

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def seed(session: AsyncSession, path: Path, content="# Scene\n\nReturn to the food shop."):
    project = Project(slug=f"outline-{uuid4()}", title="Novel", workspace_root=str(path))
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1)
    session.add(chapter)
    await session.commit()
    document = await DocumentService(session).create_document(
        project_id=project.id, chapter_id=chapter.id, document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline", path="outline.md", content=content, source=DocumentSource.USER,
    )
    chapter.current_outline_document_id = document.id
    await session.commit()
    url = f"/api/v1/projects/{project.id}/chapters/{chapter.id}/outline/approve"
    payload = dict(document_id=str(document.id), expected_current_version_id=str(document.current_version_id))
    return chapter, document, url, payload


async def test_approval_is_version_bound_retryable_and_does_not_create_a_draft(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, url, payload = await seed(async_session, tmp_path)
    responses = await asyncio.gather(client.post(url, json=payload), client.post(url, json=payload))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    await async_session.refresh(chapter)
    assert chapter.status == "OUTLINE_APPROVED"
    assert chapter.approved_outline_version_id == document.current_version_id
    assert chapter.current_draft_document_id is None
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    repository = ChapterProductionRepository(async_session, contract_version=CONTRACT_VERSION,
                                             inactive_run_statuses={"COMPLETED", "CANCELLED"})
    _, _, version = await repository.approved_outline(chapter.project_id, chapter.id, lock=False)
    assert version.id == document.current_version_id
    await async_session.rollback()
    # A later edit must not inherit the user's earlier confirmation.
    edited = await DocumentService(async_session).write_document(
        document_id=UUID(payload["document_id"]), content="# Scene\n\nA different plan.",
        source=DocumentSource.USER, expected_current_version_id=UUID(payload["expected_current_version_id"]),
    )
    edited_id = edited.id
    await async_session.refresh(chapter)
    with pytest.raises(_ChapterProductionRepositoryValidationError):
        await repository.approved_outline(chapter.project_id, chapter.id, lock=False)
    await async_session.rollback()
    assert (await client.post(url, json=payload)).status_code == 409
    assert (await client.post(url, json=payload | {"expected_current_version_id": str(edited_id)})).status_code == 200


async def test_approval_rejects_wrong_scope_version_and_untrusted_fields(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, _, url, payload = await seed(async_session, tmp_path)
    assert (await client.post(url.replace(str(chapter.project_id), str(uuid4())), json=payload)).status_code == 404
    assert (await client.post(url, json=payload | {"document_id": str(uuid4())})).status_code == 409
    assert (await client.post(url, json=payload | {"expected_current_version_id": str(uuid4())})).status_code == 409
    assert (await client.post(url, json=payload | {"document_id": str(UUID(int=0))})).status_code == 422
    assert (await client.post(url, json=payload | {"status": "OUTLINE_APPROVED"})).status_code == 422
    await async_session.refresh(chapter)
    assert chapter.status == "OUTLINE_DISCUSSION" and chapter.approved_outline_version_id is None


async def test_approval_rejects_empty_outline_and_pending_workflow(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, url, payload = await seed(async_session, tmp_path, content=" ")
    assert (await client.post(url, json=payload)).status_code == 409
    edited = await DocumentService(async_session).write_document(
        document_id=document.id, content="A valid plan.", source=DocumentSource.USER,
        expected_current_version_id=document.current_version_id,
    )
    payload["expected_current_version_id"] = str(edited.id)
    run = WorkflowRun(project_id=chapter.project_id, chapter_id=chapter.id,
                      workflow_type="chapter_production", status="AUTHOR_REVISION", awaiting_user=True)
    async_session.add(run)
    await async_session.commit()
    assert (await client.post(url, json=payload)).status_code == 409
    run.status = "COMPLETED"
    await async_session.commit()
    assert (await client.post(url, json=payload)).status_code == 200
    # Retrying confirmation must not rewind a chapter that has since progressed.
    await async_session.refresh(chapter)
    chapter.status = "REVIEWING"
    chapter.current_draft_document_id = uuid4()
    await async_session.commit()
    assert (await client.post(url, json=payload)).status_code == 200
    await async_session.refresh(chapter)
    assert chapter.status == "REVIEWING"
