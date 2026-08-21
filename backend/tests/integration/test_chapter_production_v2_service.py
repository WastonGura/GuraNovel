from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.graph.chapter_production_execution as graph_execution
from app.graph import chapter_production_topology

from app.agents import (
    DeterministicChapterWriterProvider,
    RevisionAgent,
    ReviewDrivenRevisionRequest,
    UserFeedbackRevisionRequest,
    WriterAgent,
)
from app.core.errors import NotFoundError
from app.llm import ProviderTimeoutError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    ReviewMode,
    ReviewReport,
    User,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_production_v2_service import (
    ChapterProductionV2ProviderError,
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Service,
    ChapterProductionV2Started,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_runtime import chapter_production_langgraph_pin
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.document_service import DocumentService
from app.services.initial_candidate_persistence import InitialCandidatePersistence
from app.services.initial_provider_handoff import InitialProviderHandoff
from app.services.initial_run_bootstrap import InitialRunBootstrap
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterProductionStatus,
    ChapterReviewBinding,
    ChapterReviewOutcome,
    ChapterReviewStage,
)


class TransactionCheckingProvider(DeterministicChapterWriterProvider):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        self.calls += 1
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class CallerStateCheckingProvider(DeterministicChapterWriterProvider):
    def __init__(self, session: AsyncSession, pending: User) -> None:
        self.session = session
        self.pending = pending
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is True
        assert self.pending in self.session.new
        self.calls += 1
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


def phase_source(session: AsyncSession) -> ChapterPhaseSessionSource:
    bind = session.bind
    assert bind is not None
    return ChapterPhaseSessionSource(bind)


class UnsafeFailingProvider(DeterministicChapterWriterProvider):
    async def draft_initial(self, request: object, profile: object) -> object:
        raise RuntimeError("private-provider-output /tmp/secret-draft.md")

    async def revise_from_user_feedback(self, request: object, profile: object) -> object:
        raise RuntimeError("private-provider-output /tmp/secret-feedback.md")

    async def revise_from_review(self, request: object, profile: object) -> object:
        raise RuntimeError("private-provider-output /tmp/secret-review.md")


def assert_safe_provider_error(error: ChapterProductionV2ProviderError) -> None:
    assert str(error) == "Chapter drafting failed safely."
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "private-provider-output" not in repr(error)
    assert "/tmp/secret" not in repr(error)


class FailOnceProvider(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        if self.calls == 1:
            raise ProviderTimeoutError()
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class FailingFeedbackProvider(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def revise_from_user_feedback(self, request: object, profile: object) -> object:
        self.calls += 1
        raise ProviderTimeoutError()


class FailingReviewProvider(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def revise_from_review(self, request: object, profile: object) -> object:
        self.calls += 1
        raise ProviderTimeoutError()


class BarrierProvider(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class OutcomeBarrierProvider(BarrierProvider):
    def __init__(self, outcome: str) -> None:
        super().__init__()
        self.outcome = outcome

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.outcome == "fail":
            raise ProviderTimeoutError()
        if self.outcome == "cancel":
            raise asyncio.CancelledError("canary-old-attempt")
        return await DeterministicChapterWriterProvider.draft_initial(
            self,
            request,
            profile,  # type: ignore[arg-type]
        )


class BarrierRevisionProvider(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def revise_from_user_feedback(self, request: object, profile: object) -> object:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().revise_from_user_feedback(request, profile)  # type: ignore[arg-type]

    async def revise_from_review(self, request: object, profile: object) -> object:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().revise_from_review(request, profile)  # type: ignore[arg-type]


class CancellingProvider(DeterministicChapterWriterProvider):
    async def draft_initial(self, request: object, profile: object) -> object:
        raise asyncio.CancelledError("canary-initial-provider-secret")

    async def revise_from_user_feedback(self, request: object, profile: object) -> object:
        raise asyncio.CancelledError("canary-feedback-provider-secret")

    async def revise_from_review(self, request: object, profile: object) -> object:
        raise asyncio.CancelledError("canary-review-provider-secret")


class OwnershipTransferringProvider(DeterministicChapterWriterProvider):
    def __init__(self, session: AsyncSession, replacement_owner_id: UUID) -> None:
        self.session = session
        self.replacement_owner_id = replacement_owner_id

    async def draft_initial(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        project = await self.session.get(Project, getattr(request, "project_id"))
        assert project is not None
        project.owner_id = self.replacement_owner_id
        await self.session.commit()
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class TransactionCheckingRevisionProvider(DeterministicChapterWriterProvider):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.calls = 0
        self.request: UserFeedbackRevisionRequest | None = None
        self.review_calls = 0
        self.review_request: ReviewDrivenRevisionRequest | None = None

    async def revise_from_user_feedback(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        assert isinstance(request, UserFeedbackRevisionRequest)
        self.calls += 1
        self.request = request
        return await super().revise_from_user_feedback(request, profile)  # type: ignore[arg-type]

    async def revise_from_review(self, request: object, profile: object) -> object:
        assert self.session.in_transaction() is False
        assert isinstance(request, ReviewDrivenRevisionRequest)
        self.review_calls += 1
        self.review_request = request
        return await super().revise_from_review(request, profile)  # type: ignore[arg-type]


async def approved_chapter(
    session: AsyncSession, workspace: Path
) -> tuple[Project, Chapter, Document, DocumentVersion]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"owner-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"chapter-v2-{uuid4().hex}",
        title="Chapter V2",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id,
        chapter_number=7,
        title="The Gate",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()
    outline = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Approved outline",
        path=f"chapters/{chapter.id}-selected-outline.md",
        content="# Arrival\n\nReach the sealed gate.\n\n## Warning\n\nHear the cost of entry.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved chapter outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    assert outline.current_version is not None
    return project, chapter, outline, outline.current_version


async def owned_started_chapter(
    session: AsyncSession, workspace: Path
) -> tuple[
    Project,
    Chapter,
    User,
    ChapterProductionV2Service,
    ChapterProductionV2Started,
    TransactionCheckingRevisionProvider,
]:
    project, chapter, _, _ = await approved_chapter(session, workspace)
    assert project.owner_id is not None
    owner = await session.get(User, project.owner_id)
    assert owner is not None
    owner.id = UUID(str(owner.id))
    await session.commit()
    provider = TransactionCheckingRevisionProvider(session)
    service = ChapterProductionV2Service(
        session,
        writer_agent=WriterAgent(TransactionCheckingProvider(session)),
        revision_agent=RevisionAgent(provider),
        phase_session_source=phase_source(session),
    )
    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    return project, chapter, owner, service, started, provider


async def seeded_review_revision(
    session: AsyncSession, workspace: Path
) -> tuple[
    Project,
    Chapter,
    User,
    ChapterProductionV2Service,
    ChapterProductionV2Started,
    TransactionCheckingRevisionProvider,
    ReviewReport,
]:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        session, workspace
    )
    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    state = await service.load_state(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
    )
    run = await session.get(WorkflowRun, started.workflow_run_id)
    document = await session.get(Document, started.draft_document_id)
    version = await session.get(DocumentVersion, started.draft_version_id)
    latest = await session.scalar(
        select(WorkflowCheckpoint)
        .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
        .order_by(WorkflowCheckpoint.checkpoint_index.desc())
    )
    assert run is not None and document is not None and version is not None and latest is not None
    segment_map = await DocumentService(session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=document.id,
        version_id=version.id,
    )
    evidence_segment_id = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    suggested_action = "Address the blocking review finding in a new candidate."
    report = ReviewReport(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_run_id=run.id,
        review_mode=ReviewMode.CHAPTER_EDITOR.value,
        reviewer_agent_role="editor_agent",
        target_document_id=document.id,
        target_version_id=version.id,
        passed=False,
        summary="The consequence needs a concrete cost.",
        blocking_issues=[
            {
                "sequence": 1,
                "code": "seeded_blocking_finding",
                "severity": "blocking",
                "required": True,
                "evidence_segment_ids": [str(evidence_segment_id)],
                "rationale": "The consequence needs a concrete cost.",
                "suggested_action": suggested_action,
                "segmenter_version": segment_map.segmenter_version,
                "segment_map_hash": segment_map.map_hash,
            }
        ],
        warnings=[],
        notes=[],
        suggested_actions=[suggested_action],
        raw_report={
            "claim_id": str(uuid4()),
            "contract_version": "chapter-production-v2",
            "operation_key": "b" * 64,
            "request_hash": "d" * 64,
            "segment_map_hash": segment_map.map_hash,
            "segmenter_version": segment_map.segmenter_version,
        },
    )
    action = ActionRequest(
        workflow_run_id=run.id,
        project_id=project.id,
        chapter_id=chapter.id,
        request_type="chapter_review_revision",
        status=ActionRequestStatus.PENDING.value,
        prompt="Resolve the blocking review.",
        options=["request_revision"],
        default_option="request_revision",
        metadata_={
            "contract_version": "chapter-production-v2",
            "action_kind": "review_revision",
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "content_hash": version.content_hash,
            "operation_key": "b" * 64,
        },
    )
    session.add_all((report, action))
    await session.flush()
    action.metadata_ = {
        **action.metadata_,
        "review_report_id": str(report.id),
        "review_stage": ChapterReviewStage.EDITOR.value,
    }
    review_binding = ChapterReviewBinding(
        report_id=str(report.id),
        stage=ChapterReviewStage.EDITOR,
        workflow_run_id=str(run.id),
        chapter_id=str(chapter.id),
        document_id=str(document.id),
        document_version_id=str(version.id),
        review_mode=report.review_mode,
        reviewer_agent_role=report.reviewer_agent_role,
        passed=False,
    )
    action_binding = ChapterActionBinding(
        action_request_id=str(action.id),
        workflow_run_id=str(run.id),
        chapter_id=str(chapter.id),
        request_type=action.request_type,
        kind=ChapterActionKind.REVIEW_REVISION,
        status=ActionRequestStatus.PENDING,
        pending_count=1,
        document_id=str(document.id),
        document_version_id=str(version.id),
        content_hash=version.content_hash,
        current_document_id=str(document.id),
        current_document_version_id=str(version.id),
        current_content_hash=version.content_hash,
    )
    blocked = state.record_review(
        outcome=ChapterReviewOutcome.BLOCKING,
        review=review_binding,
        action=action_binding,
    )
    service._append_state(run, latest, blocked)
    await session.commit()
    latest = await session.scalar(
        select(WorkflowCheckpoint)
        .where(WorkflowCheckpoint.workflow_run_id == run.id)
        .order_by(WorkflowCheckpoint.checkpoint_index.desc())
    )
    assert latest is not None
    resolved = blocked.resolve_action(
        action=action_binding,
        decision=ChapterActionDecision.REQUEST_REVISION,
    )
    action.status = ActionRequestStatus.REVISED.value
    action.user_decision = ChapterActionDecision.REQUEST_REVISION.value
    action.resolved_by_id = owner.id
    action.resolved_at = datetime.now(UTC)
    service._append_state(run, latest, resolved)
    await session.commit()
    return project, chapter, owner, service, started, provider, report


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_start_requires_owned_phase_source_before_caller_side_effects(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    provider = TransactionCheckingProvider(async_session)
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await ChapterProductionV2Service(
            async_session, writer_agent=WriterAgent(provider)
        ).start_from_approved_outline(
            project.id, chapter.id, actor_user_id=project.owner_id
        )

    assert type(raised.value) is ChapterProductionV2ValidationError
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert provider.calls == 0
    assert async_session.in_transaction() is True and pending in async_session.new


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("entrypoint", ("resume", "reconcile", "ack"))
async def test_initial_owned_phase_entrypoints_require_source_before_side_effects(
    async_session: AsyncSession, tmp_path: Path, entrypoint: str
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    provider = TransactionCheckingProvider(async_session)
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)
    service = ChapterProductionV2Service(async_session, writer_agent=WriterAgent(provider))
    run_id = uuid4()

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        if entrypoint == "resume":
            await service.resume_drafting(
                project.id, chapter.id, run_id, actor_user_id=project.owner_id
            )
        elif entrypoint == "reconcile":
            await service.reconcile_indeterminate(
                project.id, chapter.id, run_id, actor_user_id=project.owner_id
            )
        else:
            await service.acknowledge_provider_no_write(
                project.id,
                chapter.id,
                run_id,
                actor_user_id=project.owner_id,
                expected_attempt_key="a" * 64,
                expected_attempt_id=str(uuid4()),
            )

    assert type(raised.value) is ChapterProductionV2ValidationError
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert provider.calls == 0
    assert async_session.in_transaction() is True and pending in async_session.new


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_start_keeps_caller_transaction_and_pending_objects_untouched(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)
    provider = CallerStateCheckingProvider(async_session, pending)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=phase_source(async_session),
    )

    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=project.owner_id
    )

    assert started.workflow_run_id.int != 0 and provider.calls == 1
    assert async_session.in_transaction() is True and pending in async_session.new


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_reconcile_rejects_pristine_run_without_candidate(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    source = phase_source(async_session)
    run_id = await InitialRunBootstrap(
        ChapterPhaseSessionLease(source), True
    ).start_or_resume(project.id, chapter.id, actor_user_id=project.owner_id)
    provider = TransactionCheckingProvider(async_session)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=source,
    )

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.reconcile_indeterminate(
            project.id, chapter.id, run_id, actor_user_id=project.owner_id
        )
    assert provider.calls == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_from_approved_outline_persists_exact_v2_draft_gate_and_replays(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, outline, outline_version = await approved_chapter(async_session, tmp_path)
    provider = TransactionCheckingProvider(async_session)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=phase_source(async_session),
    )

    # #113's public evidence boundary remains draft/final-only; V2 gets a narrow helper.
    with pytest.raises(NotFoundError):
        await DocumentService(async_session).derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=outline.id,
            version_id=outline_version.id,
        )
    production_map = await DocumentService(async_session).derive_chapter_production_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=outline.id,
        version_id=outline_version.id,
    )
    assert production_map.content_hash == outline_version.content_hash
    await async_session.commit()

    assert project.owner_id is not None
    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=project.owner_id
    )
    replayed = await service.resume_drafting(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=project.owner_id,
    )

    assert replayed == started
    assert provider.calls == 1
    await async_session.refresh(chapter)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    draft = await async_session.get(Document, started.draft_document_id)
    version = await async_session.get(DocumentVersion, started.draft_version_id)
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert run is not None and draft is not None and version is not None and action is not None
    assert run.status == ChapterProductionStatus.AUTHOR_REVISION.value
    assert run.current_node == "author_revision" and run.awaiting_user is True
    assert run.metadata_ == {
        "contract_version": "chapter-production-v2",
        "review_policy_version": "chapter-quality-v1",
        "chief_editor_required": True,
        "outline_document_id": str(outline.id),
        "outline_version_id": str(outline_version.id),
        "outline_content_hash": outline_version.content_hash,
        "segmenter_version": "markdown-v1",
        "operation_key": run.metadata_["operation_key"],
        "provider_attempt": None,
        "reviewer_claim": None,
        "chapter_production_runtime": {
            "scheduler_kind": "service_v2",
            "graph_id": "chapter-production-v2",
            "graph_version": "0",
        },
    }
    assert draft.project_id == project.id and draft.chapter_id == chapter.id
    assert draft.type == DocumentType.CHAPTER_DRAFT.value
    assert draft.current_version_id == version.id
    assert version.parent_version_id is None
    assert version.source == DocumentSource.WRITER_AGENT.value
    assert version.agent_role == "writer_agent"
    assert version.workflow_run_id == run.id
    assert version.metadata_["operation_key"] == run.metadata_["operation_key"]
    assert set(version.metadata_) == {"attempt_id", "contract_version", "operation_key"}
    assert UUID(version.metadata_["attempt_id"]).int != 0
    assert action.status == ActionRequestStatus.PENDING.value
    assert action.metadata_ == {
        "contract_version": "chapter-production-v2",
        "action_kind": "author_revision",
        "document_id": str(draft.id),
        "document_version_id": str(version.id),
        "content_hash": version.content_hash,
        "operation_key": run.metadata_["operation_key"],
    }
    assert action.user_feedback is None
    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert persisted_chapter is not None
    assert persisted_chapter.current_draft_document_id == draft.id
    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index)
        )
    )
    assert [item.checkpoint_index for item in checkpoints] == [0, 1]
    assert checkpoints[-1].state_json["status"] == ChapterProductionStatus.AUTHOR_REVISION.value
    assert "Arrival" not in str(checkpoints)
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.status == ActionRequestStatus.PENDING.value,
            )
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_langgraph_start_and_flag_off_restart_preserve_exact_draft_gate(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, outline, outline_version = await approved_chapter(
        async_session, tmp_path
    )
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)
    provider = CallerStateCheckingProvider(async_session, pending)
    source = phase_source(async_session)
    cursors: list[str] = []
    real_advance = graph_execution.advance_chapter_production

    async def observed_advance(*args: object, **kwargs: object) -> object:
        cursors.append(kwargs["cursor"])  # type: ignore[arg-type]
        return await real_advance(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(graph_execution, "advance_chapter_production", observed_advance)
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=source,
    )
    assert project.owner_id is not None

    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=project.owner_id
    )

    assert cursors == ["reconstruct", "draft"]
    assert provider.calls == 1
    assert pending in async_session.new
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    restarted = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=source,
    )
    replayed = await restarted.resume_drafting(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=project.owner_id,
    )

    assert replayed == started
    assert cursors == ["reconstruct", "draft", "reconstruct"]
    assert provider.calls == 1
    assert pending in async_session.new
    async with ChapterPhaseSessionLease(source).lease() as probe:
        assert await probe.scalar(
            select(func.count()).select_from(User).where(User.username == pending.username)
        ) == 0
        run = await probe.get(WorkflowRun, started.workflow_run_id)
        persisted_chapter = await probe.get(Chapter, chapter.id)
        draft = await probe.get(Document, started.draft_document_id)
        version = await probe.get(DocumentVersion, started.draft_version_id)
        action = await probe.get(ActionRequest, started.action_request_id)
        checkpoints = list(
            await probe.scalars(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
                .order_by(WorkflowCheckpoint.checkpoint_index)
            )
        )
        assert run is not None and run.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()
        assert (run.status, run.current_node, run.awaiting_user) == (
            ChapterProductionStatus.AUTHOR_REVISION.value,
            "author_revision",
            True,
        )
        assert persisted_chapter is not None and persisted_chapter.current_draft_document_id == started.draft_document_id
        assert draft is not None and draft.current_version_id == started.draft_version_id
        assert version is not None and version.workflow_run_id == started.workflow_run_id
        assert action is not None and action.status == ActionRequestStatus.PENDING.value
        assert [item.checkpoint_index for item in checkpoints] == [0, 1]
        assert checkpoints[-1].state_json["action_request_id"] == str(action.id)
        assert "Arrival" not in str(checkpoints)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("cancelled", (False, True))
async def test_langgraph_node_failure_never_finishes_caller_pending_row(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)

    class AbortProvider(DeterministicChapterWriterProvider):
        async def draft_initial(self, request: object, profile: object) -> object:
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError("canary")

    source = phase_source(async_session)
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(AbortProvider()),
        phase_session_source=source,
    )
    assert project.owner_id is not None

    expected = asyncio.CancelledError if cancelled else ChapterProductionV2ProviderError
    with pytest.raises(expected):
        await service.start_from_approved_outline(
            project.id, chapter.id, actor_user_id=project.owner_id
        )

    assert pending in async_session.new
    async with ChapterPhaseSessionLease(source).lease() as probe:
        assert await probe.scalar(
            select(func.count()).select_from(User).where(User.username == pending.username)
        ) == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_rejects_foreign_outline_and_provider_failure_is_safe(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    foreign = Document(
        project_id=project.id,
        chapter_id=None,
        type=DocumentType.CHAPTER_SELECTED_OUTLINE.value,
        path=f"foreign/{uuid4()}.md",
    )
    async_session.add(foreign)
    await async_session.flush()
    chapter.current_outline_document_id = foreign.id
    await async_session.commit()
    with pytest.raises(ChapterProductionV2ValidationError):
        await ChapterProductionV2Service(
            async_session,
            writer_agent=WriterAgent(UnsafeFailingProvider()),
            phase_session_source=phase_source(async_session),
        ).start_from_approved_outline(project.id, chapter.id, actor_user_id=project.owner_id)

    # Restore the exact approved outline and prove arbitrary provider details never escape.
    safe_project, _, outline, _ = await approved_chapter(async_session, tmp_path / "safe")
    safe_chapter = outline.chapter_id
    assert safe_chapter is not None and safe_project.owner_id is not None
    with pytest.raises(ChapterProductionV2ProviderError) as raised:
        await ChapterProductionV2Service(
            async_session,
            writer_agent=WriterAgent(UnsafeFailingProvider()),
            phase_session_source=phase_source(async_session),
        ).start_from_approved_outline(
            outline.project_id,
            safe_chapter,
            actor_user_id=safe_project.owner_id,
        )
    assert_safe_provider_error(raised.value)
    failed_run = await async_session.scalar(
        select(WorkflowRun).where(WorkflowRun.chapter_id == safe_chapter)
    )
    assert failed_run is not None
    assert failed_run.status == ChapterProductionStatus.FAILED.value
    assert failed_run.awaiting_user is False
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.workflow_run_id == failed_run.id)
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_accept_author_action_enters_editor_review_and_cannot_replay(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )

    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert run is not None and action is not None
    assert run.status == ChapterProductionStatus.EDITOR_REVIEW.value
    assert run.current_node == "editor_review" and run.awaiting_user is False
    assert action.status == ActionRequestStatus.APPROVED.value
    assert action.user_decision == "accept" and action.resolved_by_id == owner.id
    assert action.user_feedback is None and action.resolved_at is not None
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            decision="accept",
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_accept_resolves_future_expiry_within_the_database_clock_and_calls_no_provider(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path
    )
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert action is not None
    action.expires_at = datetime.now(UTC) + timedelta(hours=1)
    await async_session.commit()

    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )

    assert provider.calls == 0 and provider.review_calls == 0
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    resolved = await async_session.get(ActionRequest, started.action_request_id)
    assert run is not None and resolved is not None
    assert run.status == ChapterProductionStatus.EDITOR_REVIEW.value
    assert run.awaiting_user is False
    assert resolved.status == ActionRequestStatus.APPROVED.value
    assert resolved.resolved_at is not None and resolved.user_decision == "accept"


@pytest.mark.integration
@pytest.mark.anyio
async def test_accept_fails_closed_when_the_database_clock_passed_expiry(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path
    )
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert action is not None
    action.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            decision="accept",
        )

    assert provider.calls == 0 and provider.review_calls == 0
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    pending = await async_session.get(ActionRequest, started.action_request_id)
    assert run is not None and pending is not None
    assert run.status == ChapterProductionStatus.AUTHOR_REVISION.value
    assert run.awaiting_user is True
    assert pending.status == ActionRequestStatus.PENDING.value
    assert pending.resolved_at is None and pending.user_decision is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_feedback_revision_uses_current_locators_merges_target_and_reopens_gate(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path
    )
    old_content = await DocumentService(async_session).read_version_content(
        started.draft_document_id, started.draft_version_id
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    revised = await service.request_user_feedback_revision(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        feedback="Make the consequence concrete without changing the other scene.",
        target_segment_ids=(target.segment_id,),
    )

    assert provider.calls == 1 and provider.request is not None
    assert provider.request.source_draft.version_id == started.draft_version_id
    assert provider.request.target_segment_ids == (target.segment_id,)
    assert tuple(item.segment_id for item in provider.request.source_draft.segments) == tuple(
        item.segment_id for item in segment_map.segments
    )
    old_action = await async_session.get(ActionRequest, started.action_request_id)
    new_action = await async_session.get(ActionRequest, revised.action_request_id)
    new_version = await async_session.get(DocumentVersion, revised.draft_version_id)
    assert old_action is not None and new_action is not None and new_version is not None
    assert old_action.status == ActionRequestStatus.REVISED.value
    assert old_action.user_decision == "request_revision"
    assert old_action.user_feedback == (
        "Make the consequence concrete without changing the other scene."
    )
    assert "consequence" not in str(old_action.metadata_)
    assert new_version.parent_version_id == started.draft_version_id
    assert new_version.source == DocumentSource.WRITER_AGENT.value
    assert new_version.agent_role == "revision_agent"
    assert new_version.workflow_run_id == started.workflow_run_id
    new_content = await DocumentService(async_session).read_version_content(
        revised.draft_document_id, revised.draft_version_id
    )
    old_bytes = old_content.encode()
    new_bytes = new_content.encode()
    assert old_bytes[: target.start_byte] == new_bytes[: target.start_byte]
    assert old_content[target.end_byte :] in new_content
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ChapterProductionStatus.AUTHOR_REVISION.value
    assert run.awaiting_user is True
    assert new_action.status == ActionRequestStatus.PENDING.value
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback="replay",
            target_segment_ids=(target.segment_id,),
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_post_feedback_manual_commit_crash_uses_legacy_recovery_without_provider(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    revised = await service.request_user_feedback_revision(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        feedback="Open a post-feedback author gate.",
        target_segment_ids=(target,),
    )
    calls = provider.calls

    async def lose_finalize_ack(**_: object) -> object:
        raise ChapterProductionV2CommitIndeterminateError() from None

    with monkeypatch.context() as patch:
        patch.setattr(service._manual_edit, "_finalize_manual_edit", lose_finalize_ack)
        with pytest.raises(ChapterProductionV2CommitIndeterminateError):
            await service.submit_manual_edit(
                project.id,
                chapter.id,
                started.workflow_run_id,
                revised.action_request_id,
                actor_user_id=owner.id,
                content="# Arrival\n\nThe post-feedback user child survived.\n",
            )
    for entrypoint in ("start", "resume"):
        with pytest.raises(ChapterProductionV2ValidationError):
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
    recovered = await service.reconcile_indeterminate(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
    )
    assert recovered.status is ChapterProductionStatus.EDITOR_REVIEW
    assert provider.calls == calls


@pytest.mark.integration
@pytest.mark.anyio
async def test_manual_edit_requires_owner_and_creates_parent_linked_user_version(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    stranger = User(username=f"stranger-{uuid4().hex}", display_name="Stranger")
    async_session.add(stranger)
    await async_session.commit()
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            project_id,
            chapter_id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=stranger.id,
            content="# Private\n\nunauthorized edit\n",
        )

    result = await service.submit_manual_edit(
        project_id,
        chapter_id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner_id,
        content="# Arrival\n\nUser-authored exact replacement.\n",
    )

    version = await async_session.get(DocumentVersion, result.draft_version_id)
    action = await async_session.get(ActionRequest, started.action_request_id)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert version is not None and action is not None and run is not None
    assert version.parent_version_id == started.draft_version_id
    assert version.source == DocumentSource.USER.value
    assert str(version.actor_user_id) == str(owner_id) and version.agent_role is None
    assert version.workflow_run_id == run.id
    assert action.status == ActionRequestStatus.REVISED.value
    assert action.user_decision == "submit_manual_edit"
    assert action.user_feedback is None
    assert run.status == ChapterProductionStatus.EDITOR_REVIEW.value
    assert run.awaiting_user is False


@pytest.mark.integration
@pytest.mark.anyio
async def test_author_actions_reject_foreign_locator_action_stale_current_and_duplicates(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path / "foreign"
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.request_user_feedback_revision(
            project_id,
            chapter_id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner_id,
            feedback="bounded feedback",
            target_segment_ids=(uuid4(),),
        )
    assert provider.calls == 0
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project_id,
            chapter_id,
            started.workflow_run_id,
            uuid4(),
            actor_user_id=owner_id,
            decision="accept",
        )

    action = await async_session.get(ActionRequest, started.action_request_id)
    assert action is not None
    duplicate = ActionRequest(
        workflow_run_id=started.workflow_run_id,
        project_id=project_id,
        chapter_id=chapter_id,
        request_type=action.request_type,
        status=ActionRequestStatus.PENDING.value,
        prompt=action.prompt,
        options=action.options,
        default_option=action.default_option,
        metadata_=dict(action.metadata_),
    )
    async_session.add(duplicate)
    await async_session.commit()
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project_id,
            chapter_id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner_id,
            decision="accept",
        )

    (
        other_project,
        other_chapter,
        other_owner,
        other_service,
        other_started,
        _,
    ) = await owned_started_chapter(async_session, tmp_path / "stale")
    await DocumentService(async_session).write_document(
        document_id=other_started.draft_document_id,
        content="# Changed elsewhere\n\nNew current version.\n",
        source=DocumentSource.USER,
        expected_current_version_id=other_started.draft_version_id,
        actor_user_id=other_owner.id,
        change_summary="Concurrent external edit.",
    )
    adopted = await other_service.submit_manual_edit(
        other_project.id,
        other_chapter.id,
        other_started.workflow_run_id,
        other_started.action_request_id,
        actor_user_id=other_owner.id,
        content="# Stale replacement\n\nMust not commit.\n",
    )
    stale_action = await async_session.get(ActionRequest, other_started.action_request_id)
    adopted_state = await other_service.load_state(
        other_project.id,
        other_chapter.id,
        other_started.workflow_run_id,
        actor_user_id=other_owner.id,
    )
    assert stale_action is not None and stale_action.status == ActionRequestStatus.CANCELLED.value
    assert adopted_state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert str(adopted.draft_version_id) == adopted_state.document_version_id


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_revision_consumes_persisted_refs_without_creating_reports(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, provider, report = await seeded_review_revision(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")
    report_count = await async_session.scalar(select(func.count()).select_from(ReviewReport))

    result = await service.execute_review_revision(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
        report_ids=(report.id,),
        target_segment_ids=(target.segment_id,),
    )

    assert provider.review_calls == 1 and provider.review_request is not None
    assert provider.review_request.source_draft.version_id == started.draft_version_id
    assert provider.review_request.review_report_refs[0].report_id == report.id
    assert (
        await async_session.scalar(select(func.count()).select_from(ReviewReport)) == report_count
    )
    version = await async_session.get(DocumentVersion, result.draft_version_id)
    state = await service.load_state(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
    )
    assert version is not None
    assert version.parent_version_id == started.draft_version_id
    assert version.source == DocumentSource.WRITER_AGENT.value
    assert version.agent_role == "revision_agent"
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.editor_report_id is None
    assert state.chief_editor_report_id is None and state.lore_report_id is None
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target.segment_id,),
        )
    assert provider.review_calls == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_reconcile_committed_feedback_version_never_reinvokes_provider(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    async def lose_finalize_ack(*args: object, **kwargs: object) -> object:
        raise ChapterProductionV2CommitIndeterminateError()

    monkeypatch.setattr(service._feedback_saga, "finalize", lose_finalize_ack)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback="Make the consequence exact.",
            target_segment_ids=(target.segment_id,),
        )
    version_count = await async_session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.document_id == started.draft_document_id)
    )
    calls = provider.calls
    reconciler = ChapterProductionV2Service(
        async_session,
        writer_agent=service.writer_agent,
        revision_agent=service.revision_agent,
        phase_session_source=phase_source(async_session),
    )

    state = await reconciler.reconcile_indeterminate(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
    )
    replayed = await reconciler.reconcile_indeterminate(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
    )

    assert state.status is ChapterProductionStatus.AUTHOR_REVISION
    assert replayed == state
    assert provider.calls == calls
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == started.draft_document_id)
        )
        == version_count
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_provider_cancellation_is_rethrown_without_false_failure(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(CancellingProvider()),
        phase_session_source=phase_source(async_session),
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        await service.start_from_approved_outline(
            project.id,
            chapter.id,
            actor_user_id=project.owner_id,
        )

    assert raised.value.args == ()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "canary" not in repr(raised.value)

    run = await async_session.scalar(
        select(WorkflowRun).where(WorkflowRun.chapter_id == chapter.id)
    )
    assert run is not None and run.status == ChapterProductionStatus.DRAFTING.value


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_provider_phase3_rechecks_the_locked_project_owner(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    original_owner_id = project.owner_id
    assert original_owner_id is not None
    replacement = User(username=f"replacement-{uuid4().hex}", display_name="Replacement")
    async_session.add(replacement)
    await async_session.commit()
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(OwnershipTransferringProvider(async_session, replacement.id)),
        phase_session_source=phase_source(async_session),
    )

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await service.start_from_approved_outline(
            project.id,
            chapter.id,
            actor_user_id=original_owner_id,
        )
    assert raised.value.__cause__ is None and raised.value.__context__ is None

    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.workflow_run_id.is_not(None))
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_feedback_provider_cancellation_is_rethrown_without_false_failure(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    service.revision_agent = RevisionAgent(CancellingProvider())
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    with pytest.raises(asyncio.CancelledError) as raised:
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback="Keep the cancellation resumable.",
            target_segment_ids=(target.segment_id,),
        )

    assert raised.value.args == ()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "canary" not in repr(raised.value)

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None and run.status == ChapterProductionStatus.AUTHOR_REVISION.value


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_provider_cancellation_is_rethrown_without_false_failure(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    service.revision_agent = RevisionAgent(CancellingProvider())
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    with pytest.raises(asyncio.CancelledError) as raised:
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target.segment_id,),
        )

    assert raised.value.args == ()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "canary" not in repr(raised.value)

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None and run.status == ChapterProductionStatus.REVIEW_REVISION.value


@pytest.mark.integration
@pytest.mark.anyio
async def test_feedback_provider_exception_context_is_not_exposed(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    service.revision_agent = RevisionAgent(UnsafeFailingProvider())
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    with pytest.raises(ChapterProductionV2ProviderError) as raised:
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback="Keep provider details private.",
            target_segment_ids=(target.segment_id,),
        )

    assert_safe_provider_error(raised.value)


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_provider_exception_context_is_not_exposed(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    service.revision_agent = RevisionAgent(UnsafeFailingProvider())
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    with pytest.raises(ChapterProductionV2ProviderError) as raised:
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target.segment_id,),
        )

    assert_safe_provider_error(raised.value)


@pytest.mark.integration
@pytest.mark.anyio
async def test_feedback_reconciliation_error_is_preserved_after_provider_call(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    async def stale_generation(*args: object, **kwargs: object) -> None:
        raise ChapterProductionV2ReconciliationError()

    monkeypatch.setattr(service._feedback_saga, "persist", stale_generation)
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback="Preserve reconciliation semantics.",
            target_segment_ids=(target.segment_id,),
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_reconciliation_error_is_preserved_after_provider_call(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, owner, service, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    async def stale_generation(**kwargs: object) -> None:
        raise ChapterProductionV2ReconciliationError()

    monkeypatch.setattr(service.documents, "write_document", stale_generation)
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target.segment_id,),
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_known_provider_failure_recovers_only_matching_attempt_then_succeeds(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    provider = FailOnceProvider()
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=phase_source(async_session),
    )
    with pytest.raises(ChapterProductionV2ProviderError):
        await service.start_from_approved_outline(
            project.id, chapter.id, actor_user_id=project.owner_id
        )
    run = await async_session.scalar(
        select(WorkflowRun).where(WorkflowRun.chapter_id == chapter.id)
    )
    assert run is not None and run.status == ChapterProductionStatus.FAILED.value
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.reconcile_indeterminate(
            project.id, chapter.id, run.id, actor_user_id=project.owner_id
        )
    assert provider.calls == 1

    result = await service.resume_drafting(
        project.id, chapter.id, run.id, actor_user_id=project.owner_id
    )

    assert result.workflow_run_id == run.id and provider.calls == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_reconcile_adopts_unique_parentless_candidate_when_pointer_is_none(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    source = phase_source(async_session)
    lease = ChapterPhaseSessionLease(source)
    provider = TransactionCheckingProvider(async_session)
    result = await InitialProviderHandoff(
        lease, WriterAgent(provider), True
    ).execute(project.id, chapter.id, actor_user_id=project.owner_id)
    identity = await InitialCandidatePersistence(lease, True).persist(result)
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=source,
    )
    state = await service.reconcile_indeterminate(
        project.id,
        chapter.id,
        identity.workflow_run_id,
        actor_user_id=project.owner_id,
    )

    assert state.status is ChapterProductionStatus.AUTHOR_REVISION
    assert state.document_id == str(identity.document_id)
    assert state.document_version_id == str(identity.version_id)
    assert provider.calls == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_warning_passed_report_can_drive_an_explicit_revision(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    report.passed = True
    warning = {
        **report.blocking_issues[0],
        "severity": "warning",
        "required": False,
    }
    report.blocking_issues = []
    report.warnings = [warning]
    action = await async_session.scalar(
        select(ActionRequest).where(
            ActionRequest.workflow_run_id == started.workflow_run_id,
            ActionRequest.status == ActionRequestStatus.REVISED.value,
        )
    )
    assert action is not None
    action.request_type = "chapter_review_warning"
    action.options = ["accept_warning", "request_revision"]
    action.default_option = None
    action.metadata_ = {**action.metadata_, "action_kind": "review_warning"}
    await async_session.commit()
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(item for item in segment_map.segments if item.kind.value == "paragraph")

    result = await service.execute_review_revision(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
        report_ids=(report.id,),
        target_segment_ids=(target.segment_id,),
    )

    assert result.draft_version_id != started.draft_version_id


@pytest.mark.integration
@pytest.mark.anyio
async def test_changed_outline_cannot_create_a_second_active_v2_run(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, provider = await owned_started_chapter(
        async_session, tmp_path
    )
    project_id, chapter_id, owner_id = project.id, chapter.id, owner.id
    outline = await async_session.get(Document, chapter.current_outline_document_id)
    assert outline is not None and outline.current_version_id is not None
    await DocumentService(async_session).write_document(
        document_id=outline.id,
        content="# Changed plan\n\nUse the newly approved route.\n",
        source=DocumentSource.USER,
        expected_current_version_id=outline.current_version_id,
        actor_user_id=owner_id,
        change_summary="Approve a changed outline snapshot.",
    )

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.start_from_approved_outline(project_id, chapter_id, actor_user_id=owner_id)

    runs = list(
        await async_session.scalars(select(WorkflowRun).where(WorkflowRun.chapter_id == chapter_id))
    )
    assert [run.id for run in runs] == [started.workflow_run_id]
    assert provider.calls == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_sessions_initial_resume_claims_exactly_one_provider_and_candidate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    project_id, chapter_id, owner_id = project.id, chapter.id, project.owner_id
    provider = BarrierProvider()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    source = ChapterPhaseSessionSource(engine)

    async def start_once() -> ChapterProductionV2Started:
        async with sessions() as session:
            return await ChapterProductionV2Service(
                session,
                writer_agent=WriterAgent(provider),
                phase_session_source=source,
            ).start_from_approved_outline(project_id, chapter_id, actor_user_id=owner_id)

    first = asyncio.create_task(start_once())
    await asyncio.wait_for(provider.entered.wait(), timeout=10.0)
    try:

        with pytest.raises(ChapterProductionV2ReconciliationError):
            await start_once()
    finally:
        provider.release.set()
    started = await first
    try:
        async with sessions() as check:
            versions = list(
                await check.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.workflow_run_id == started.workflow_run_id
                    )
                )
            )
            actions = list(
                await check.scalars(
                    select(ActionRequest).where(
                        ActionRequest.workflow_run_id == started.workflow_run_id
                    )
                )
            )
            checkpoints = list(
                await check.scalars(
                    select(WorkflowCheckpoint).where(
                        WorkflowCheckpoint.workflow_run_id == started.workflow_run_id
                    )
                )
            )
        assert provider.calls == 1
        assert len(versions) == len(actions) == 1
        assert sorted(item.checkpoint_index for item in checkpoints) == [0, 1]
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_sessions_feedback_claims_one_provider_version_and_successor_action(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner, _, started, _ = await owned_started_chapter(async_session, tmp_path)
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    provider = BarrierRevisionProvider()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def revise_once() -> ChapterProductionV2Updated:
        async with sessions() as session:
            return await ChapterProductionV2Service(
                session,
                writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
                revision_agent=RevisionAgent(provider),
            ).request_user_feedback_revision(
                project.id,
                chapter.id,
                started.workflow_run_id,
                started.action_request_id,
                actor_user_id=owner.id,
                feedback="Bind this exact concurrent feedback attempt.",
                target_segment_ids=(target,),
            )

    first = asyncio.create_task(revise_once())
    await asyncio.wait_for(provider.entered.wait(), timeout=10.0)
    try:

        with pytest.raises(ChapterProductionV2ValidationError):
            await revise_once()
    finally:
        provider.release.set()
    revised = await first
    try:
        async with sessions() as check:
            version_count = await check.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == started.draft_document_id)
            )
            actions = list(
                await check.scalars(
                    select(ActionRequest).where(
                        ActionRequest.workflow_run_id == started.workflow_run_id
                    )
                )
            )
        assert provider.calls == 1 and version_count == 2
        assert len(actions) == 2
        assert sum(item.status == ActionRequestStatus.PENDING.value for item in actions) == 1
        assert revised.action_request_id is not None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("old_outcome", ["success", "fail", "cancel"])
async def test_claimed_no_candidate_requires_explicit_ack_before_restart(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    old_outcome: str,
) -> None:
    project, chapter, _, _ = await approved_chapter(async_session, tmp_path)
    assert project.owner_id is not None
    project_id, chapter_id, owner_id = project.id, chapter.id, project.owner_id
    provider = OutcomeBarrierProvider(old_outcome)
    retry_provider = BarrierProvider()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    source = ChapterPhaseSessionSource(engine)

    async def start_claim() -> ChapterProductionV2Started:
        async with sessions() as session:
            return await ChapterProductionV2Service(
                session,
                writer_agent=WriterAgent(provider),
                phase_session_source=source,
            ).start_from_approved_outline(project_id, chapter_id, actor_user_id=owner_id)

    pending = asyncio.create_task(start_claim())
    await asyncio.wait_for(provider.entered.wait(), timeout=10.0)
    try:
        async with sessions() as restarted:
            service = ChapterProductionV2Service(
                restarted,
                writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
                phase_session_source=source,
            )
            run = await restarted.scalar(
                select(WorkflowRun).where(WorkflowRun.chapter_id == chapter_id)
            )
            assert run is not None
            run_id = run.id
            attempt = run.metadata_["provider_attempt"]
            assert type(attempt) is dict
            with pytest.raises(ChapterProductionV2ReconciliationError):
                await service.reconcile_indeterminate(
                    project_id, chapter_id, run_id, actor_user_id=owner_id
                )
            await service.acknowledge_provider_no_write(
                project_id,
                chapter_id,
                run_id,
                actor_user_id=owner_id,
                expected_attempt_key=attempt["key"],
                expected_attempt_id=attempt["attempt_id"],
            )

        async def restart_claim() -> ChapterProductionV2Started:
            async with sessions() as restarted:
                return await ChapterProductionV2Service(
                    restarted,
                    writer_agent=WriterAgent(retry_provider),
                    phase_session_source=source,
                ).start_from_approved_outline(project_id, chapter_id, actor_user_id=owner_id)

        retry = asyncio.create_task(restart_claim())
        await asyncio.wait_for(retry_provider.entered.wait(), timeout=10.0)

        provider.release.set()
        expected_error = {
            "success": ChapterProductionV2ReconciliationError,
            "fail": ChapterProductionV2ProviderError,
            "cancel": asyncio.CancelledError,
        }[old_outcome]
        with pytest.raises(expected_error):
            await pending
        retry_provider.release.set()
        result = await retry
        assert result.draft_document_id is not None
        async with sessions() as check:
            versions = list(
                await check.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.workflow_run_id == result.workflow_run_id
                    )
                )
            )
            assert len(versions) == 1
    finally:
        provider.release.set()
        retry_provider.release.set()
        if not pending.done():
            pending.cancel()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_revision_agent_none_rolls_back_feedback_claim_transaction(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    service.revision_agent = None
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )

    with pytest.raises(ChapterProductionV2ProviderError):
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback="Must not leave a transaction open.",
            target_segment_ids=(target,),
        )

    assert async_session.in_transaction() is False
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert action is not None and action.status == ActionRequestStatus.PENDING.value
    assert action.user_decision is None and action.user_feedback is None


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("tamper", ["missing", "duplicate", "wrong-decision"])
async def test_failed_feedback_recovery_rejects_ambiguous_or_tampered_source_action(
    async_session: AsyncSession, tmp_path: Path, tamper: str
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path / tamper
    )
    failing = FailingFeedbackProvider()
    service.revision_agent = RevisionAgent(failing)
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    feedback = "Keep this exact failed feedback identity."
    with pytest.raises(ChapterProductionV2ProviderError):
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback=feedback,
            target_segment_ids=(target,),
        )
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert action is not None
    if tamper == "missing":
        await async_session.delete(action)
    elif tamper == "wrong-decision":
        action.user_decision = ChapterActionDecision.ACCEPT.value
    else:
        async_session.add(
            ActionRequest(
                workflow_run_id=action.workflow_run_id,
                project_id=action.project_id,
                chapter_id=action.chapter_id,
                request_type=action.request_type,
                status=action.status,
                prompt=action.prompt,
                options=action.options,
                user_decision=action.user_decision,
                user_feedback=action.user_feedback,
                resolved_by_id=action.resolved_by_id,
                resolved_at=action.resolved_at,
                metadata_=dict(action.metadata_),
            )
        )
    await async_session.commit()
    failing.calls = 0

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.request_user_feedback_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            feedback=feedback,
            target_segment_ids=(target,),
        )
    assert failing.calls == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_failed_review_recovery_rejects_mutated_report_before_provider(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    failing = FailingReviewProvider()
    service.revision_agent = RevisionAgent(failing)
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    with pytest.raises(ChapterProductionV2ProviderError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target,),
        )
    report.summary = "Changed after the failed provider attempt."
    await async_session.commit()
    failing.calls = 0

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target,),
        )
    assert failing.calls == 0


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("foreign_kind", ["foreign-user", "agent", "workflow"])
async def test_stale_action_auto_adoption_rejects_foreign_child_attribution(
    async_session: AsyncSession, tmp_path: Path, foreign_kind: str
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path / foreign_kind
    )
    replacement = User(username=f"foreign-{uuid4().hex}", display_name="Foreign")
    async_session.add(replacement)
    await async_session.commit()
    kwargs: dict[str, object] = {
        "source": DocumentSource.USER,
        "actor_user_id": owner.id,
    }
    if foreign_kind == "foreign-user":
        kwargs["actor_user_id"] = replacement.id
    elif foreign_kind == "agent":
        kwargs = {"source": DocumentSource.WRITER_AGENT, "agent_role": "revision_agent"}
    else:
        kwargs["workflow_run_id"] = started.workflow_run_id
    await DocumentService(async_session).write_document(
        document_id=started.draft_document_id,
        content="# Foreign child\n\nMust not be auto-adopted.\n",
        expected_current_version_id=started.draft_version_id,
        change_summary="Foreign-attributed child.",
        **kwargs,  # type: ignore[arg-type]
    )

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            decision="accept",
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_editor_review_reconcile_rejects_unexpected_candidate(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    await DocumentService(async_session).write_document(
        document_id=started.draft_document_id,
        content="# Unexpected workflow child\n\nDo not adopt this in Editor review.\n",
        source=DocumentSource.WRITER_AGENT,
        expected_current_version_id=started.draft_version_id,
        agent_role="revision_agent",
        workflow_run_id=started.workflow_run_id,
        change_summary="Unexpected editor-review child.",
        version_metadata={"contract_version": "chapter-production-v2", "operation_key": "a" * 64},
    )

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.reconcile_indeterminate(
            project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_report_summary_mutation_invalidates_claimed_work_identity(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner, _, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    provider = BarrierRevisionProvider()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def revise() -> ChapterProductionV2Updated:
        async with sessions() as session:
            return await ChapterProductionV2Service(
                session,
                writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
                revision_agent=RevisionAgent(provider),
            ).execute_review_revision(
                project.id,
                chapter.id,
                started.workflow_run_id,
                actor_user_id=owner.id,
                report_ids=(report.id,),
                target_segment_ids=(target,),
            )

    pending = asyncio.create_task(revise())
    await asyncio.wait_for(provider.entered.wait(), timeout=10.0)
    try:

        async with sessions() as mutation:
            current = await mutation.get(ReviewReport, report.id)
            assert current is not None
            current.summary = "Mutated after the provider claim."
            await mutation.commit()
        provider.release.set()
        with pytest.raises(ChapterProductionV2ValidationError):
            await pending
        async with sessions() as check:
            assert (
                await check.scalar(
                    select(func.count())
                    .select_from(DocumentVersion)
                    .where(DocumentVersion.parent_version_id == started.draft_version_id)
                )
                == 0
            )
    finally:
        provider.release.set()
        if not pending.done():
            pending.cancel()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_finalize_revalidates_claimed_report_after_document_commit(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner, service, started, _, report = await seeded_review_revision(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    original_finalize = service._review_saga.finalize
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def mutate_then_finalize(
        identity: object, **kwargs: object
    ) -> ChapterProductionV2Updated:
        async with sessions() as mutation:
            current = await mutation.get(ReviewReport, report.id)
            assert current is not None
            current.raw_report = {
                **current.raw_report,
                "provider_envelope": "mutated after the document version commit",
            }
            await mutation.commit()
        return await original_finalize(identity, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service._review_saga, "finalize", mutate_then_finalize)
    try:
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await service.execute_review_revision(
                project.id,
                chapter.id,
                started.workflow_run_id,
                actor_user_id=owner.id,
                report_ids=(report.id,),
                target_segment_ids=(target,),
            )
        assert (
            await async_session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.parent_version_id == started.draft_version_id)
            )
            == 1
        )
        run = await async_session.get(WorkflowRun, started.workflow_run_id)
        assert run is not None
        assert run.status == ChapterProductionStatus.REVIEW_REVISION.value
        assert type(run.metadata_.get("provider_attempt")) is dict
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_review_claim_commit_indeterminate_propagates_unchanged(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter, owner, service, started, provider, report = await seeded_review_revision(
        async_session, tmp_path
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )

    async def indeterminate_commit() -> None:
        raise ChapterProductionV2CommitIndeterminateError()

    monkeypatch.setattr(service, "_commit", indeterminate_commit)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target,),
        )
    assert provider.review_calls == 0


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("entrypoint", ("start", "resume", "reconcile", "load"))
async def test_legacy_run_metadata_without_reviewer_claim_normalizes_at_public_entrypoints(
    async_session: AsyncSession, tmp_path: Path, entrypoint: str
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path
    )
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    legacy_metadata = dict(run.metadata_)
    legacy_metadata.pop("reviewer_claim")
    run.metadata_ = legacy_metadata
    await async_session.commit()

    if entrypoint == "start":
        replayed = await service.start_from_approved_outline(
            project.id, chapter.id, actor_user_id=owner.id
        )
        assert replayed == started
    elif entrypoint == "resume":
        replayed = await service.resume_drafting(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
        )
        assert replayed == started
    elif entrypoint == "reconcile":
        reconciled = await service.reconcile_indeterminate(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
        )
        assert reconciled.status is ChapterProductionStatus.AUTHOR_REVISION
    else:
        loaded = await service.load_state(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
        )
        assert loaded.status is ChapterProductionStatus.AUTHOR_REVISION
    await async_session.refresh(run)
    assert run.metadata_ == {**legacy_metadata, "reviewer_claim": None}

    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    resumed = await service.load_state(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
    )
    assert resumed.status is ChapterProductionStatus.EDITOR_REVIEW


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("malformation", ["missing_other_key", "unexpected_key"])
async def test_legacy_run_metadata_compatibility_rejects_other_malformed_shapes(
    async_session: AsyncSession, tmp_path: Path, malformation: str
) -> None:
    project, chapter, owner, service, started, _ = await owned_started_chapter(
        async_session, tmp_path / malformation
    )
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    metadata = dict(run.metadata_)
    metadata.pop("reviewer_claim")
    if malformation == "missing_other_key":
        metadata.pop("operation_key")
    else:
        metadata["unexpected"] = "value"
    run.metadata_ = metadata
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.load_state(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
        )


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "tamper",
    [
        "raw_report_shape",
        "raw_report_provenance",
        "finding_shape",
        "evidence",
        "severity",
        "passed",
    ],
)
async def test_review_revision_revalidates_persisted_report_before_provider(
    async_session: AsyncSession, tmp_path: Path, tamper: str
) -> None:
    project, chapter, owner, service, started, provider, report = (
        await seeded_review_revision(async_session, tmp_path / tamper)
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    if tamper == "raw_report_shape":
        report.raw_report = {**report.raw_report, "provider_envelope": "forbidden"}
    elif tamper == "raw_report_provenance":
        report.raw_report = {**report.raw_report, "request_hash": "not-a-hash"}
    elif tamper == "finding_shape":
        report.blocking_issues = [{**report.blocking_issues[0], "unexpected": True}]
    elif tamper == "evidence":
        report.blocking_issues = [
            {**report.blocking_issues[0], "evidence_segment_ids": [str(uuid4())]}
        ]
    elif tamper == "severity":
        report.blocking_issues = [{**report.blocking_issues[0], "severity": "warning"}]
    else:
        report.passed = True
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target,),
        )
    assert provider.review_calls == 0
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.parent_version_id == started.draft_version_id)
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "tamper", ["missing", "decision", "report_binding", "duplicate"]
)
async def test_review_revision_requires_exact_resolved_review_action_gate(
    async_session: AsyncSession, tmp_path: Path, tamper: str
) -> None:
    project, chapter, owner, service, started, provider, report = (
        await seeded_review_revision(async_session, tmp_path / tamper)
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=started.draft_document_id,
        version_id=started.draft_version_id,
    )
    target = next(
        item.segment_id for item in segment_map.segments if item.kind.value == "paragraph"
    )
    action = await async_session.scalar(
        select(ActionRequest).where(
            ActionRequest.workflow_run_id == started.workflow_run_id,
            ActionRequest.status == ActionRequestStatus.REVISED.value,
        )
    )
    assert action is not None
    if tamper == "missing":
        await async_session.delete(action)
    elif tamper == "decision":
        action.user_decision = ChapterActionDecision.ACCEPT_WARNING.value
    elif tamper == "report_binding":
        action.metadata_ = {**action.metadata_, "review_report_id": str(uuid4())}
    else:
        async_session.add(
            ActionRequest(
                workflow_run_id=action.workflow_run_id,
                project_id=action.project_id,
                chapter_id=action.chapter_id,
                request_type=action.request_type,
                status=action.status,
                prompt=action.prompt,
                options=list(action.options),
                default_option=action.default_option,
                user_decision=action.user_decision,
                user_feedback=action.user_feedback,
                resolved_by_id=action.resolved_by_id,
                resolved_at=action.resolved_at,
                metadata_=dict(action.metadata_),
            )
        )
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service.execute_review_revision(
            project.id,
            chapter.id,
            started.workflow_run_id,
            actor_user_id=owner.id,
            report_ids=(report.id,),
            target_segment_ids=(target,),
        )
    assert provider.review_calls == 0
