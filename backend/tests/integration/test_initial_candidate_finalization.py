from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agents.chapter_writer_agents import WriterAgent
from app.agents.chapter_writer_fakes import DeterministicChapterWriterProvider
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    User,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Started,
)
from app.services.document_service import DocumentService
from app.services.initial_candidate_finalization import InitialCandidateFinalizer
from app.services.initial_candidate_persistence import (
    InitialCandidateIdentity,
    InitialCandidatePersistence,
)
from app.services.initial_provider_handoff import InitialProviderHandoff


pytestmark = pytest.mark.integration


async def _approved(
    session: AsyncSession, workspace: Path
) -> tuple[Project, Chapter, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"finalizer-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"finalizer-{uuid4().hex}",
        title="Finalizer",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id,
        chapter_number=1,
        title="Finalizer",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()
    outline = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline",
        path=f"chapters/{chapter.id}-outline.md",
        content="# Arrival\n\nReach the gate.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    return project, chapter, owner


async def _candidate(
    engine: AsyncEngine, project: Project, chapter: Chapter, owner: User
) -> tuple[ChapterPhaseSessionLease, InitialCandidateIdentity]:
    lease = ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine))
    result = await InitialProviderHandoff(
        lease, WriterAgent(DeterministicChapterWriterProvider()), True
    ).execute(project.id, chapter.id, actor_user_id=owner.id)
    return lease, await InitialCandidatePersistence(lease, True).persist(result)


@pytest.mark.anyio
async def test_finalize_fresh_candidate_atomically_creates_exact_author_gate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        finalizer = InitialCandidateFinalizer(lease, True)

        started = await finalizer.finalize(identity, actor_user_id=owner.id)
        assert await finalizer.finalize(identity, actor_user_id=owner.id) == started

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, identity.workflow_run_id)
            live_chapter = await session.get(Chapter, chapter.id)
            actions = tuple(
                await session.scalars(
                    select(ActionRequest).where(
                        ActionRequest.workflow_run_id == identity.workflow_run_id
                    )
                )
            )
            checkpoints = tuple(
                await session.scalars(
                    select(WorkflowCheckpoint)
                    .where(WorkflowCheckpoint.workflow_run_id == identity.workflow_run_id)
                    .order_by(WorkflowCheckpoint.checkpoint_index)
                )
            )
        assert type(started) is ChapterProductionV2Started
        assert run is not None and run.metadata_["provider_attempt"] is None
        assert run.status == "AUTHOR_REVISION" and run.awaiting_user is True
        assert live_chapter is not None
        assert live_chapter.current_draft_document_id == identity.document_id
        assert len(actions) == 1 and actions[0].id == started.action_request_id
        assert tuple(item.checkpoint_index for item in checkpoints) == (0, 1)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_resume_and_reconcile_reconstruct_exact_author_gate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        finalizer = InitialCandidateFinalizer(lease, True)
        started = await finalizer.resume(
            project.id, chapter.id, identity.workflow_run_id, actor_user_id=owner.id
        )
        replayed = await finalizer.resume(
            project.id, chapter.id, identity.workflow_run_id, actor_user_id=owner.id
        )
        state = await finalizer.reconcile(
            project.id, chapter.id, identity.workflow_run_id, actor_user_id=owner.id
        )
        assert started is not None and replayed == started
        assert state.status.value == "AUTHOR_REVISION"
        assert state.document_version_id == str(identity.version_id)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_finalize_reloads_candidate_and_rejects_stale_identity(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            version = await session.get(DocumentVersion, identity.version_id)
            assert version is not None
            version.metadata_ = {**version.metadata_, "attempt_id": str(uuid4())}
            await session.commit()

        with pytest.raises(ChapterProductionV2ReconciliationError):
            await InitialCandidateFinalizer(lease, True).finalize(
                identity, actor_user_id=owner.id
            )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            action_count = await session.scalar(
                select(func.count()).select_from(ActionRequest)
            )
            checkpoints = tuple(
                await session.scalars(
                    select(WorkflowCheckpoint).where(
                        WorkflowCheckpoint.workflow_run_id == identity.workflow_run_id
                    )
                )
            )
        assert action_count == 0
        assert tuple(item.checkpoint_index for item in checkpoints) == (0,)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_finalize_rejects_duplicate_three_key_evidence(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            foreign = await DocumentService(session).create_document(
                project_id=project.id,
                chapter_id=chapter.id,
                document_type=DocumentType.CHAPTER_DRAFT,
                title="Foreign",
                path=f"chapters/foreign-{uuid4()}.md",
                content="# Foreign\n",
                source=DocumentSource.WRITER_AGENT,
                agent_role="writer_agent",
                change_summary="Foreign evidence.",
                version_metadata={
                    "contract_version": "chapter-production-v2",
                    "operation_key": identity.operation_key,
                    "attempt_id": str(identity.attempt_id),
                },
            )
            assert foreign.current_version is not None
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await InitialCandidateFinalizer(lease, True).finalize(
                identity, actor_user_id=owner.id
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_finalize_commit_ack_loss_replays_exact_gate(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        original = AsyncSession.commit

        async def commit_then_lose_ack(session: AsyncSession) -> None:
            await original(session)
            raise ConnectionError("PRIVATE finalizer commit acknowledgement")

        with monkeypatch.context() as commit_patch:
            commit_patch.setattr(AsyncSession, "commit", commit_then_lose_ack)
            with pytest.raises(ChapterProductionV2CommitIndeterminateError):
                await InitialCandidateFinalizer(lease, True).finalize(
                    identity, actor_user_id=owner.id
                )
        replayed = await InitialCandidateFinalizer(lease, True).finalize(
            identity, actor_user_id=owner.id
        )
        assert replayed.workflow_run_id == identity.workflow_run_id
        assert replayed.draft_version_id == identity.version_id
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_concurrent_finalize_creates_only_one_author_gate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        finalizer = InitialCandidateFinalizer(lease, True)
        left, right = await asyncio.gather(
            finalizer.finalize(identity, actor_user_id=owner.id),
            finalizer.finalize(identity, actor_user_id=owner.id),
        )
        assert left == right
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            action_count = await session.scalar(
                select(func.count())
                .select_from(ActionRequest)
                .where(ActionRequest.workflow_run_id == identity.workflow_run_id)
            )
        assert action_count == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("current_file", "extra_action"))
async def test_finalize_rejects_file_or_action_corruption(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            document = await session.get(Document, identity.document_id)
            assert document is not None
            if corruption == "current_file":
                (tmp_path / document.path).write_text("# Tampered\n", encoding="utf-8")
            else:
                session.add(
                    ActionRequest(
                        workflow_run_id=identity.workflow_run_id,
                        project_id=identity.project_id,
                        chapter_id=identity.chapter_id,
                        request_type="foreign_action",
                        status="pending",
                        prompt="Foreign",
                    )
                )
                await session.commit()
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await InitialCandidateFinalizer(lease, True).finalize(
                identity, actor_user_id=owner.id
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("missing", "approved", "wrong_revision"))
async def test_reconcile_rejects_corrupt_initial_author_action(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, identity = await _candidate(engine, project, chapter, owner)
        started = await InitialCandidateFinalizer(lease, True).finalize(
            identity, actor_user_id=owner.id
        )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            action = await session.get(ActionRequest, started.action_request_id)
            assert action is not None
            if corruption == "missing":
                await session.delete(action)
            else:
                action.status = (
                    ActionRequestStatus.APPROVED.value
                    if corruption == "approved"
                    else ActionRequestStatus.REVISED.value
                )
                action.user_decision = (
                    "accept" if corruption == "approved" else "request_revision"
                )
                action.resolved_by_id = owner.id
                action.resolved_at = datetime.now(UTC)
            await session.commit()
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await InitialCandidateFinalizer(lease, True).reconcile(
                project.id,
                chapter.id,
                identity.workflow_run_id,
                actor_user_id=owner.id,
            )
    finally:
        await engine.dispose()
