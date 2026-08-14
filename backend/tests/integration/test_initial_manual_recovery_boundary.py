from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
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
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Started,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.services.document_service import DocumentService
from app.workflows.chapter_production import ChapterProductionStatus


pytestmark = pytest.mark.integration


class _CountingProvider(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


def _test_runtime(
    database_url: str,
) -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
    ChapterPhaseSessionSource,
]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False), ChapterPhaseSessionSource(
        engine
    )


def _service(
    session: AsyncSession,
    provider: _CountingProvider,
    source: ChapterPhaseSessionSource,
) -> ChapterProductionV2Service:
    return ChapterProductionV2Service(
        session,
        writer_agent=WriterAgent(provider),
        phase_session_source=source,
    )


async def _approved(
    session: AsyncSession, workspace: Path
) -> tuple[Project, Chapter, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"manual-recovery-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"manual-recovery-{uuid4().hex}",
        title="Manual recovery",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id,
        chapter_number=1,
        title="Recovery boundary",
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
        content="# Arrival\n\nReach the recovery gate.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    return project, chapter, owner


async def _start(
    sessions: async_sessionmaker[AsyncSession],
    source: ChapterPhaseSessionSource,
    project: Project,
    chapter: Chapter,
    owner: User,
    provider: _CountingProvider,
) -> ChapterProductionV2Started:
    async with sessions() as caller:
        started = await _service(caller, provider, source).start_from_approved_outline(
            project.id, chapter.id, actor_user_id=owner.id
        )
    provider.calls = 0
    return started


async def _pollute_resolved_manual_action(
    sessions: async_sessionmaker[AsyncSession],
    started: ChapterProductionV2Started,
    owner_id: UUID,
) -> None:
    async with sessions() as session:
        action = await session.get(ActionRequest, started.action_request_id)
        assert action is not None
        action.status = ActionRequestStatus.REVISED.value
        action.user_decision = "submit_manual_edit"
        action.user_feedback = None
        action.resolved_by_id = owner_id
        action.resolved_at = datetime.now(UTC)
        await session.commit()


async def _manual_commit_crash(
    sessions: async_sessionmaker[AsyncSession],
    source: ChapterPhaseSessionSource,
    project: Project,
    chapter: Chapter,
    owner: User,
    provider: _CountingProvider,
    started: ChapterProductionV2Started,
    monkeypatch: pytest.MonkeyPatch,
) -> DocumentVersion:
    async with sessions() as caller:
        crashing = _service(caller, provider, source)

        async def lose_finalize_ack(**_: object) -> object:
            raise ChapterProductionV2CommitIndeterminateError() from None

        with monkeypatch.context() as patch:
            patch.setattr(crashing, "_finalize_manual_edit", lose_finalize_ack)
            with pytest.raises(ChapterProductionV2CommitIndeterminateError):
                await crashing.submit_manual_edit(
                    project.id,
                    chapter.id,
                    started.workflow_run_id,
                    started.action_request_id,
                    actor_user_id=owner.id,
                    content="# Arrival\n\nThe exact user child survives the crash.\n",
                )
    async with sessions() as check:
        children = list(
            await check.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.parent_version_id == started.draft_version_id,
                    DocumentVersion.workflow_run_id == started.workflow_run_id,
                )
            )
        )
        assert len(children) == 1
        child = children[0]
        assert child.source == DocumentSource.USER.value
        assert child.actor_user_id == owner.id and child.agent_role is None
        return child


async def _foreign_version(
    sessions: async_sessionmaker[AsyncSession],
    project: Project,
    chapter: Chapter,
    owner: User,
) -> UUID:
    async with sessions() as session:
        document = await DocumentService(session).create_document(
            project_id=project.id,
            chapter_id=chapter.id,
            document_type=DocumentType.CHAPTER_DRAFT,
            title="Foreign draft",
            path=f"chapters/{uuid4()}-foreign-draft.md",
            content="# Foreign\n\nUnrelated durable evidence.\n",
            source=DocumentSource.USER,
            actor_user_id=owner.id,
            change_summary="Create foreign recovery evidence.",
        )
        assert document.current_version_id is not None
        return document.current_version_id


async def _control_snapshot(
    sessions: async_sessionmaker[AsyncSession], run_id: UUID
) -> tuple[Any, ...]:
    async with sessions() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        actions = list(
            await session.scalars(
                select(ActionRequest)
                .where(ActionRequest.workflow_run_id == run_id)
                .order_by(ActionRequest.created_at, ActionRequest.id)
            )
        )
        checkpoints = list(
            await session.scalars(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == run_id)
                .order_by(WorkflowCheckpoint.checkpoint_index)
            )
        )
        return (
            (
                run.status,
                run.current_node,
                run.next_node,
                run.awaiting_user,
                deepcopy(run.metadata_),
            ),
            tuple(
                (
                    action.id,
                    action.status,
                    action.user_decision,
                    action.user_feedback,
                    action.resolved_by_id,
                    action.resolved_at,
                    deepcopy(action.metadata_),
                )
                for action in actions
            ),
            tuple(
                (
                    checkpoint.id,
                    checkpoint.checkpoint_index,
                    checkpoint.node_name,
                    deepcopy(checkpoint.state_json),
                )
                for checkpoint in checkpoints
            ),
        )


async def _assert_reconcile_fails_without_control_write(
    sessions: async_sessionmaker[AsyncSession],
    source: ChapterPhaseSessionSource,
    project: Project,
    chapter: Chapter,
    owner: User,
    provider: _CountingProvider,
    started: ChapterProductionV2Started,
) -> None:
    before = await _control_snapshot(sessions, started.workflow_run_id)
    async with sessions() as caller:
        with pytest.raises(ChapterProductionV2ReconciliationError) as raised:
            await _service(caller, provider, source).reconcile_indeterminate(
                project.id,
                chapter.id,
                started.workflow_run_id,
                actor_user_id=owner.id,
            )
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert await _control_snapshot(sessions, started.workflow_run_id) == before
    assert provider.calls == 0


@pytest.mark.anyio
async def test_resolved_manual_action_without_user_child_fails_closed(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine, sessions, source = _test_runtime(integration_database_url)
    provider = _CountingProvider()
    try:
        started = await _start(sessions, source, project, chapter, owner, provider)
        await _pollute_resolved_manual_action(sessions, started, owner.id)
        async with sessions() as check:
            children = list(
                await check.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.parent_version_id == started.draft_version_id,
                        DocumentVersion.workflow_run_id == started.workflow_run_id,
                    )
                )
            )
            assert children == []
        await _assert_reconcile_fails_without_control_write(
            sessions, source, project, chapter, owner, provider, started
        )
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "corruption",
    ("duplicate", "foreign_actor", "foreign_document", "wrong_operation_key"),
)
async def test_resolved_manual_action_rejects_ambiguous_or_foreign_user_child(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path / corruption)
    engine, sessions, source = _test_runtime(integration_database_url)
    provider = _CountingProvider()
    try:
        started = await _start(sessions, source, project, chapter, owner, provider)
        child = await _manual_commit_crash(
            sessions, source, project, chapter, owner, provider, started, monkeypatch
        )
        foreign_version_id = (
            await _foreign_version(sessions, project, chapter, owner)
            if corruption == "foreign_document"
            else None
        )
        async with sessions() as mutation:
            if corruption == "foreign_actor":
                stranger = User(username=f"foreign-{uuid4().hex}", display_name="Foreign")
                mutation.add(stranger)
                await mutation.flush()
                live_child = await mutation.get(DocumentVersion, child.id)
                assert live_child is not None
                live_child.actor_user_id = stranger.id
                await mutation.commit()
            elif corruption == "foreign_document":
                live_child = await mutation.get(DocumentVersion, foreign_version_id)
                assert live_child is not None
                live_child.parent_version_id = started.draft_version_id
                await mutation.commit()
            elif corruption == "wrong_operation_key":
                live_child = await mutation.get(DocumentVersion, child.id)
                assert live_child is not None
                live_child.metadata_ = {
                    **live_child.metadata_,
                    "operation_key": "0" * 64,
                }
                await mutation.commit()
            else:
                document = await mutation.get(Document, started.draft_document_id)
                assert document is not None
                document.current_version_id = started.draft_version_id
                await mutation.commit()
        if corruption == "duplicate":
            async with sessions() as mutation:
                duplicate = await DocumentService(mutation).write_document(
                    document_id=started.draft_document_id,
                    content="# Arrival\n\nA second exact sibling is ambiguous.\n",
                    source=DocumentSource.USER,
                    expected_current_version_id=started.draft_version_id,
                    actor_user_id=owner.id,
                    workflow_run_id=started.workflow_run_id,
                    change_summary="Duplicate manual recovery evidence.",
                    version_metadata=deepcopy(child.metadata_),
                )
                assert duplicate.parent_version_id == started.draft_version_id
        await _assert_reconcile_fails_without_control_write(
            sessions, source, project, chapter, owner, provider, started
        )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_exact_manual_commit_crash_preserves_public_entrypoint_parity(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine, sessions, source = _test_runtime(integration_database_url)
    provider = _CountingProvider()
    try:
        started = await _start(sessions, source, project, chapter, owner, provider)
        child = await _manual_commit_crash(
            sessions, source, project, chapter, owner, provider, started, monkeypatch
        )
        before = await _control_snapshot(sessions, started.workflow_run_id)
        for entrypoint in ("start", "resume"):
            async with sessions() as caller:
                service = _service(caller, provider, source)
                with pytest.raises(ChapterProductionV2ValidationError) as raised:
                    if entrypoint == "start":
                        await service.start_from_approved_outline(
                            project.id, chapter.id, actor_user_id=owner.id
                        )
                    else:
                        await service.resume_drafting(
                            project.id,
                            chapter.id,
                            started.workflow_run_id,
                            actor_user_id=owner.id,
                        )
            assert raised.value.__cause__ is None and raised.value.__context__ is None
            assert await _control_snapshot(sessions, started.workflow_run_id) == before
        async with sessions() as caller:
            recovered = await _service(caller, provider, source).reconcile_indeterminate(
                project.id,
                chapter.id,
                started.workflow_run_id,
                actor_user_id=owner.id,
            )
        assert recovered.status is ChapterProductionStatus.EDITOR_REVIEW
        assert recovered.document_version_id == str(child.id)
        assert provider.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_exact_manual_child_with_extra_pending_action_fails_closed(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine, sessions, source = _test_runtime(integration_database_url)
    provider = _CountingProvider()
    try:
        started = await _start(sessions, source, project, chapter, owner, provider)
        await _manual_commit_crash(
            sessions, source, project, chapter, owner, provider, started, monkeypatch
        )
        async with sessions() as mutation:
            mutation.add(
                ActionRequest(
                    workflow_run_id=started.workflow_run_id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    request_type="foreign_pending",
                    status=ActionRequestStatus.PENDING.value,
                    prompt="Foreign pending gate.",
                )
            )
            await mutation.commit()
        await _assert_reconcile_fails_without_control_write(
            sessions, source, project, chapter, owner, provider, started
        )
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "late_mutation", ("pending_action", "sibling_version", "foreign_sibling")
)
async def test_manual_route_revalidates_cardinality_in_legacy_phase(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    late_mutation: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine, sessions, source = _test_runtime(integration_database_url)
    provider = _CountingProvider()
    snapshots: list[tuple[Any, ...]] = []
    try:
        started = await _start(sessions, source, project, chapter, owner, provider)
        child = await _manual_commit_crash(
            sessions, source, project, chapter, owner, provider, started, monkeypatch
        )
        foreign_version_id = (
            await _foreign_version(sessions, project, chapter, owner)
            if late_mutation == "foreign_sibling"
            else None
        )
        async with sessions() as caller:
            service = _service(caller, provider, source)
            original = service._resolved_source_action

            async def insert_after_preflight(
                *args: object, **kwargs: object
            ) -> ActionRequest:
                result = await original(*args, **kwargs)  # type: ignore[arg-type]
                async with sessions() as mutation:
                    if late_mutation == "pending_action":
                        mutation.add(
                            ActionRequest(
                                workflow_run_id=started.workflow_run_id,
                                project_id=project.id,
                                chapter_id=chapter.id,
                                request_type="late_pending",
                                status=ActionRequestStatus.PENDING.value,
                                prompt="Inserted after initial routing.",
                            )
                        )
                    elif late_mutation == "sibling_version":
                        mutation.add(
                            DocumentVersion(
                                document_id=child.document_id,
                                version_number=child.version_number + 1,
                                parent_version_id=started.draft_version_id,
                                source=child.source,
                                actor_user_id=child.actor_user_id,
                                agent_role=child.agent_role,
                                workflow_run_id=child.workflow_run_id,
                                content_hash=child.content_hash,
                                byte_size=child.byte_size,
                                word_count=child.word_count,
                                file_path=child.file_path,
                                snapshot_path=f"{child.snapshot_path}.late",
                                change_summary="Late ambiguous sibling.",
                                metadata_=deepcopy(child.metadata_),
                            )
                        )
                    else:
                        foreign = await mutation.get(
                            DocumentVersion, foreign_version_id
                        )
                        assert foreign is not None
                        foreign.parent_version_id = started.draft_version_id
                    await mutation.commit()
                snapshots.append(
                    await _control_snapshot(sessions, started.workflow_run_id)
                )
                return result

            monkeypatch.setattr(service, "_resolved_source_action", insert_after_preflight)
            with pytest.raises(ChapterProductionV2ReconciliationError) as raised:
                await service.reconcile_indeterminate(
                    project.id,
                    chapter.id,
                    started.workflow_run_id,
                    actor_user_id=owner.id,
                )
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        assert len(snapshots) == 1
        assert await _control_snapshot(sessions, started.workflow_run_id) == snapshots[0]
        assert provider.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("pollution", ("provider_attempt", "reviewer_claim"))
async def test_manual_recovery_rejects_incompatible_durable_claims(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pollution: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path / pollution)
    engine, sessions, source = _test_runtime(integration_database_url)
    provider = _CountingProvider()
    try:
        started = await _start(sessions, source, project, chapter, owner, provider)
        child = await _manual_commit_crash(
            sessions, source, project, chapter, owner, provider, started, monkeypatch
        )
        async with sessions() as mutation:
            run = await mutation.get(WorkflowRun, started.workflow_run_id)
            assert run is not None
            metadata = deepcopy(run.metadata_)
            if pollution == "provider_attempt":
                metadata[pollution] = {
                    "attempt_id": str(uuid4()),
                    "key": "a" * 64,
                    "kind": "feedback",
                    "checkpoint_index": 1,
                    "source_document_id": str(started.draft_document_id),
                    "source_version_id": str(started.draft_version_id),
                    "action_request_id": str(started.action_request_id),
                    "target_segment_ids": [str(uuid4())],
                    "feedback_hash": "b" * 64,
                    "report_ids": [],
                    "report_input_hash": None,
                    "status": "claimed",
                }
            else:
                metadata[pollution] = {
                    "claim_id": str(uuid4()),
                    "operation_key": "a" * 64,
                    "stage": "editor",
                    "checkpoint_index": 1,
                    "document_id": str(child.document_id),
                    "document_version_id": str(child.id),
                    "content_hash": child.content_hash,
                    "review_policy_version": "chapter-quality-v1",
                    "segment_map_hash": "b" * 64,
                    "request_hash": "c" * 64,
                    "status": "claimed",
                }
            run.metadata_ = metadata
            await mutation.commit()
        await _assert_reconcile_fails_without_control_write(
            sessions, source, project, chapter, owner, provider, started
        )
    finally:
        await engine.dispose()
