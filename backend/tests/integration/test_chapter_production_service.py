from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.errors import WorkflowStateError
from app.services.chapter_production_service import (
    ChapterProductionCommitIndeterminateError,
    ChapterProductionService,
)
from app.services.chapter_service import ChapterService
from app.services.document_service import DocumentService
from app.services.project_service import ProjectService
from app.workspace import ProjectWorkspace


async def create_project_and_chapter(async_session: AsyncSession, workspace_base: Path):
    project = await ProjectService(async_session, ProjectWorkspace(workspace_base)).create_project(
        slug=f"chapter-production-{workspace_base.name}",
        title="Archive of Ash",
    )
    chapter = await ChapterService(async_session).create_chapter(
        project_id=project.id, title="The Locked Door"
    )
    return project, chapter


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_production_persists_fake_artifacts_and_approval_gate(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "workspaces")

    result = await ChapterProductionService(async_session).start_production(project.id, chapter.id)

    run = await async_session.get(WorkflowRun, result.workflow_run_id)
    assert run is not None
    assert run.project_id == project.id
    assert run.chapter_id == chapter.id
    assert run.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value
    assert run.status == "awaiting_approval"
    assert run.current_node == "approval"
    assert run.next_node is None
    assert run.awaiting_user is True

    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert persisted_chapter is not None
    documents = list(
        await async_session.scalars(
            select(Document)
            .options(selectinload(Document.current_version))
            .where(Document.project_id == project.id)
            .order_by(Document.path)
        )
    )
    assert [(document.type, document.path, document.chapter_id) for document in documents] == [
        (DocumentType.CHAPTER_DRAFT.value, "chapters/chapter-0001-draft.md", chapter.id),
        (DocumentType.CHAPTER_OUTLINE_OPTIONS.value, "chapters/chapter-0001-outline.md", chapter.id),
    ]
    outline = next(document for document in documents if document.type == DocumentType.CHAPTER_OUTLINE_OPTIONS.value)
    draft = next(document for document in documents if document.type == DocumentType.CHAPTER_DRAFT.value)
    assert persisted_chapter.current_outline_document_id == outline.id
    assert persisted_chapter.current_draft_document_id == draft.id
    assert outline.current_version is not None
    assert draft.current_version is not None
    assert outline.current_version.source == DocumentSource.OUTLINE_AGENT.value
    assert outline.current_version.agent_role == "outline_agent"
    assert outline.current_version.workflow_run_id == run.id
    assert draft.current_version.source == DocumentSource.WRITER_AGENT.value
    assert draft.current_version.agent_role == "writer_agent"
    assert draft.current_version.workflow_run_id == run.id
    assert (Path(project.workspace_root) / outline.path).read_text() == (
        await DocumentService(async_session).read_current_content(outline.id)
    ).content
    assert (Path(project.workspace_root) / draft.path).read_text() == (
        await DocumentService(async_session).read_current_content(draft.id)
    ).content

    events = list(
        await async_session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run.id)
            .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
        )
    )
    assert [event.event_type for event in events] == [
        "production_started",
        "fake_output_stored",
        "awaiting_approval",
    ]
    action = await async_session.scalar(
        select(ActionRequest).where(ActionRequest.workflow_run_id == run.id)
    )
    assert action is not None
    assert action.project_id == project.id
    assert action.chapter_id == chapter.id
    assert action.request_type == "chapter_production_approval"
    assert action.status == ActionRequestStatus.PENDING.value
    assert action.options == ["approved", "rejected"]
    assert action.default_option == "approved"
    assert action.prompt == "Approve the generated chapter outline and draft?"


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_production_scopes_chapters_and_rejects_an_existing_awaiting_run(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "first")
    other_project, other_chapter = await create_project_and_chapter(
        async_session, tmp_path / "second"
    )
    service = ChapterProductionService(async_session)

    with pytest.raises(NotFoundError):
        await service.start_production(project.id, other_chapter.id)
    with pytest.raises(NotFoundError):
        await service.start_production(uuid4(), chapter.id)

    await service.start_production(project.id, chapter.id)
    with pytest.raises(ConflictError) as error:
        await service.start_production(project.id, chapter.id)

    assert error.value.message == "Chapter production is already awaiting approval."
    assert await async_session.scalar(
        select(WorkflowRun.id).where(
            WorkflowRun.project_id == other_project.id,
            WorkflowRun.chapter_id == other_chapter.id,
        )
    ) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_start_production_creates_one_durable_run_and_approval_gate(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "concurrent")
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def start_production() -> object:
        async with session_factory() as session:
            return await ChapterProductionService(session).start_production(project.id, chapter.id)

    try:
        outcomes = await asyncio.gather(*(start_production() for _ in range(4)), return_exceptions=True)

        successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 3
        assert all(isinstance(error, ConflictError) for error in failures)

        async with session_factory() as check_session:
            runs = list(
                await check_session.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.project_id == project.id,
                        WorkflowRun.chapter_id == chapter.id,
                        WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
                    )
                )
            )
            actions = list(
                await check_session.scalars(
                    select(ActionRequest).where(
                        ActionRequest.project_id == project.id,
                        ActionRequest.chapter_id == chapter.id,
                        ActionRequest.request_type == "chapter_production_approval",
                        ActionRequest.status == ActionRequestStatus.PENDING.value,
                    )
                )
            )

        assert len(runs) == 1
        assert len(actions) == 1
        assert actions[0].workflow_run_id == runs[0].id
        workspace = Path(project.workspace_root)
        assert (workspace / "chapters/chapter-0001-outline.md").exists()
        assert (workspace / "chapters/chapter-0001-draft.md").exists()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_resolve_action_approves_the_scoped_pending_gate_and_rejects_replay(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "approve")
    service = ChapterProductionService(async_session)
    started = await service.start_production(project.id, chapter.id)

    resolved = await service.resolve_action(
        project.id, chapter.id, started.workflow_run_id, started.action_id, "approved"
    )

    assert resolved.decision == "approved"
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    action = await async_session.get(ActionRequest, started.action_id)
    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert run is not None
    assert action is not None
    assert persisted_chapter is not None
    assert run.status == "completed"
    assert run.awaiting_user is False
    assert run.current_node == "approval"
    assert run.next_node is None
    assert action.status == ActionRequestStatus.APPROVED.value
    assert action.user_decision == "approved"
    assert persisted_chapter.status == "OUTLINE_APPROVED"
    assert list(
        await async_session.scalars(
            select(WorkflowEvent.event_type)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
            .order_by(WorkflowEvent.created_at)
        )
    ) == ["production_started", "fake_output_stored", "awaiting_approval", "approval_approved"]

    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            project.id, chapter.id, started.workflow_run_id, started.action_id, "approved"
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_resolve_action_rejects_without_changing_chapter_status_or_crossing_scope(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "reject")
    other_project, other_chapter = await create_project_and_chapter(
        async_session, tmp_path / "isolated"
    )
    service = ChapterProductionService(async_session)
    started = await service.start_production(project.id, chapter.id)
    other_started = await service.start_production(other_project.id, other_chapter.id)

    with pytest.raises(NotFoundError):
        await service.resolve_action(
            project.id,
            chapter.id,
            other_started.workflow_run_id,
            other_started.action_id,
            "rejected",
        )
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            project.id, chapter.id, started.workflow_run_id, started.action_id, "revise"
        )

    await service.resolve_action(
        project.id, chapter.id, started.workflow_run_id, started.action_id, "rejected"
    )

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    action = await async_session.get(ActionRequest, started.action_id)
    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert run is not None
    assert action is not None
    assert persisted_chapter is not None
    assert run.status == "rejected"
    assert run.awaiting_user is False
    assert run.current_node == "approval"
    assert run.next_node is None
    assert action.status == ActionRequestStatus.REJECTED.value
    assert action.user_decision == "rejected"
    assert persisted_chapter.status == "OUTLINE_DISCUSSION"


@pytest.mark.integration
@pytest.mark.anyio
async def test_post_document_workflow_commit_failure_preserves_artifacts_and_is_indeterminate(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "indeterminate")
    workspace = Path(project.workspace_root)
    original_commit = async_session.commit
    commits = 0

    async def fail_final_workflow_commit() -> None:
        nonlocal commits
        commits += 1
        if commits == 3:
            raise RuntimeError("final workflow commit failed")
        await original_commit()

    monkeypatch.setattr(async_session, "commit", fail_final_workflow_commit)

    with pytest.raises(ChapterProductionCommitIndeterminateError) as error:
        await ChapterProductionService(async_session).start_production(project.id, chapter.id)

    assert error.value.code == "chapter_production_commit_indeterminate"
    assert (workspace / "chapters/chapter-0001-outline.md").exists()
    assert (workspace / "chapters/chapter-0001-draft.md").exists()
    assert len(list((workspace / ".versions").rglob("*.md"))) == 2
