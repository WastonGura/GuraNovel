"""Feedback validation, claim, and session-free Revision-provider handoff.

Phase 1 claims the exact author-gate revision in a fresh short transaction and
closes it. Phase 2 calls the Revision provider with no session or transaction
open, using a pure snapshot. The result is a frozen content-safe replacement
plan; Phase 3 persistence/finalization stays in the facade (issue #169).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import CandidateChapterOutput, RevisionAgent, UserFeedbackRevisionRequest
from app.documents.chapter_segments import ChapterSegmentMap
from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.author_accept_coordination import _StaleActionAdopted
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_production_repository import (
    ChapterProductionRepository,
    _ChapterProductionRepositoryValidationError,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ProviderError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentService
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
)
from app.workspace.hashing import sha256_content


_CONTRACT_VERSION = "chapter-production-v2"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"
_INACTIVE = frozenset({ChapterProductionStatus.COMPLETED.value, ChapterProductionStatus.CANCELLED.value})


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _safe_cancelled_error(_: BaseException) -> asyncio.CancelledError:
    """Return a cancellation signal that cannot disclose provider exception data."""
    return asyncio.CancelledError()


def _new_attempt_id() -> str:
    """Create a content-free provider-attempt generation identifier."""
    return str(uuid4())


def _expiry_precludes_resolution(expires_at: object, database_now: object) -> bool:
    """Fail closed once the single database clock reaches the action expiry."""
    return expires_at is not None and database_now >= expires_at


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except BaseException:
        pass


async def _commit(session: AsyncSession) -> None:
    failed = False
    try:
        await session.commit()
    except BaseException:
        failed = True
    if failed:
        await _rollback(session)
        raise ChapterProductionV2CommitIndeterminateError() from None


def _author_binding(
    action: ActionRequest, run: WorkflowRun, chapter: Chapter, document_id: UUID,
    version_id: UUID, content_hash: str, current_document_id: UUID,
    current_version_id: UUID, current_content_hash: str,
) -> ChapterActionBinding:
    return ChapterActionBinding(
        action_request_id=str(action.id),
        workflow_run_id=str(run.id),
        chapter_id=str(chapter.id),
        request_type=action.request_type,
        kind=ChapterActionKind.AUTHOR_REVISION,
        status=ActionRequestStatus.PENDING,
        pending_count=1,
        document_id=str(document_id),
        document_version_id=str(version_id),
        content_hash=content_hash,
        current_document_id=str(current_document_id),
        current_document_version_id=str(current_version_id),
        current_content_hash=current_content_hash,
    )


@dataclass(frozen=True, slots=True)
class _ClaimContext:
    run: WorkflowRun
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    action: ActionRequest
    binding: ChapterActionBinding
    document: Document
    version: DocumentVersion


@dataclass(frozen=True, slots=True)
class _Scope:
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    action_request_id: UUID
    actor_user_id: UUID
    feedback: str = field(repr=False)
    target_segment_ids: tuple[UUID, ...]
    feedback_hash: str


@dataclass(frozen=True, slots=True, repr=False)
class _Claim:
    source_document_id: UUID
    source_version_id: UUID
    source_content_hash: str
    operation_key: str
    attempt_id: str
    attempt_checkpoint_index: int
    feedback: str = field(repr=False)
    target_segment_ids: tuple[UUID, ...]
    segment_map: ChapterSegmentMap = field(repr=False)
    request: UserFeedbackRevisionRequest = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class FeedbackRevisionPlan:
    """Frozen, content-safe handoff between the provider and Phase 3 persistence."""

    source_document_id: UUID
    source_version_id: UUID
    source_content_hash: str
    operation_key: str
    attempt_id: str
    attempt_checkpoint_index: int
    feedback: str = field(repr=False)
    target_segment_ids: tuple[UUID, ...]
    segment_map: ChapterSegmentMap = field(repr=False)
    candidate: CandidateChapterOutput = field(repr=False)

    def __repr__(self) -> str:
        return (
            "FeedbackRevisionPlan("
            f"source_document_id={self.source_document_id!r}, "
            f"source_version_id={self.source_version_id!r}, "
            f"source_content_hash={self.source_content_hash!r}, "
            f"operation_key={self.operation_key!r}, "
            f"attempt_id={self.attempt_id!r}, "
            f"attempt_checkpoint_index={self.attempt_checkpoint_index!r}, "
            f"target_segment_ids={self.target_segment_ids!r}"
            ")"
        )


class _FeedbackClaimPhase:
    """Session-bound Phase 1 walk, mirroring the facade claim without its session."""

    def __init__(self, session: AsyncSession, service: object) -> None:
        self.session = session
        self.service = service
        self.documents = DocumentService(session)
        self.repository = ChapterProductionRepository(
            session, contract_version=_CONTRACT_VERSION, inactive_run_statuses=_INACTIVE
        )

    async def _require_project_owner(self, project_id: UUID, actor_user_id: UUID) -> None:
        try:
            await self.repository.require_project_owner(project_id, actor_user_id, lock=True)
        except _ChapterProductionRepositoryValidationError:
            raise _invalid() from None

    async def _chapter(self, project_id: UUID, chapter_id: UUID) -> Chapter:
        try:
            return await self.repository.chapter(project_id, chapter_id, lock=True)
        except _ChapterProductionRepositoryValidationError:
            raise _invalid() from None

    async def _run(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID) -> WorkflowRun:
        try:
            return await self.repository.run(project_id, chapter_id, workflow_run_id, lock=True)
        except _ChapterProductionRepositoryValidationError:
            raise _invalid() from None

    async def _locked_state(self, run: WorkflowRun) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
        self.service._run_metadata(run)  # type: ignore[attr-defined]
        checkpoints = list(await self.session.scalars(
            select(WorkflowCheckpoint).execution_options(populate_existing=True)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc()).limit(2).with_for_update()
        ))
        if not checkpoints:
            raise _invalid()
        checkpoint = checkpoints[0]
        if len(checkpoints) == 2 and checkpoint.checkpoint_index != checkpoints[1].checkpoint_index + 1:
            raise _invalid()
        payload = checkpoint.state_json
        if type(payload) is not dict:
            raise _invalid()
        try:
            state = ChapterProductionState.from_checkpoint(payload)
            state.validate_persistence_binding(
                workflow_run_id=str(run.id), chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type, run_status=run.status,
                run_current_node=run.current_node, run_awaiting_user=run.awaiting_user,
                checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                checkpoint_node_name=checkpoint.node_name,
            )
            return state, checkpoint
        except (ChapterProductionValidationError, KeyError, TypeError, ValueError):
            raise _invalid() from None

    def _append_state(
        self, run: WorkflowRun, checkpoint: WorkflowCheckpoint, state: ChapterProductionState
    ) -> None:
        self.service._project_state(run, state)  # type: ignore[attr-defined]
        self.session.add(WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=checkpoint.checkpoint_index + 1,
            node_name=state.current_node,
            state_json=state.to_checkpoint(),
        ))

    async def _pending_action(
        self, run: WorkflowRun, project_id: UUID, chapter_id: UUID, action_request_id: UUID
    ) -> ActionRequest:
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_request_id, ActionRequest.workflow_run_id == run.id,
                ActionRequest.project_id == project_id, ActionRequest.chapter_id == chapter_id,
                ActionRequest.request_type == _AUTHOR_ACTION_TYPE,
            )
            .with_for_update()
        )
        pending_count = await self.session.scalar(
            select(func.count()).select_from(ActionRequest).where(
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.status == ActionRequestStatus.PENDING.value,
            )
        )
        if (
            action is None
            or action.status != ActionRequestStatus.PENDING.value
            or pending_count != 1
            or action.user_decision is not None
            or action.user_feedback is not None
            or action.resolved_by_id is not None
            or action.resolved_at is not None
        ):
            raise _invalid()
        return action

    async def _current_revision(
        self, project_id: UUID, chapter_id: UUID, document_id: UUID, version_id: UUID, content_hash: str
    ) -> tuple[Document | None, DocumentVersion | None]:
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == document_id, Document.project_id == project_id,
                Document.chapter_id == chapter_id, Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id == version_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id,
                  DocumentVersion.content_hash == content_hash)
            .with_for_update()
        )
        return document, version

    async def _stale_adoption(
        self, *, scope: _Scope, run: WorkflowRun, chapter: Chapter, checkpoint: WorkflowCheckpoint,
        state: ChapterProductionState, action: ActionRequest, document_id: UUID, version_id: UUID,
        metadata: dict[str, str],
    ) -> None:
        stale_document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == document_id, Document.project_id == scope.project_id,
                Document.chapter_id == scope.chapter_id, Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id.is_not(None), Document.current_version_id != version_id,
            )
            .with_for_update()
        )
        stale_version = None
        if stale_document is not None:
            stale_version = await self.session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.id == stale_document.current_version_id,
                      DocumentVersion.document_id == document_id,
                      DocumentVersion.parent_version_id == version_id)
                .with_for_update()
            )
        if not (
            stale_document is not None
            and stale_version is not None
            and chapter.current_draft_document_id == stale_document.id
            and stale_version.source == DocumentSource.USER.value
            and stale_version.actor_user_id is not None
            and str(stale_version.actor_user_id) == str(scope.actor_user_id)
            and stale_version.agent_role is None
            and stale_version.workflow_run_id is None
        ):
            return
        await self.documents.derive_chapter_segment_map(
            project_id=scope.project_id, chapter_id=scope.chapter_id,
            document_id=stale_document.id, version_id=stale_version.id,
        )
        binding = _author_binding(
            action, run, chapter, document_id, version_id, metadata["content_hash"],
            stale_document.id, stale_version.id, stale_version.content_hash,
        )
        adopted = state.reconcile_stale_action(action=binding)
        self.service._resolve_action_row(  # type: ignore[attr-defined]
            action,
            status=ActionRequestStatus.CANCELLED,
            decision=ChapterActionDecision.CANCEL,
            actor_user_id=scope.actor_user_id,
        )
        self._append_state(run, checkpoint, adopted)
        await _commit(self.session)
        raise _StaleActionAdopted(ChapterProductionV2Updated(
            workflow_run_id=run.id,
            draft_document_id=stale_document.id,
            draft_version_id=stale_version.id,
            action_request_id=None,
        ))

    async def author_context(self, *, scope: _Scope) -> _ClaimContext:
        await self._require_project_owner(scope.project_id, scope.actor_user_id)
        chapter = await self._chapter(scope.project_id, scope.chapter_id)
        run = await self._run(scope.project_id, scope.chapter_id, scope.workflow_run_id)
        state, checkpoint = await self._locked_state(run)
        if (
            state.status is not ChapterProductionStatus.AUTHOR_REVISION
            or not state.awaiting_user
            or state.action_request_id != str(scope.action_request_id)
            or state.action_kind is not ChapterActionKind.AUTHOR_REVISION
        ):
            raise _invalid()
        action = await self._pending_action(
            run, scope.project_id, scope.chapter_id, scope.action_request_id
        )
        metadata = self.service._action_metadata(action)  # type: ignore[attr-defined]
        document_id = UUID(metadata["document_id"])
        version_id = UUID(metadata["document_version_id"])
        document, version = await self._current_revision(
            scope.project_id, scope.chapter_id, document_id, version_id, metadata["content_hash"]
        )
        if document is None:
            await self._stale_adoption(
                scope=scope, run=run, chapter=chapter, checkpoint=checkpoint, state=state,
                action=action, document_id=document_id, version_id=version_id, metadata=metadata,
            )
        if (
            document is None
            or version is None
            or chapter.current_draft_document_id != document.id
            or state.document_id != str(document.id)
            or state.document_version_id != str(version.id)
            or state.content_hash != version.content_hash
        ):
            raise _invalid()
        await self.documents.derive_chapter_segment_map(
            project_id=scope.project_id, chapter_id=scope.chapter_id,
            document_id=document.id, version_id=version.id,
        )
        binding = _author_binding(
            action, run, chapter, document.id, version.id, version.content_hash,
            document.id, version.id, version.content_hash,
        )
        return _ClaimContext(run, state, checkpoint, action, binding, document, version)


class FeedbackRevisionHandoff:
    """Claim the feedback gate and hand off a pure provider snapshot to Phase 3."""

    def __init__(
        self, service: object, phase_sessions: ChapterPhaseSessionLease | None,
        revision_agent: RevisionAgent | None,
    ) -> None:
        if (phase_sessions is not None and type(phase_sessions) is not ChapterPhaseSessionLease) or (
            revision_agent is not None and type(revision_agent) is not RevisionAgent
        ):
            raise _invalid() from None
        self.service = service
        self.phase_sessions = phase_sessions
        self.revision_agent = revision_agent

    async def execute(
        self, *, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID,
        action_request_id: UUID, actor_user_id: UUID, feedback: str,
        target_segment_ids: tuple[UUID, ...],
    ) -> FeedbackRevisionPlan:
        self.service._validated_ids(  # type: ignore[attr-defined]
            project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id
        )
        target_segment_ids = self.service._validated_uuid_sequence(  # type: ignore[attr-defined]
            target_segment_ids, maximum=64
        )
        feedback = self.service._validated_feedback(feedback)  # type: ignore[attr-defined]
        scope = _Scope(
            project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id,
            feedback, target_segment_ids, sha256_content(feedback),
        )
        await self.service._recover_failed_attempt(  # type: ignore[attr-defined]
            project_id=scope.project_id,
            chapter_id=scope.chapter_id,
            workflow_run_id=scope.workflow_run_id,
            actor_user_id=scope.actor_user_id,
            kind="feedback",
            action_request_id=scope.action_request_id,
            target_segment_ids=scope.target_segment_ids,
            feedback_hash=scope.feedback_hash,
            restore_feedback=True,
        )
        claim = await self._claim(scope)
        candidate = await self._provider(scope.workflow_run_id, claim)
        return FeedbackRevisionPlan(
            source_document_id=claim.source_document_id,
            source_version_id=claim.source_version_id,
            source_content_hash=claim.source_content_hash,
            operation_key=claim.operation_key,
            attempt_id=claim.attempt_id,
            attempt_checkpoint_index=claim.attempt_checkpoint_index,
            feedback=claim.feedback,
            target_segment_ids=claim.target_segment_ids,
            segment_map=claim.segment_map,
            candidate=candidate,
        )

    async def _provider(self, workflow_run_id: UUID, claim: _Claim) -> CandidateChapterOutput:
        cancellation: asyncio.CancelledError | None = None
        provider_failure: ChapterFailureCode | None = None
        try:
            return await self.revision_agent.user_feedback_revision(claim.request)
        except asyncio.CancelledError as error:
            cancellation = _safe_cancelled_error(error)
        except ProviderTimeoutError:
            provider_failure = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            provider_failure = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except Exception:
            provider_failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await self.service._release_attempt(  # type: ignore[attr-defined]
                workflow_run_id,
                expected_key=claim.operation_key,
                expected_attempt_id=claim.attempt_id,
                expected_kind="feedback",
                expected_checkpoint_index=claim.attempt_checkpoint_index,
                restore_feedback=True,
            )
            raise cancellation from None
        await self.service._fail_provider(  # type: ignore[attr-defined]
            workflow_run_id,
            provider_failure,
            expected_status=ChapterProductionStatus.DRAFTING,
            expected_checkpoint_index=claim.attempt_checkpoint_index,
            expected_attempt_key=claim.operation_key,
            expected_attempt_id=claim.attempt_id,
        )
        raise ChapterProductionV2ProviderError() from None

    async def _claim(self, scope: _Scope) -> _Claim:
        if self.phase_sessions is not None:
            async with self.phase_sessions.lease() as session:
                return await self._claim_in(session, scope)
        return await self._claim_in(self.service.session, scope)  # type: ignore[attr-defined]

    async def _claim_in(self, session: AsyncSession, scope: _Scope) -> _Claim:
        try:
            return await self._claim_locked(session, scope)
        except _StaleActionAdopted:
            raise
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ProviderError:
            await _rollback(session)
            raise
        except ChapterProductionV2ValidationError:
            await _rollback(session)
            raise
        except Exception:
            await _rollback(session)
            raise _invalid() from None

    async def _claim_locked(self, session: AsyncSession, scope: _Scope) -> _Claim:
        phase = _FeedbackClaimPhase(session, self.service)
        context = await phase.author_context(scope=scope)
        if self.revision_agent is None:
            raise ChapterProductionV2ProviderError() from None
        database_now = await session.scalar(select(func.clock_timestamp()))
        if _expiry_precludes_resolution(context.action.expires_at, database_now):
            raise _invalid()
        segment_map = await phase.documents.derive_chapter_segment_map(
            project_id=scope.project_id,
            chapter_id=scope.chapter_id,
            document_id=context.document.id,
            version_id=context.version.id,
        )
        request = self.service._feedback_request(  # type: ignore[attr-defined]
            context=context,
            project_id=scope.project_id,
            chapter_id=scope.chapter_id,
            feedback=scope.feedback,
            target_segment_ids=scope.target_segment_ids,
            segment_map=segment_map,
        )
        next_state = context.state.resolve_action(
            action=context.binding, decision=ChapterActionDecision.REQUEST_REVISION
        )
        operation_key = self.service._decision_operation_key(  # type: ignore[attr-defined]
            scope.workflow_run_id, scope.action_request_id, context.version.id, "feedback",
            target_segment_ids=scope.target_segment_ids, feedback_hash=scope.feedback_hash,
        )
        attempt_id = _new_attempt_id()
        self.service._resolve_action_row(  # type: ignore[attr-defined]
            context.action,
            status=ActionRequestStatus.REVISED,
            decision=ChapterActionDecision.REQUEST_REVISION,
            actor_user_id=scope.actor_user_id,
            feedback=scope.feedback,
        )
        phase._append_state(context.run, context.checkpoint, next_state)
        attempt_checkpoint_index = context.checkpoint.checkpoint_index + 1
        self.service._set_attempt(  # type: ignore[attr-defined]
            context.run,
            self.service._attempt_payload(  # type: ignore[attr-defined]
                attempt_id=attempt_id,
                key=operation_key,
                kind="feedback",
                checkpoint_index=attempt_checkpoint_index,
                source_document_id=context.document.id,
                source_version_id=context.version.id,
                action_request_id=scope.action_request_id,
                target_segment_ids=scope.target_segment_ids,
                feedback_hash=scope.feedback_hash,
            ),
        )
        await _commit(session)
        return _Claim(
            source_document_id=context.document.id,
            source_version_id=context.version.id,
            source_content_hash=context.version.content_hash,
            operation_key=operation_key,
            attempt_id=attempt_id,
            attempt_checkpoint_index=attempt_checkpoint_index,
            feedback=scope.feedback,
            target_segment_ids=scope.target_segment_ids,
            segment_map=segment_map,
            request=request,
        )


__all__ = ["FeedbackRevisionHandoff", "FeedbackRevisionPlan"]

