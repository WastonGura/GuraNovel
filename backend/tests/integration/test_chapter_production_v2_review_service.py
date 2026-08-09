from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import (
    ChiefEditorChapterFinalAgent,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    EditorAgent,
    LoreChapterFinalAgent,
    WriterAgent,
)
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    ReviewReport,
    User,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.documents.chapter_segments import MAX_CHAPTER_CONTENT_BYTES
from app.services.chapter_production_v2_service import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Finalized,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ReviewProviderError,
    ChapterProductionV2Service,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentCommitIndeterminateError, DocumentService
from app.workflows.chapter_production import ChapterProductionStatus


class TransactionCheckingReviewProvider(DeterministicChapterReviewProvider):
    def __init__(self, session: AsyncSession, *, outcome: str = "passed") -> None:
        super().__init__(outcome=outcome)  # type: ignore[arg-type]
        self.session = session
        self.editor_calls = 0
        self.chief_calls = 0
        self.lore_calls = 0

    async def review_editor(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        self.editor_calls += 1
        return await super().review_editor(request, profile)  # type: ignore[arg-type]

    async def review_chief_final(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        self.chief_calls += 1
        return await super().review_chief_final(request, profile)  # type: ignore[arg-type]

    async def review_lore_final(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        self.lore_calls += 1
        return await super().review_lore_final(request, profile)  # type: ignore[arg-type]


class BarrierEditorReviewProvider(DeterministicChapterReviewProvider):
    def __init__(self) -> None:
        super().__init__(outcome="passed")
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def review_editor(self, request: object, profile: object) -> object:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().review_editor(request, profile)  # type: ignore[arg-type]


class FailOnceEditorReviewProvider(DeterministicChapterReviewProvider):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(outcome="passed")
        self.session = session
        self.calls = 0

    async def review_editor(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("private-review-provider /tmp/review-secret.md")
        return await super().review_editor(request, profile)  # type: ignore[arg-type]


async def review_ready_chapter(
    session: AsyncSession,
    workspace: Path,
    *,
    chief_editor_required: bool = True,
    editor_outcome: str = "passed",
    chief_outcome: str = "passed",
    lore_outcome: str = "passed",
) -> tuple[
    Project,
    Chapter,
    User,
    ChapterProductionV2Service,
    TransactionCheckingReviewProvider,
    TransactionCheckingReviewProvider,
    TransactionCheckingReviewProvider,
]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"review-owner-{uuid4().hex}", display_name="Review owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"chapter-review-{uuid4().hex}",
        title="Chapter review",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id,
        chapter_number=9,
        title="Review gate",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()

    outline = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Approved outline",
        path=f"chapters/{chapter.id}-approved-outline.md",
        content="# Arrival\n\nReach the gate.\n\n## Cost\n\nPay the known price.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
    )
    await DocumentService(session).create_document(
        project_id=project.id,
        document_type=DocumentType.STYLE_GUIDE,
        title="Style guide",
        path="style/style-guide.md",
        content="# Style\n\nUse precise, restrained prose.\n",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    await DocumentService(session).create_document(
        project_id=project.id,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="World overview",
        path="world/overview.md",
        content="# Boundary\n\nThe sealed gate always demands a known price.\n",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()

    editor_provider = TransactionCheckingReviewProvider(session, outcome=editor_outcome)
    chief_provider = TransactionCheckingReviewProvider(session, outcome=chief_outcome)
    lore_provider = TransactionCheckingReviewProvider(session, outcome=lore_outcome)
    service = ChapterProductionV2Service(
        session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
        editor_agent=EditorAgent(editor_provider),
        chief_editor_agent=ChiefEditorChapterFinalAgent(chief_provider),
        lore_agent=LoreChapterFinalAgent(lore_provider),
        chief_editor_required=chief_editor_required,
    )
    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    run = await session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    chapter.metadata_["test_run_id"] = str(run.id)
    await session.commit()
    return (
        project,
        chapter,
        owner,
        service,
        editor_provider,
        chief_provider,
        lore_provider,
    )


def run_id(chapter: Chapter):
    from uuid import UUID

    return UUID(chapter.metadata_["test_run_id"])


@pytest.mark.integration
@pytest.mark.anyio
async def test_clean_reviews_create_exact_ready_pair_and_finalize_without_panel(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, editor, chief, lore = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)

    editor_result = await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    chief_result = await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    ready_result = await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )

    assert editor_result.action_request_id is None
    assert chief_result.action_request_id is None
    assert ready_result.action_request_id is None
    assert (editor.editor_calls, chief.chief_calls, lore.lore_calls) == (1, 1, 1)
    state = await service.load_state(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.REVISION_READY
    reports = list(
        await async_session.scalars(
            select(ReviewReport)
            .where(ReviewReport.workflow_run_id == workflow_run_id)
            .order_by(ReviewReport.created_at)
        )
    )
    assert [report.review_mode for report in reports] == [
        "chapter_editor",
        "chapter_chief_final",
        "chapter_final_lore",
    ]
    assert all(
        set(report.raw_report)
        == {
            "claim_id",
            "contract_version",
            "operation_key",
            "request_hash",
            "segment_map_hash",
            "segmenter_version",
        }
        and report.target_version_id is not None
        for report in reports
    )
    assert "summary" not in str([report.raw_report for report in reports]).lower()
    ready_checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == workflow_run_id,
                WorkflowCheckpoint.node_name == "REVISION_READY",
            )
        )
    )
    ready_events = list(
        await async_session.scalars(
            select(WorkflowEvent).where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type == "revision_ready",
            )
        )
    )
    assert len(ready_checkpoints) == len(ready_events) == 1
    assert ready_events[0].payload == {
        "chapter_id": str(chapter.id),
        "checkpoint_id": str(ready_checkpoints[0].id),
        "checkpoint_index": ready_checkpoints[0].checkpoint_index,
        "document_id": state.document_id,
        "document_version_id": state.document_version_id,
        "content_hash": state.content_hash,
        "review_policy_version": "chapter-quality-v1",
        "status": "REVISION_READY",
    }

    finalized = await service.finalize_without_reader_panel(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    replayed = await service.finalize_without_reader_panel(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert isinstance(finalized, ChapterProductionV2Finalized)
    assert replayed == finalized
    persisted_chapter = await async_session.get(Chapter, chapter.id)
    final_document = await async_session.get(Document, finalized.final_document_id)
    final_version = await async_session.get(DocumentVersion, finalized.final_version_id)
    assert persisted_chapter is not None and persisted_chapter.final_document_id == final_document.id
    assert persisted_chapter.status == ChapterProductionStatus.COMPLETED.value
    assert final_document is not None and final_document.type == DocumentType.CHAPTER_FINAL.value
    assert final_version is not None and final_version.document_id == final_document.id
    assert (
        await async_session.scalar(
            select(func.count()).select_from(Document).where(
                Document.chapter_id == chapter.id,
                Document.type == DocumentType.CHAPTER_FINAL.value,
            )
        )
        == 1
    )

    public_events = list(
        await async_session.scalars(
            select(WorkflowEvent).where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type.in_(
                    ("chapter_review_recorded", "chapter_finalized")
                ),
            )
        )
    )
    review_events = [
        event for event in public_events if event.event_type == "chapter_review_recorded"
    ]
    final_events = [
        event for event in public_events if event.event_type == "chapter_finalized"
    ]
    assert len(review_events) == 3 and len(final_events) == 1
    assert all(
        set(event.payload)
        == {
            "chapter_id",
            "document_version_id",
            "finding_codes",
            "review_outcome",
            "review_report_id",
            "review_stage",
            "segment_map_hash",
            "status",
        }
        for event in review_events
    )
    assert final_events[0].payload == {
        "chapter_id": str(chapter.id),
        "document_version_id": state.document_version_id,
        "final_document_id": str(finalized.final_document_id),
        "final_version_id": str(finalized.final_version_id),
        "status": ChapterProductionStatus.COMPLETED.value,
    }
    assert all(
        "summary" not in event.payload
        and "content" not in event.payload
        and "path" not in event.payload
        and "message" not in event.payload
        for event in public_events
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_warning_requires_exact_user_decision_and_blocking_cannot_be_accepted(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, _, _, _ = await review_ready_chapter(
        async_session, tmp_path / "warning", editor_outcome="warning"
    )
    workflow_run_id = run_id(chapter)
    warned = await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    assert warned.action_request_id is not None
    action = await async_session.get(ActionRequest, warned.action_request_id)
    assert action is not None and action.options == ["accept_warning", "request_revision"]
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_review_action(
            project_id,
            chapter_id,
            workflow_run_id,
            uuid4(),
            actor_user_id=owner_id,
            decision="accept_warning",
        )
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_review_action(
            project_id,
            chapter_id,
            workflow_run_id,
            warned.action_request_id,
            actor_user_id=uuid4(),
            decision="accept_warning",
        )
    await service.resolve_review_action(
        project_id,
        chapter_id,
        workflow_run_id,
        warned.action_request_id,
        actor_user_id=owner_id,
        decision="accept_warning",
    )
    refreshed_action = await async_session.get(ActionRequest, warned.action_request_id)
    assert refreshed_action is not None
    assert refreshed_action.status == ActionRequestStatus.APPROVED.value
    resolved_event = await async_session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == workflow_run_id,
            WorkflowEvent.event_type == "chapter_review_action_resolved",
        )
    )
    assert resolved_event is not None
    assert resolved_event.payload == {
        "action_request_id": str(warned.action_request_id),
        "chapter_id": str(chapter_id),
        "decision": "accept_warning",
        "document_version_id": action.metadata_["document_version_id"],
        "status": ChapterProductionStatus.CHIEF_FINAL_REVIEW.value,
    }

    blocked_project, blocked_chapter, blocked_owner, blocked_service, *_ = (
        await review_ready_chapter(
            async_session,
            tmp_path / "blocking",
            editor_outcome="blocking",
        )
    )
    blocked_run_id = run_id(blocked_chapter)
    blocked = await blocked_service.execute_current_review(
        blocked_project.id,
        blocked_chapter.id,
        blocked_run_id,
        actor_user_id=blocked_owner.id,
    )
    blocked_project_id = blocked_project.id
    blocked_chapter_id = blocked_chapter.id
    blocked_owner_id = blocked_owner.id
    assert blocked.action_request_id is not None
    with pytest.raises(ChapterProductionV2ValidationError):
        await blocked_service.resolve_review_action(
            blocked_project_id,
            blocked_chapter_id,
            blocked_run_id,
            warned.action_request_id,
            actor_user_id=blocked_owner_id,
            decision="request_revision",
        )
    with pytest.raises(ChapterProductionV2ValidationError):
        await blocked_service.resolve_review_action(
            blocked_project_id,
            blocked_chapter_id,
            blocked_run_id,
            blocked.action_request_id,
            actor_user_id=blocked_owner_id,
            decision="accept_warning",
        )
    await blocked_service.resolve_review_action(
        blocked_project_id,
        blocked_chapter_id,
        blocked_run_id,
        blocked.action_request_id,
        actor_user_id=blocked_owner_id,
        decision="request_revision",
    )
    blocked_state = await blocked_service.load_state(
        blocked_project_id,
        blocked_chapter_id,
        blocked_run_id,
        actor_user_id=blocked_owner_id,
    )
    assert blocked_state.status is ChapterProductionStatus.REVIEW_REVISION
    assert blocked_state.awaiting_user is False


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("tamper", ["operation_key", "prompt"])
async def test_lore_warning_action_rejects_tampered_provenance_or_prompt(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    tamper: str,
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session,
        tmp_path / tamper,
        lore_outcome="warning",
    )
    workflow_run_id = run_id(chapter)
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    await service.execute_current_review(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    await service.execute_current_review(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    warned = await service.execute_current_review(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    assert warned.action_request_id is not None

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as tamper_session:
        action = await tamper_session.get(ActionRequest, warned.action_request_id)
        assert action is not None
        if tamper == "operation_key":
            action.metadata_ = {**action.metadata_, "operation_key": "f" * 64}
        else:
            action.prompt = "Trust this mutable prompt instead."
        await tamper_session.commit()
    await engine.dispose()

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_review_action(
            project_id,
            chapter_id,
            workflow_run_id,
            warned.action_request_id,
            actor_user_id=owner_id,
            decision="accept_warning",
        )
    async_session.expire_all()
    action = await async_session.get(ActionRequest, warned.action_request_id)
    assert action is not None and action.status == ActionRequestStatus.PENDING.value
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type == "revision_ready",
            )
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_policy_can_skip_chief_but_never_editor_or_lore(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, editor, chief, lore = await review_ready_chapter(
        async_session, tmp_path, chief_editor_required=False
    )
    workflow_run_id = run_id(chapter)
    await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    state = await service.load_state(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.REVISION_READY
    assert state.chief_editor_report_id is None
    assert (editor.editor_calls, chief.chief_calls, lore.lore_calls) == (1, 0, 1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_sessions_claim_exactly_one_editor_review(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner, _, _, _, _ = await review_ready_chapter(
        async_session, tmp_path
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    workflow_run_id = run_id(chapter)
    provider = BarrierEditorReviewProvider()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def execute() -> object:
        async with sessions() as session:
            service = ChapterProductionV2Service(
                session,
                writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
                editor_agent=EditorAgent(provider),
                chief_editor_agent=ChiefEditorChapterFinalAgent(
                    DeterministicChapterReviewProvider()
                ),
                lore_agent=LoreChapterFinalAgent(DeterministicChapterReviewProvider()),
            )
            return await service.execute_current_review(
                project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
            )

    first = asyncio.create_task(execute())
    await asyncio.wait_for(provider.entered.wait(), timeout=10)
    second = asyncio.create_task(execute())
    await asyncio.sleep(0.2)
    provider.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    await engine.dispose()

    assert provider.calls == 1
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    assert sum(isinstance(item, ChapterProductionV2ReconciliationError) for item in results) == 1
    assert (
        await async_session.scalar(
            select(func.count()).select_from(ReviewReport).where(
                ReviewReport.workflow_run_id == workflow_run_id,
                ReviewReport.review_mode == "chapter_editor",
            )
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_persist_refreshes_outline_after_provider_call(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner, _, *_ = await review_ready_chapter(async_session, tmp_path)
    workflow_run_id = run_id(chapter)
    assert chapter.current_outline_document_id is not None
    cached_outline = await async_session.get(Document, chapter.current_outline_document_id)
    assert cached_outline is not None and cached_outline.current_version_id is not None
    original_outline_version_id = cached_outline.current_version_id
    cached_outline_version = await async_session.get(
        DocumentVersion, original_outline_version_id
    )
    assert cached_outline_version is not None

    provider = BarrierEditorReviewProvider()
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
        editor_agent=EditorAgent(provider),
        chief_editor_agent=ChiefEditorChapterFinalAgent(
            DeterministicChapterReviewProvider()
        ),
        lore_agent=LoreChapterFinalAgent(DeterministicChapterReviewProvider()),
    )
    review_task = asyncio.create_task(
        service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    )
    await asyncio.wait_for(provider.entered.wait(), timeout=10)

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as foreign_session:
            replacement = await DocumentService(foreign_session).write_document(
                document_id=cached_outline.id,
                content="# Replaced outline\n\nTake a different route.\n",
                source=DocumentSource.USER,
                expected_current_version_id=original_outline_version_id,
                actor_user_id=owner.id,
                change_summary="Concurrent approved-outline replacement.",
            )
            replacement_id = replacement.id
        assert cached_outline.current_version_id == original_outline_version_id
        provider.release.set()
        with pytest.raises(ChapterProductionV2ValidationError):
            await review_task
        assert cached_outline in async_session.identity_map.values()
        await async_session.refresh(cached_outline)
        assert cached_outline.current_version_id == replacement_id
        assert (
            await async_session.scalar(
                select(func.count()).select_from(ReviewReport).where(
                    ReviewReport.workflow_run_id == workflow_run_id,
                    ReviewReport.review_mode == "chapter_editor",
                )
            )
            == 0
        )
    finally:
        provider.release.set()
        if not review_task.done():
            review_task.cancel()
            await asyncio.gather(review_task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["missing_event", "duplicate_checkpoint", "event_payload"])
async def test_revision_ready_pair_corruption_fails_closed(
    async_session: AsyncSession, tmp_path: Path, corruption: str
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path / corruption
    )
    workflow_run_id = run_id(chapter)
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    checkpoint = await async_session.scalar(
        select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == workflow_run_id,
            WorkflowCheckpoint.node_name == "REVISION_READY",
        )
    )
    event = await async_session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == workflow_run_id,
            WorkflowEvent.event_type == "revision_ready",
        )
    )
    assert checkpoint is not None and event is not None
    if corruption == "missing_event":
        await async_session.delete(event)
    elif corruption == "duplicate_checkpoint":
        async_session.add(
            WorkflowCheckpoint(
                workflow_run_id=workflow_run_id,
                checkpoint_index=checkpoint.checkpoint_index + 1,
                node_name=checkpoint.node_name,
                state_json=checkpoint.state_json,
            )
        )
    else:
        event.payload = {**event.payload, "content_hash": "0" * 64}
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.load_state(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_final_file_materialization_failure_remains_recoverable(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    original = service.documents.write_staged_files

    def fail_materialization(*_: object) -> None:
        raise DocumentCommitIndeterminateError()

    monkeypatch.setattr(service.documents, "write_staged_files", fail_materialization)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.finalize_without_reader_panel(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    state = await service.load_state(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    persisted_chapter = await async_session.get(Chapter, chapter_id)
    assert state.status is ChapterProductionStatus.ARCHIVE_UPDATE
    assert persisted_chapter is not None
    assert persisted_chapter.status == ChapterProductionStatus.ARCHIVE_UPDATE.value
    assert persisted_chapter.final_document_id is not None

    monkeypatch.setattr(service.documents, "write_staged_files", original)
    result = await service.finalize_without_reader_panel(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    assert result.final_document_id == persisted_chapter.final_document_id
    assert (
        await async_session.scalar(
            select(func.count()).select_from(Document).where(
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_FINAL.value,
            )
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_reviewer_failure_is_safe_retryable_and_does_not_duplicate_report(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _, _, _ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    provider = FailOnceEditorReviewProvider(async_session)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
        editor_agent=EditorAgent(provider),
        chief_editor_agent=ChiefEditorChapterFinalAgent(DeterministicChapterReviewProvider()),
        lore_agent=LoreChapterFinalAgent(DeterministicChapterReviewProvider()),
    )
    with pytest.raises(ChapterProductionV2ReviewProviderError) as raised:
        await service.execute_current_review(
            project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
        )
    assert str(raised.value) == "Chapter review failed safely."
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    failed = await service.load_state(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert failed.status is ChapterProductionStatus.FAILED

    await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert provider.calls == 2
    assert (
        await async_session.scalar(
            select(func.count()).select_from(ReviewReport).where(
                ReviewReport.workflow_run_id == workflow_run_id,
                ReviewReport.review_mode == "chapter_editor",
            )
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_operator_acknowledges_exact_claim_before_safe_retry(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    workflow_run_id = run_id(chapter)
    context = await service._claim_current_review(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=workflow_run_id,
        actor_user_id=owner_id,
    )
    claim = context.run.metadata_["reviewer_claim"]
    assert isinstance(claim, dict)
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.acknowledge_reviewer_no_write(
            project_id,
            chapter_id,
            workflow_run_id,
            actor_user_id=owner_id,
            expected_operation_key=claim["operation_key"],
            expected_claim_id=str(uuid4()),
        )
    await service.acknowledge_reviewer_no_write(
        project_id,
        chapter_id,
        workflow_run_id,
        actor_user_id=owner_id,
        expected_operation_key=claim["operation_key"],
        expected_claim_id=claim["claim_id"],
    )
    result = await service.execute_current_review(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    assert result.action_request_id is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_reviewer_claim_commit_indeterminate_never_calls_provider(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner, service, editor, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)

    async def indeterminate_commit() -> None:
        raise ChapterProductionV2CommitIndeterminateError()

    monkeypatch.setattr(service, "_commit", indeterminate_commit)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    assert editor.editor_calls == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_warning_and_ready_resume_across_fresh_sessions(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path, editor_outcome="warning"
    )
    workflow_run_id = run_id(chapter)
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    warned = await service.execute_current_review(
        project_id,
        chapter_id,
        workflow_run_id,
        actor_user_id=owner_id,
    )
    assert warned.action_request_id is not None

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def restarted_service(session: AsyncSession) -> ChapterProductionV2Service:
        return ChapterProductionV2Service(
            session,
            writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
            editor_agent=EditorAgent(DeterministicChapterReviewProvider()),
            chief_editor_agent=ChiefEditorChapterFinalAgent(
                DeterministicChapterReviewProvider()
            ),
            lore_agent=LoreChapterFinalAgent(DeterministicChapterReviewProvider()),
        )

    try:
        async with sessions() as restarted_session:
            restarted = restarted_service(restarted_session)
            loaded = await restarted.load_state(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=owner_id,
            )
            assert loaded.status is ChapterProductionStatus.EDITOR_REVIEW
            assert loaded.action_request_id == str(warned.action_request_id)
            await restarted.resolve_review_action(
                project_id,
                chapter_id,
                workflow_run_id,
                warned.action_request_id,
                actor_user_id=owner_id,
                decision="accept_warning",
            )
            await restarted.execute_current_review(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=owner_id,
            )
            await restarted.execute_current_review(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=owner_id,
            )
            ready = await restarted.load_state(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=owner_id,
            )
            assert ready.status is ChapterProductionStatus.REVISION_READY

        async with sessions() as replay_session:
            replayed = await restarted_service(replay_session).load_state(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=owner_id,
            )
            assert replayed == ready
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_same_hash_new_version_invalidates_reports_and_ready_capability(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    ready = await service.load_state(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    assert ready.document_id is not None and ready.document_version_id is not None
    content = await DocumentService(async_session).read_version_content(
        UUID(ready.document_id),
        UUID(ready.document_version_id),
    )
    newer = await DocumentService(async_session).write_document(
        document_id=UUID(ready.document_id),
        content=content,
        source=DocumentSource.USER,
        expected_current_version_id=UUID(ready.document_version_id),
        actor_user_id=owner_id,
    )
    assert newer.content_hash == ready.content_hash
    assert str(newer.id) != ready.document_version_id

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.load_state(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.finalize_without_reader_panel(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    assert (
        await async_session.scalar(
            select(func.count()).select_from(Document).where(
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_FINAL.value,
            )
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_sessions_finalize_one_final_document_and_version(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def finalize() -> ChapterProductionV2Finalized:
        async with sessions() as session:
            return await ChapterProductionV2Service(
                session,
                writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
            ).finalize_without_reader_panel(
                project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
            )

    first, second = await asyncio.gather(finalize(), finalize())
    await engine.dispose()
    assert first == second
    assert (
        await async_session.scalar(
            select(func.count()).select_from(Document).where(
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_FINAL.value,
            )
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.document_id == first.final_document_id
            )
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_archive_recovery_rejects_same_content_foreign_current_version(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    ready = await service.load_state(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    assert ready.document_id is not None and ready.document_version_id is not None
    content = await DocumentService(async_session).read_version_content(
        UUID(ready.document_id), UUID(ready.document_version_id)
    )
    original_write = service.documents.write_staged_files

    def fail_materialization(*_: object) -> None:
        raise DocumentCommitIndeterminateError()

    monkeypatch.setattr(service.documents, "write_staged_files", fail_materialization)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.finalize_without_reader_panel(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    final_document = await async_session.scalar(
        select(Document).where(
            Document.chapter_id == chapter_id,
            Document.type == DocumentType.CHAPTER_FINAL.value,
        )
    )
    assert final_document is not None and final_document.current_version_id is not None
    expected_system_version_id = final_document.current_version_id

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as foreign_session:
        foreign_version = await DocumentService(foreign_session).write_document(
            document_id=final_document.id,
            content=content,
            source=DocumentSource.USER,
            expected_current_version_id=expected_system_version_id,
            actor_user_id=owner_id,
            change_summary="Untrusted same-content replacement.",
        )
        assert foreign_version.content_hash == ready.content_hash
        assert foreign_version.source == DocumentSource.USER.value
        assert foreign_version.metadata_ == {}
    await engine.dispose()
    monkeypatch.setattr(service.documents, "write_staged_files", original_write)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.finalize_without_reader_panel(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )

    async_session.expire_all()
    persisted_chapter = await async_session.get(Chapter, chapter_id)
    persisted_run = await async_session.get(WorkflowRun, workflow_run_id)
    assert persisted_chapter is not None and persisted_run is not None
    assert persisted_chapter.status == ChapterProductionStatus.ARCHIVE_UPDATE.value
    assert persisted_run.status == ChapterProductionStatus.ARCHIVE_UPDATE.value
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type == "chapter_finalized",
            )
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_completed_replay_refreshes_same_content_foreign_current_version(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    finalized = await service.finalize_without_reader_panel(
        project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
    )
    content = await DocumentService(async_session).read_version_content(
        finalized.final_document_id, finalized.final_version_id
    )
    system_version = await async_session.get(DocumentVersion, finalized.final_version_id)
    cached_document = await async_session.get(Document, finalized.final_document_id)
    assert system_version is not None and cached_document is not None
    assert cached_document.current_version_id == finalized.final_version_id
    expected_content_hash = system_version.content_hash

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as foreign_session:
        foreign_version = await DocumentService(foreign_session).write_document(
            document_id=finalized.final_document_id,
            content=content,
            source=DocumentSource.USER,
            expected_current_version_id=finalized.final_version_id,
            actor_user_id=owner_id,
            change_summary="Untrusted completed replacement.",
        )
        assert foreign_version.content_hash == expected_content_hash
        assert foreign_version.source == DocumentSource.USER.value
        assert foreign_version.metadata_ == {}
        foreign_version_id = foreign_version.id
    await engine.dispose()

    assert cached_document in async_session.identity_map.values()
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.finalize_without_reader_panel(
            project_id, chapter_id, workflow_run_id, actor_user_id=owner_id
        )
    assert cached_document in async_session.identity_map.values()
    await async_session.refresh(cached_document)
    assert cached_document.current_version_id == foreign_version_id


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("target_type", ["outline", "context"])
@pytest.mark.parametrize("corruption", ["hash", "oversize", "non_regular"])
async def test_review_claim_rejects_corrupt_outline_and_context_snapshots(
    async_session: AsyncSession,
    tmp_path: Path,
    target_type: str,
    corruption: str,
) -> None:
    project, chapter, owner, service, editor, *_ = await review_ready_chapter(
        async_session, tmp_path / target_type / corruption
    )
    workflow_run_id = run_id(chapter)
    document_type = (
        DocumentType.CHAPTER_SELECTED_OUTLINE.value
        if target_type == "outline"
        else DocumentType.STYLE_GUIDE.value
    )
    document = await async_session.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.type == document_type,
        )
    )
    assert document is not None and document.current_version_id is not None
    version = await async_session.get(DocumentVersion, document.current_version_id)
    assert version is not None and version.snapshot_path is not None
    snapshot = Path(project.workspace_root) / version.snapshot_path
    if corruption == "hash":
        snapshot.write_bytes(b"x" * max(1, version.byte_size))
    elif corruption == "oversize":
        snapshot.write_bytes(b"x" * (MAX_CHAPTER_CONTENT_BYTES + 1))
    else:
        replacement = snapshot.with_name(f"{snapshot.name}.replacement")
        replacement.write_text("not the trusted snapshot\n", encoding="utf-8")
        snapshot.unlink()
        snapshot.symlink_to(replacement)

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    assert editor.editor_calls == 0
    run = await async_session.get(WorkflowRun, workflow_run_id)
    assert run is not None and run.metadata_["reviewer_claim"] is None
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(ReviewReport)
            .where(ReviewReport.workflow_run_id == workflow_run_id)
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_malformed_ready_marker_cannot_be_hidden_during_ready_creation(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, _, _, lore = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    for _ in range(2):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    marker = await async_session.scalar(
        select(WorkflowCheckpoint)
        .where(WorkflowCheckpoint.workflow_run_id == workflow_run_id)
        .order_by(WorkflowCheckpoint.checkpoint_index)
    )
    assert marker is not None
    marker.state_json = {"status": ChapterProductionStatus.REVISION_READY.value}
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    assert lore.lore_calls == 1
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type == "revision_ready",
            )
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_complete_historical_ready_pair_with_different_version_is_allowed(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    current = await service.load_state(
        project.id,
        chapter.id,
        workflow_run_id,
        actor_user_id=owner.id,
    )
    assert current.document_id is not None and current.document_version_id is not None
    content = await DocumentService(async_session).read_version_content(
        UUID(current.document_id), UUID(current.document_version_id)
    )
    historical_document = await DocumentService(async_session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Historical reviewed draft",
        path=f"chapters/{chapter.id}-historical-reviewed-draft.md",
        content=content,
        source=DocumentSource.SYSTEM,
        workflow_run_id=workflow_run_id,
    )
    historical_version = historical_document.current_version
    assert historical_version is not None
    historical_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=historical_document.id,
        version_id=historical_version.id,
    )
    historical_reports: list[ReviewReport] = []
    for mode, role in (
        ("chapter_editor", "editor_agent"),
        ("chapter_chief_final", "chief_editor_agent"),
        ("chapter_final_lore", "lore_agent"),
    ):
        historical_reports.append(
            ReviewReport(
                project_id=project.id,
                chapter_id=chapter.id,
                workflow_run_id=workflow_run_id,
                review_mode=mode,
                reviewer_agent_role=role,
                target_document_id=historical_document.id,
                target_version_id=historical_version.id,
                passed=True,
                summary=f"Historical passed {mode} review.",
                blocking_issues=[],
                warnings=[],
                notes=[],
                suggested_actions=[],
                raw_report={
                    "claim_id": str(uuid4()),
                    "contract_version": "chapter-production-v2",
                    "operation_key": uuid4().hex * 2,
                    "request_hash": uuid4().hex * 2,
                    "segment_map_hash": historical_map.map_hash,
                    "segmenter_version": historical_map.segmenter_version,
                },
            )
        )
    async_session.add_all(historical_reports)
    await async_session.flush()
    current_ready = await async_session.scalar(
        select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == workflow_run_id,
            WorkflowCheckpoint.node_name == ChapterProductionStatus.REVISION_READY.value,
        )
    )
    marker = await async_session.scalar(
        select(WorkflowCheckpoint)
        .where(
            WorkflowCheckpoint.workflow_run_id == workflow_run_id,
            WorkflowCheckpoint.checkpoint_index > 0,
            WorkflowCheckpoint.node_name != ChapterProductionStatus.REVISION_READY.value,
        )
        .order_by(WorkflowCheckpoint.checkpoint_index)
    )
    assert current_ready is not None and marker is not None
    historical_payload = {
        **current_ready.state_json,
        "document_id": str(historical_document.id),
        "document_version_id": str(historical_version.id),
        "content_hash": historical_version.content_hash,
        "editor_report_id": str(historical_reports[0].id),
        "chief_editor_report_id": str(historical_reports[1].id),
        "lore_report_id": str(historical_reports[2].id),
    }
    marker.node_name = ChapterProductionStatus.REVISION_READY.value
    marker.state_json = historical_payload
    async_session.add(
        WorkflowEvent(
            workflow_run_id=workflow_run_id,
            event_type="revision_ready",
            node_name=ChapterProductionStatus.REVISION_READY.value,
            payload={
                "chapter_id": str(chapter.id),
                "checkpoint_id": str(marker.id),
                "checkpoint_index": marker.checkpoint_index,
                "document_id": str(historical_document.id),
                "document_version_id": str(historical_version.id),
                "content_hash": historical_version.content_hash,
                "review_policy_version": "chapter-quality-v1",
                "status": ChapterProductionStatus.REVISION_READY.value,
            },
        )
    )
    await async_session.commit()

    loaded = await service.load_state(
        project.id,
        chapter.id,
        workflow_run_id,
        actor_user_id=owner.id,
    )
    assert loaded.document_version_id == current.document_version_id
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type == "revision_ready",
            )
        )
        == 2
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_final_recovery_rejects_foreign_snapshot_path_without_overwrite(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path
    )
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    original = service.documents.write_staged_files

    def fail_materialization(*_: object) -> None:
        raise DocumentCommitIndeterminateError()

    monkeypatch.setattr(service.documents, "write_staged_files", fail_materialization)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.finalize_without_reader_panel(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    final_document = await async_session.scalar(
        select(Document).where(
            Document.chapter_id == chapter.id,
            Document.type == DocumentType.CHAPTER_FINAL.value,
        )
    )
    style_document = await async_session.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.type == DocumentType.STYLE_GUIDE.value,
        )
    )
    assert final_document is not None and final_document.current_version_id is not None
    assert style_document is not None
    final_version = await async_session.get(
        DocumentVersion, final_document.current_version_id
    )
    assert final_version is not None
    style_path = Path(project.workspace_root) / style_document.path
    style_before = style_path.read_bytes()
    final_version.snapshot_path = style_document.path
    await async_session.commit()
    monkeypatch.setattr(service.documents, "write_staged_files", original)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.finalize_without_reader_panel(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    assert style_path.read_bytes() == style_before


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("artifact", ["current", "snapshot"])
@pytest.mark.parametrize("corruption", ["missing", "non_regular", "foreign_content"])
async def test_completed_replay_revalidates_bounded_final_artifact_bytes(
    async_session: AsyncSession,
    tmp_path: Path,
    artifact: str,
    corruption: str,
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path / artifact / corruption
    )
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    finalized = await service.finalize_without_reader_panel(
        project.id,
        chapter.id,
        workflow_run_id,
        actor_user_id=owner.id,
    )
    document = await async_session.get(Document, finalized.final_document_id)
    version = await async_session.get(DocumentVersion, finalized.final_version_id)
    assert document is not None and version is not None and version.snapshot_path is not None
    relative_path = document.path if artifact == "current" else version.snapshot_path
    artifact_path = Path(project.workspace_root) / relative_path
    if corruption == "missing":
        artifact_path.unlink()
    elif corruption == "non_regular":
        replacement = artifact_path.with_name(f"{artifact_path.name}.foreign")
        replacement.write_text("foreign final artifact\n", encoding="utf-8")
        artifact_path.unlink()
        artifact_path.symlink_to(replacement)
    else:
        artifact_path.write_bytes(b"x" * max(1, version.byte_size))

    with pytest.raises(ChapterProductionV2ReconciliationError) as raised:
        await service.finalize_without_reader_panel(
            project.id,
            chapter.id,
            workflow_run_id,
            actor_user_id=owner.id,
        )
    assert str(raised.value) == "Chapter production requires explicit reconciliation."
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert relative_path not in repr(raised.value)
    assert "foreign final artifact" not in repr(raised.value)
