"""Integration tests for Chapter Production V2 HTTP routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    ChapterProductionV2Composition,
    get_chapter_production_v2_composition,
    get_db_session,
)
from app.main import create_app
from app.models import Chapter, Document, DocumentSource, DocumentType, DocumentVersion, Project, User
from app.agents import (
    ChiefEditorChapterFinalAgent,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    EditorAgent,
    LoreChapterFinalAgent,
    RevisionAgent,
    WriterAgent,
)
from app.services.document_service import DocumentService
from app.workflows.chapter_production import ChapterProductionStatus


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _test_composition() -> ChapterProductionV2Composition:
    writer_provider = DeterministicChapterWriterProvider()
    review_provider = DeterministicChapterReviewProvider()
    return ChapterProductionV2Composition(
        writer_agent=WriterAgent(writer_provider),
        revision_agent=RevisionAgent(writer_provider),
        editor_agent=EditorAgent(review_provider),
        chief_editor_agent=ChiefEditorChapterFinalAgent(review_provider),
        lore_agent=LoreChapterFinalAgent(review_provider),
        chief_editor_required=False,
    )


@pytest.fixture
async def v2_client(
    async_session: AsyncSession,
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()
    session_factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_chapter_production_v2_composition] = _test_composition

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _seed_approved_chapter(
    session: AsyncSession, workspace_root: Path
) -> tuple[Project, Chapter, Document, DocumentVersion, User]:
    workspace_root.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"owner-{uuid4().hex[:8]}", display_name="Owner")
    session.add(owner)
    await session.flush()

    project = Project(
        slug=f"proj-v2-routes-{uuid4().hex[:8]}",
        title="Project V2 Routes",
        workspace_root=str(workspace_root),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()

    chapter = Chapter(
        project_id=project.id,
        chapter_number=1,
        title="Chapter 1",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()

    outline_doc = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Chapter 1 Outline",
        path=f"chapters/{chapter.id}-selected-outline.md",
        content="## Chapter 1: The First Dawn\n\nOutline content describing key plot points.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved chapter outline.",
        actor_user_id=owner.id,
    )
    chapter.current_outline_document_id = outline_doc.id
    await session.commit()
    assert outline_doc.current_version is not None

    return project, chapter, outline_doc, outline_doc.current_version, owner


def _routes_url(project_id: UUID, chapter_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2"


async def test_full_chapter_production_v2_lifecycle_via_http(
    v2_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, chapter, outline_doc, outline_ver, owner = await _seed_approved_chapter(
        async_session, tmp_path / "v2_e2e"
    )
    base_url = _routes_url(project.id, chapter.id)

    # 1. Start production from approved outline
    start_resp = await v2_client.post(base_url)
    assert start_resp.status_code == 201, start_resp.text
    started_data = start_resp.json()
    run_id = UUID(started_data["workflow_run_id"])
    author_action_id = UUID(started_data["action_request_id"])
    draft_doc_id = UUID(started_data["draft_document_id"])
    draft_ver_id = UUID(started_data["draft_version_id"])

    assert UUID(started_data["outline_document_id"]) == outline_doc.id
    assert UUID(started_data["outline_version_id"]) == outline_ver.id

    # 2. List runs
    list_resp = await v2_client.get(base_url)
    assert list_resp.status_code == 200, list_resp.text
    runs = list_resp.json()
    assert len(runs) >= 1
    assert any(r["workflow_run_id"] == str(run_id) for r in runs)

    # 3. Get run state
    get_resp = await v2_client.get(f"{base_url}/{run_id}")
    assert get_resp.status_code == 200, get_resp.text
    state_data = get_resp.json()
    assert state_data["chapter_workflow_run_id"] == str(run_id)
    assert state_data["status"] == ChapterProductionStatus.AUTHOR_REVISION.value
    assert state_data["awaiting_user"] is True
    assert state_data["document_id"] == str(draft_doc_id)
    assert state_data["document_version_id"] == str(draft_ver_id)

    # 4. Resolve author action (accept)
    resolve_resp = await v2_client.post(
        f"{base_url}/{run_id}/actions/{author_action_id}/resolve",
        json={"decision": "accept"},
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    updated_data = resolve_resp.json()
    assert updated_data["workflow_run_id"] == str(run_id)

    # Verify state transitioned to editor review
    get_resp = await v2_client.get(f"{base_url}/{run_id}")
    assert get_resp.status_code == 200
    state_data = get_resp.json()
    assert state_data["status"] == ChapterProductionStatus.EDITOR_REVIEW.value

    # 5. Trigger review
    review_resp = await v2_client.post(f"{base_url}/{run_id}/review")
    assert review_resp.status_code == 200, review_resp.text
    review_updated = review_resp.json()
    review_action_id = review_updated.get("action_request_id")

    # 6. Check review state
    get_resp = await v2_client.get(f"{base_url}/{run_id}")
    assert get_resp.status_code == 200
    state_data = get_resp.json()
    if review_action_id is not None:
        assert state_data["status"] == ChapterProductionStatus.EDITOR_REVIEW.value
        # Proceed with warnings
        proceed_resp = await v2_client.post(
            f"{base_url}/{run_id}/actions/{review_action_id}/resolve",
            json={"decision": "proceed_with_warnings"},
        )
        assert proceed_resp.status_code == 200, proceed_resp.text
    else:
        assert state_data["status"] == ChapterProductionStatus.REVISION_READY.value

    # 7. Finalize without reader panel
    finalize_resp = await v2_client.post(f"{base_url}/{run_id}/finalize")
    assert finalize_resp.status_code == 200, finalize_resp.text
    finalized_data = finalize_resp.json()
    assert finalized_data["workflow_run_id"] == str(run_id)
    assert finalized_data["final_document_id"] is not None
    assert finalized_data["final_version_id"] is not None

    # 8. Check final state (completed)
    get_resp = await v2_client.get(f"{base_url}/{run_id}")
    assert get_resp.status_code == 200
    state_data = get_resp.json()
    assert state_data["status"] == ChapterProductionStatus.COMPLETED.value


async def test_extra_forbidden_and_cross_scope_isolation_via_http(
    v2_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, chapter, _, _, _ = await _seed_approved_chapter(
        async_session, tmp_path / "v2_isolation"
    )
    base_url = _routes_url(project.id, chapter.id)

    # 1. Extra field forbidden on start
    bad_start = await v2_client.post(base_url, json={"unexpected_field": "hacked"})
    assert bad_start.status_code == 422, bad_start.text
    assert bad_start.json()["error"]["code"] == "validation_error"

    # 2. Cross-project access returns 404
    foreign_proj_id = uuid4()
    foreign_url = _routes_url(foreign_proj_id, chapter.id)
    not_found_resp = await v2_client.get(foreign_url)
    assert not_found_resp.status_code == 404
    assert not_found_resp.json()["error"]["code"] == "not_found"
