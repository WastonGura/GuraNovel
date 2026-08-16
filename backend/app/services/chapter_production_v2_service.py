"""Content-safe primitives for Chapter Production V2 orchestration.

The database orchestrator is deliberately added separately.  These helpers keep
candidate composition and immutable-version range replacement deterministic and
independent of provider or persistence authority.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    ChiefEditorChapterFinalAgent,
    EditorAgent,
    LoreChapterFinalAgent,
    RevisionAgent,
    ReviewDrivenRevisionRequest,
    ReviewReportReference,
    SourceDraftReference,
    SourceDraftSegment,
    UserFeedbackReference,
    UserFeedbackRevisionRequest,
    WriterAgent,
)
from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    MAX_CHAPTER_CONTENT_BYTES,
    ChapterSegmentMap,
    ChapterSegmentError,
    derive_chapter_segment_map,
    normalize_chapter_content,
)
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    ReviewMode,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_production_repository import (
    ChapterProductionRepository,
    _ChapterProductionRepositoryReconciliationError,
    _ChapterProductionRepositoryValidationError,
)
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    CONTRACT_VERSION as _CONTRACT_VERSION,
    REVIEWER_CLAIM_STATUS_CLAIMED as _REVIEWER_CLAIM_STATUS_CLAIMED,
    REVIEWER_CLAIM_STATUS_FAILED as _REVIEWER_CLAIM_STATUS_FAILED,
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Finalized,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ReviewProviderError,
    ChapterProductionV2Started,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
    new_attempt_id,
    safe_cancelled_error,
    valid_nonzero_uuid as _valid_nonzero_uuid,
    valid_sha256 as _valid_sha256,
)
from app.services.document_service import DocumentService
from app.services.author_accept_coordination import (
    AuthorAcceptCoordinator,
    _StaleActionAdopted,
)
from app.services.chapter_draft_revision_coordinator import (
    ChapterDraftRevisionCoordinator,
)
from app.services.chapter_finalization_saga import ChapterFinalizationSaga
from app.services.chapter_review_coordinator import (
    ChapterReviewCoordinator,
)
from app.services.chapter_review_validation import (
    validated_persisted_review_report,
    validated_resolved_review_action,
)
from app.services.initial_draft_lifecycle import (
    InitialCandidateNotApplicable,
    InitialDraftLifecycle,
    InitialRecoveryRoute,
)
from app.services.manual_edit_saga import (
    ManualEditCoordinator,
    _resolved_source_action,
)
from app.services.feedback_revision_handoff import FeedbackRevisionHandoff
from app.services.review_revision_handoff import (
    ReviewRevisionHandoff,
    ReviewRevisionPlan,
)
from app.services.feedback_candidate_saga import (
    FeedbackCandidateSaga,
    _restore_feedback_without_write,
)
from app.services.review_revision_saga import (
    ReviewRevisionSaga,
    _reconciliation_candidates,
)
from app.services.revision_readiness_store import (
    RevisionReadyPair,
    RevisionReadinessStore,
    _ReviewStateReferences,
)
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
    ChapterReviewBinding,
    ChapterReviewPolicyBinding,
    ChapterReviewStage,
)
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path


_REVIEW_POLICY_VERSION = "chapter-quality-v1"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"
_ATTEMPT_STATUS_CLAIMED = "claimed"
_ATTEMPT_STATUS_FAILED = "failed"


def _new_attempt_id() -> str:
    return new_attempt_id()


def _safe_cancelled_error(error: BaseException) -> asyncio.CancelledError:
    return safe_cancelled_error(error)


@dataclass(frozen=True, slots=True)
class _AuthorContext:
    run: WorkflowRun
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    action: ActionRequest
    binding: ChapterActionBinding
    document: Document
    version: DocumentVersion


@dataclass(frozen=True, slots=True)
class _ReviewRevisionContext:
    run: WorkflowRun
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    document: Document
    version: DocumentVersion
    segment_map: ChapterSegmentMap
    reports: tuple[ReviewReport, ...]


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _validated_prospective_map(
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content: str,
) -> ChapterSegmentMap:
    """Derive the complete #113 map before granting any canonical write."""

    try:
        project_id, chapter_id, document_id, version_id = (
            UUID(str(value)) for value in (project_id, chapter_id, document_id, version_id)
        )
        segment_map = derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            content=content,
            segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION,
        )
        segment_map.canonical_bytes()
        return segment_map
    except (ChapterSegmentError, UnicodeError, ValueError, TypeError, AttributeError):
        raise _invalid() from None


def _review_report_slots(
    *,
    editor_report_id: UUID | None,
    chief_editor_report_id: UUID | None,
    lore_report_id: UUID | None,
) -> tuple[tuple[UUID, str, str], ...]:
    slots = (
        (editor_report_id, ReviewMode.CHAPTER_EDITOR.value, "editor_agent"),
        (
            chief_editor_report_id,
            ReviewMode.CHAPTER_CHIEF_FINAL.value,
            "chief_editor_agent",
        ),
        (lore_report_id, ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent"),
    )
    return tuple((report_id, mode, role) for report_id, mode, role in slots if report_id)


def compose_initial_markdown(segments: Sequence[str]) -> str:
    """Compose a complete candidate with one canonical separator and final LF."""

    if type(segments) not in (tuple, list) or not 1 <= len(segments) <= 64:
        raise _invalid() from None
    normalized: list[str] = []
    try:
        for segment in segments:
            if type(segment) is not str or segment != segment.strip():
                raise _invalid()
            value = normalize_chapter_content(segment)
            if not value:
                raise _invalid()
            normalized.append(value)
        result = "\n\n".join(normalized) + "\n"
        result = normalize_chapter_content(result)
    except ChapterProductionV2ValidationError:
        raise
    except Exception:
        raise _invalid() from None
    if len(result.encode("utf-8")) > MAX_CHAPTER_CONTENT_BYTES:
        raise _invalid() from None
    return result


def merge_segment_replacements(
    source: str,
    segment_map: ChapterSegmentMap,
    replacements: Mapping[UUID, str],
) -> str:
    """Replace exact locator byte ranges while preserving every untouched byte."""

    if (
        type(source) is not str
        or type(replacements) is not dict
        or not 1 <= len(replacements) <= 64
    ):
        raise _invalid() from None
    try:
        normalized_source = normalize_chapter_content(source)
        if normalized_source != source:
            raise _invalid()
        authoritative = derive_chapter_segment_map(
            project_id=segment_map.project_id,
            chapter_id=segment_map.chapter_id,
            document_id=segment_map.document_id,
            version_id=segment_map.version_id,
            content=source,
            segmenter_version=segment_map.segmenter_version,
        )
        if authoritative.canonical_bytes() != segment_map.canonical_bytes():
            raise _invalid()

        by_id = {item.segment_id: item for item in authoritative.segments}
        if len(by_id) != len(authoritative.segments) or any(
            type(segment_id) is not UUID or segment_id not in by_id for segment_id in replacements
        ):
            raise _invalid()

        safe_replacements: dict[UUID, bytes] = {}
        for segment_id, content in replacements.items():
            if type(content) is not str or content != content.strip():
                raise _invalid()
            normalized = normalize_chapter_content(content)
            if not normalized:
                raise _invalid()
            safe_replacements[segment_id] = normalized.encode("utf-8")

        source_bytes = source.encode("utf-8")
        output = bytearray()
        cursor = 0
        for segment in authoritative.segments:
            replacement = safe_replacements.get(segment.segment_id)
            if replacement is None:
                continue
            output.extend(source_bytes[cursor : segment.start_byte])
            output.extend(replacement)
            cursor = segment.end_byte
        output.extend(source_bytes[cursor:])
        if len(output) > MAX_CHAPTER_CONTENT_BYTES:
            raise _invalid()
        result = output.decode("utf-8")
        normalize_chapter_content(result)
    except ChapterProductionV2ValidationError:
        raise
    except Exception:
        raise _invalid() from None
    return result


class ChapterProductionV2Service:
    """Additive V2 draft orchestrator; legacy chapter production is untouched."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        writer_agent: WriterAgent,
        revision_agent: RevisionAgent | None = None,
        editor_agent: EditorAgent | None = None,
        chief_editor_agent: ChiefEditorChapterFinalAgent | None = None,
        lore_agent: LoreChapterFinalAgent | None = None,
        chief_editor_required: bool = True,
        phase_session_source: ChapterPhaseSessionSource | None = None,
    ) -> None:
        if type(chief_editor_required) is not bool:
            raise _invalid() from None
        self.session = session
        self.writer_agent = writer_agent
        self.revision_agent = revision_agent
        self.editor_agent = editor_agent
        self.chief_editor_agent = chief_editor_agent
        self.lore_agent = lore_agent
        self.chief_editor_required = chief_editor_required
        self._phase_sessions = (
            ChapterPhaseSessionLease(phase_session_source)
            if phase_session_source is not None
            else None
        )
        self._initial_drafts = None if self._phase_sessions is None else InitialDraftLifecycle(self._phase_sessions, writer_agent, chief_editor_required)
        self._author_accept = AuthorAcceptCoordinator(self)
        self._manual_edit = ManualEditCoordinator(self)
        self._feedback_handoff = FeedbackRevisionHandoff(self, self._phase_sessions, self.revision_agent)
        self._review_handoff = ReviewRevisionHandoff(self, self.revision_agent)
        self._feedback_saga = FeedbackCandidateSaga(
            self, merge_segment_replacements, _validated_prospective_map
        )
        self._review_saga = ReviewRevisionSaga(
            self, merge_segment_replacements, _validated_prospective_map
        )
        self._draft_revision = ChapterDraftRevisionCoordinator(
            self,
            feedback_saga=self._feedback_saga,
            review_saga=self._review_saga,
            manual_edit=self._manual_edit,
        )
        self._review_coordinator = ChapterReviewCoordinator(self)
        self.documents = DocumentService(session)
        self._readiness = RevisionReadinessStore(self)
        self.repository = ChapterProductionRepository(
            session,
            contract_version=_CONTRACT_VERSION,
            inactive_run_statuses=frozenset(
                {
                    ChapterProductionStatus.COMPLETED.value,
                    ChapterProductionStatus.CANCELLED.value,
                }
            ),
        )
        self._finalization = ChapterFinalizationSaga(self)

    async def start_from_approved_outline(
        self,
        project_id: UUID,
        chapter_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Started:
        """Create or resume the one V2 draft operation for the approved outline."""
        if self._initial_drafts is None:
            raise _invalid() from None
        return await self._initial_drafts.start(project_id, chapter_id, actor_user_id=actor_user_id)

    async def resume_drafting(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Started:
        """Resume an exact V2 draft without duplicating committed artifacts."""
        if self._initial_drafts is None:
            raise _invalid() from None
        return await self._initial_drafts.resume(project_id, chapter_id, workflow_run_id, actor_user_id=actor_user_id)

    async def resolve_author_action(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        actor_user_id: UUID,
        decision: str,
    ) -> ChapterProductionV2Updated:
        """Accept the exact current author gate and enter Editor review."""

        self._validated_ids(
            project_id,
            chapter_id,
            workflow_run_id,
            action_request_id,
            actor_user_id,
        )
        if decision != ChapterActionDecision.ACCEPT.value:
            raise _invalid() from None
        try:
            return await self._author_accept.accept(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

    async def request_user_feedback_revision(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        actor_user_id: UUID,
        feedback: str,
        target_segment_ids: Sequence[UUID],
    ) -> ChapterProductionV2Updated:
        """Resolve one author gate and propose a bounded locator-scoped revision."""

        self._validated_ids(
            project_id,
            chapter_id,
            workflow_run_id,
            action_request_id,
            actor_user_id,
        )
        target_segment_ids = self._validated_uuid_sequence(target_segment_ids, maximum=64)
        if type(feedback) is not str or len(feedback) > 8000:
            raise _invalid() from None
        try:
            plan = await self._feedback_handoff.execute(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
                feedback=feedback,
                target_segment_ids=target_segment_ids,
            )
            identity = await self._feedback_saga.persist(
                plan,
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
        except _StaleActionAdopted as adopted:
            return adopted.result
        return await self._feedback_saga.finalize(identity, actor_user_id=actor_user_id)

    async def submit_manual_edit(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        actor_user_id: UUID,
        content: str,
    ) -> ChapterProductionV2Updated:
        """Persist an authorized user edit as a new immutable current version."""

        self._validated_ids(
            project_id,
            chapter_id,
            workflow_run_id,
            action_request_id,
            actor_user_id,
        )
        if type(content) is not str or len(content) > MAX_CHAPTER_CONTENT_BYTES:
            raise _invalid() from None
        try:
            return await self._manual_edit.submit(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
                content=content,
            )
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

    async def execute_review_revision(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
        report_ids: Sequence[UUID],
        target_segment_ids: Sequence[UUID],
    ) -> ChapterProductionV2Updated:
        """Consume exact persisted review refs without running or creating reviews."""

        self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        report_ids = self._validated_uuid_sequence(report_ids, maximum=16)
        target_segment_ids = self._validated_uuid_sequence(target_segment_ids, maximum=64)
        plan = await self._review_handoff.execute(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            report_ids=report_ids,
            target_segment_ids=target_segment_ids,
            actor_user_id=actor_user_id,
        )
        return await self._persist_review_revision(
            plan,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )

    async def _persist_review_revision(
        self,
        plan: ReviewRevisionPlan,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        """Persist the claimed provider result and finalize; the claim committed already."""

        identity = await self._review_saga.persist(
            plan,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )
        return await self._review_saga.finalize(identity, actor_user_id=actor_user_id)

    async def execute_current_review(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        """Run exactly the server-selected reviewer for the locked current version."""

        return await self._review_coordinator.execute_review(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )

    async def resolve_review_action(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        actor_user_id: UUID,
        decision: str,
    ) -> ChapterProductionV2Updated:
        """Resolve one exact version-bound review warning or required revision."""

        return await self._review_coordinator.resolve_action(
            project_id,
            chapter_id,
            workflow_run_id,
            action_request_id,
            actor_user_id=actor_user_id,
            decision=decision,
        )

    async def acknowledge_reviewer_no_write(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
        expected_operation_key: str,
        expected_claim_id: str,
    ) -> ChapterProductionState:
        """Release a durable reviewer claim after an operator proves no report was written."""

        return await self._review_coordinator.acknowledge_no_write(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            expected_operation_key=expected_operation_key,
            expected_claim_id=expected_claim_id,
        )

    async def finalize_without_reader_panel(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Finalized:
        """Promote the exact ready version through a restart-safe DB/filesystem saga."""

        return await self._finalization.finalize(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )

    async def load_state(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        """Load only the exact latest V2 checkpoint and validate its run projection."""

        try:
            self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
            await self._require_project_owner(project_id, actor_user_id)
            await self._chapter(project_id, chapter_id, lock=False)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=False)
            state, checkpoint = await self._locked_state(run)
            await self.session.commit()
            return state
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

    async def reconcile_indeterminate(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        """Reconcile one exact committed child without invoking a provider."""
        if self._initial_drafts is None:
            raise _invalid() from None
        initial = await self._initial_drafts.reconcile(
            project_id, chapter_id, workflow_run_id, actor_user_id=actor_user_id
        )
        if initial is not InitialRecoveryRoute.LEGACY:
            return initial
        try:
            result = await self._draft_revision.reconcile(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=actor_user_id,
            )
            if result is not None:
                return result
            await self._rollback()
            return await self._reconcile_review_route(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=actor_user_id,
            )
        except ChapterProductionV2ReconciliationError:
            await self._rollback()
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
        except Exception:
            await self._rollback()
        raise ChapterProductionV2ReconciliationError() from None

    async def _reconcile_review_route(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        await self._require_project_owner(project_id, actor_user_id)
        await self._chapter(project_id, chapter_id, lock=True)
        run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, _ = await self._locked_state(run)
        if state.status is not ChapterProductionStatus.EDITOR_REVIEW:
            raise ChapterProductionV2ReconciliationError()
        candidates = await _reconciliation_candidates(
            self, run,
            parent_version_id=(
                UUID(state.document_version_id)
                if state.document_version_id is not None
                else None
            ),
        )
        if len(candidates) > 1 or candidates:
            raise ChapterProductionV2ReconciliationError()
        attempt = self._run_metadata(run)["provider_attempt"]
        if type(attempt) is dict and attempt.get("status") == _ATTEMPT_STATUS_CLAIMED:
            raise ChapterProductionV2ReconciliationError()
        if state.document_id is None:
            raise ChapterProductionV2ReconciliationError()
        canonical = await self.session.scalar(
            select(Document).where(
                Document.id == UUID(state.document_id),
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.current_version_id == UUID(state.document_version_id),
            ).with_for_update()
        )
        if canonical is None:
            raise ChapterProductionV2ReconciliationError()
        await self.session.commit()
        return state

    async def acknowledge_provider_no_write(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
        expected_attempt_key: str,
        expected_attempt_id: str,
    ) -> ChapterProductionState:
        """Authorize retry after an operator verifies a claimed attempt wrote nothing."""
        self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        if (
            self._initial_drafts is None
            or not _valid_sha256(expected_attempt_key)
            or not _valid_nonzero_uuid(expected_attempt_id)
        ):
            raise _invalid() from None
        try:
            return await self._initial_drafts.acknowledge_no_write(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=actor_user_id,
                operation_key=expected_attempt_key,
                attempt_id=UUID(expected_attempt_id),
            )
        except InitialCandidateNotApplicable:
            pass
        try:
            return await self._draft_revision.acknowledge_no_write(
                project_id,
                chapter_id,
                workflow_run_id,
                actor_user_id=actor_user_id,
                expected_attempt_key=expected_attempt_key,
                expected_attempt_id=expected_attempt_id,
            )
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ReconciliationError:
            await self._rollback()
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

    async def _fail_provider(
        self,
        workflow_run_id: UUID,
        failure_code: ChapterFailureCode,
        *,
        expected_status: ChapterProductionStatus,
        expected_checkpoint_index: int,
        expected_attempt_key: str,
        expected_attempt_id: str,
    ) -> bool:
        await self._rollback()
        run = await self.session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_run_id).with_for_update()
        )
        if run is None:
            return False
        state, checkpoint = await self._locked_state(run)
        metadata = self._run_metadata(run)
        attempt = metadata["provider_attempt"]
        if (
            state.status is not expected_status
            or checkpoint.checkpoint_index != expected_checkpoint_index
            or type(attempt) is not dict
            or attempt.get("key") != expected_attempt_key
            or attempt.get("attempt_id") != expected_attempt_id
            or attempt.get("checkpoint_index") != expected_checkpoint_index
            or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
        ):
            await self.session.commit()
            return False
        failed = state.fail(failure_code)
        failed_attempt = dict(attempt)
        failed_attempt["status"] = _ATTEMPT_STATUS_FAILED
        self._set_attempt(run, failed_attempt)
        self._append_state(run, checkpoint, failed)
        await self._commit()
        return True

    async def _review_revision_context(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        report_ids: Sequence[UUID],
        actor_user_id: UUID,
    ) -> _ReviewRevisionContext:
        await self._require_project_owner(project_id, actor_user_id)
        chapter = await self._chapter(project_id, chapter_id, lock=True)
        run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self._locked_state(run)
        report_slots = _review_report_slots(
            editor_report_id=(
                UUID(state.editor_report_id) if state.editor_report_id is not None else None
            ),
            chief_editor_report_id=(
                UUID(state.chief_editor_report_id)
                if state.chief_editor_report_id is not None
                else None
            ),
            lore_report_id=(
                UUID(state.lore_report_id) if state.lore_report_id is not None else None
            ),
        )
        expected_reports = tuple(item[0] for item in report_slots)
        if (
            state.status is not ChapterProductionStatus.REVIEW_REVISION
            or state.awaiting_user
            or state.action_request_id is not None
            or type(report_ids) not in (tuple, list)
            or tuple(report_ids) != expected_reports
            or not expected_reports
            or state.document_id is None
            or state.document_version_id is None
        ):
            raise _invalid()
        pending_count = await self.session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.status == ActionRequestStatus.PENDING.value,
            )
        )
        if pending_count != 0:
            raise _invalid()
        document_id = UUID(state.document_id)
        version_id = UUID(state.document_version_id)
        reports: list[ReviewReport] = []
        for report_id, expected_mode, expected_role in report_slots:
            report = await self.session.scalar(
                select(ReviewReport)
                .execution_options(populate_existing=True)
                .where(
                    ReviewReport.id == report_id,
                    ReviewReport.project_id == project_id,
                    ReviewReport.chapter_id == chapter_id,
                    ReviewReport.workflow_run_id == run.id,
                    ReviewReport.target_document_id == document_id,
                    ReviewReport.target_version_id == version_id,
                )
                .with_for_update()
            )
            if (
                report is None
                or report.review_mode != expected_mode
                or report.reviewer_agent_role != expected_role
            ):
                raise _invalid()
            reports.append(report)
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id == version_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.content_hash == state.content_hash,
            )
            .with_for_update()
        )
        if document is None or version is None or chapter.current_draft_document_id != document.id:
            raise _invalid()
        segment_map = await self.documents.derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
        )
        for report, (_, expected_mode, _) in zip(reports, report_slots, strict=True):
            stage = {
                ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
                ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
                ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
            }[expected_mode]
            await validated_persisted_review_report(
                self,
                row=report,
                run=run,
                document=document,
                version=version,
                stage=stage,
            )
        trigger_mode = report_slots[-1][1]
        await validated_resolved_review_action(
            self,
            run=run,
            document=document,
            version=version,
            report=reports[-1],
            stage={
                ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
                ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
                ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
            }[trigger_mode],
        )
        return _ReviewRevisionContext(
            run, state, checkpoint, document, version, segment_map, tuple(reports)
        )

    def _review_revision_request(
        self,
        *,
        context: _ReviewRevisionContext,
        project_id: UUID,
        chapter_id: UUID,
        target_segment_ids: Sequence[UUID],
    ) -> ReviewDrivenRevisionRequest:
        if type(target_segment_ids) not in (tuple, list):
            raise _invalid()
        selected = tuple(target_segment_ids)
        known_order = {item.segment_id: item.ordinal for item in context.segment_map.segments}
        if (
            not 1 <= len(selected) <= 64
            or len(selected) != len(set(selected))
            or any(type(item) is not UUID or item not in known_order for item in selected)
            or selected != tuple(sorted(selected, key=known_order.__getitem__))
            or len(context.segment_map.segments) > 64
        ):
            raise _invalid()
        metadata = self._run_metadata(context.run)
        try:
            source_segments = tuple(
                SourceDraftSegment(
                    segment_id=item.segment_id,
                    index=item.ordinal,
                    title=item.structural_path,
                    content=item.content,
                )
                for item in context.segment_map.segments
            )
            return ReviewDrivenRevisionRequest(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=context.run.id,
                approved_outline=ApprovedOutlineReference(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=UUID(metadata["outline_document_id"]),
                    version_id=UUID(metadata["outline_version_id"]),
                ),
                source_draft=SourceDraftReference(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=context.document.id,
                    version_id=context.version.id,
                    segments=source_segments,
                ),
                allowed_segments=tuple(
                    AllowedChapterSegment(
                        segment_id=item.segment_id,
                        index=item.ordinal,
                        title=item.structural_path,
                        brief=item.content,
                    )
                    for item in context.segment_map.segments
                ),
                target_segment_ids=selected,
                review_report_refs=tuple(
                    ReviewReportReference(
                        report_id=report.id,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=context.run.id,
                        target_draft_document_id=context.document.id,
                        target_draft_version_id=context.version.id,
                        summary=report.summary,
                    )
                    for report in context.reports
                ),
            )
        except Exception:
            raise _invalid() from None

    @staticmethod
    def _verified_snapshot_content(
        document: Document, version: DocumentVersion
    ) -> str:
        if (
            document.project is None
            or version.document_id != document.id
            or type(version.version_number) is not int
            or version.version_number < 1
            or version.file_path != document.path
            or version.snapshot_path
            != version_snapshot_path(str(document.id), version.version_number).as_posix()
            or type(version.byte_size) is not int
            or version.byte_size < 0
            or version.byte_size > MAX_CHAPTER_CONTENT_BYTES
            or not _valid_sha256(version.content_hash)
        ):
            raise _invalid()
        try:
            content = MarkdownStore(Path(document.project.workspace_root)).read_bounded(
                version.snapshot_path,
                max_bytes=MAX_CHAPTER_CONTENT_BYTES,
            )
        except Exception:
            raise _invalid() from None
        if (
            len(content.encode("utf-8")) != version.byte_size
            or sha256_content(content) != version.content_hash
        ):
            raise _invalid()
        return content

    async def _locked_review_document(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        state: ChapterProductionState,
        chapter: Chapter,
    ) -> tuple[Document, DocumentVersion]:
        if state.document_id is None or state.document_version_id is None:
            raise _invalid()
        document_id = UUID(state.document_id)
        version_id = UUID(state.document_version_id)
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .execution_options(populate_existing=True)
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id == version_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .execution_options(populate_existing=True)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.content_hash == state.content_hash,
            )
            .with_for_update()
        )
        if (
            document is None
            or version is None
            or chapter.current_draft_document_id != document.id
        ):
            raise _invalid()
        return document, version

    async def _exact_review_report_count(
        self, *, run: WorkflowRun, version: DocumentVersion, stage: ChapterReviewStage
    ) -> int:
        mode, role = {
            ChapterReviewStage.EDITOR: (ReviewMode.CHAPTER_EDITOR.value, "editor_agent"),
            ChapterReviewStage.CHIEF_EDITOR: (
                ReviewMode.CHAPTER_CHIEF_FINAL.value,
                "chief_editor_agent",
            ),
            ChapterReviewStage.LORE: (ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent"),
        }[stage]
        count = await self.session.scalar(
            select(func.count())
            .select_from(ReviewReport)
            .where(
                ReviewReport.project_id == run.project_id,
                ReviewReport.chapter_id == run.chapter_id,
                ReviewReport.workflow_run_id == run.id,
                ReviewReport.target_document_id == version.document_id,
                ReviewReport.target_version_id == version.id,
                ReviewReport.review_mode == mode,
                ReviewReport.reviewer_agent_role == role,
            )
        )
        return int(count or 0)

    async def _live_review_bindings_locked(
        self,
        *,
        run: WorkflowRun,
        state: ChapterProductionState | _ReviewStateReferences,
        document: Document,
        version: DocumentVersion,
    ) -> tuple[
        ChapterReviewPolicyBinding,
        ChapterReviewBinding,
        ChapterReviewBinding | None,
        ChapterReviewBinding,
    ]:
        return await self._readiness.live_review_bindings_locked(
            run=run,
            state=state,
            document=document,
            version=version,
        )

    async def _enter_revision_ready_locked(
        self,
        *,
        run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        state: ChapterProductionState,
        document: Document,
        version: DocumentVersion,
    ) -> ChapterProductionState:
        return await self._readiness.enter(
            run=run,
            checkpoint=checkpoint,
            state=state,
            document=document,
            version=version,
        )

    async def _validated_ready_pairs_locked(
        self, run: WorkflowRun
    ) -> tuple[RevisionReadyPair, ...]:
        return await self._readiness.validated_pairs(run)

    async def _restore_ready_marker_locked(
        self, run: WorkflowRun, checkpoint: WorkflowCheckpoint
    ) -> ChapterProductionState:
        return await self._readiness.restore_marker(run, checkpoint)

    async def _locked_current_document_version(
        self, document: Document
    ) -> DocumentVersion:
        if not isinstance(document, Document):
            raise ChapterProductionV2ReconciliationError()
        try:
            result = await self.repository.locked_current_document_version(
                project_id=document.project_id,
                chapter_id=document.chapter_id,
                document_id=document.id,
                expected_document_type=DocumentType.CHAPTER_FINAL,
            )
        except _ChapterProductionRepositoryReconciliationError:
            pass
        else:
            return result
        raise ChapterProductionV2ReconciliationError()

    @staticmethod
    def _review_operation_key(
        *,
        workflow_run_id: UUID,
        source_version_id: UUID,
        report_ids: tuple[UUID, ...],
        target_segment_ids: tuple[UUID, ...],
        report_input_hash: str,
    ) -> str:
        return sha256_content(
            ":".join(
                (
                    _CONTRACT_VERSION,
                    str(workflow_run_id),
                    str(source_version_id),
                    *(str(item) for item in report_ids),
                    "targets",
                    *(str(item) for item in target_segment_ids),
                    "report-input",
                    report_input_hash,
                )
            )
        )

    @staticmethod
    def _attempt_payload(
        *,
        attempt_id: str,
        key: str,
        kind: str,
        checkpoint_index: int,
        source_document_id: UUID | None = None,
        source_version_id: UUID | None = None,
        action_request_id: UUID | None = None,
        target_segment_ids: Sequence[UUID] = (),
        feedback_hash: str | None = None,
        report_ids: Sequence[UUID] = (),
        report_input_hash: str | None = None,
        status: str = _ATTEMPT_STATUS_CLAIMED,
    ) -> dict[str, object]:
        return {
            "attempt_id": attempt_id,
            "key": key,
            "kind": kind,
            "checkpoint_index": checkpoint_index,
            "source_document_id": (
                str(source_document_id) if source_document_id is not None else None
            ),
            "source_version_id": str(source_version_id) if source_version_id is not None else None,
            "action_request_id": (
                str(action_request_id) if action_request_id is not None else None
            ),
            "target_segment_ids": [str(item) for item in target_segment_ids],
            "feedback_hash": feedback_hash,
            "report_ids": [str(item) for item in report_ids],
            "report_input_hash": report_input_hash,
            "status": status,
        }

    @staticmethod
    def _review_report_input_hash(reports: Sequence[ReviewReport]) -> str:
        payload = [
            {
                "id": str(report.id),
                "project_id": str(report.project_id),
                "chapter_id": str(report.chapter_id),
                "workflow_run_id": str(report.workflow_run_id),
                "review_mode": report.review_mode,
                "reviewer_agent_role": report.reviewer_agent_role,
                "target_document_id": str(report.target_document_id),
                "target_version_id": str(report.target_version_id),
                "passed": report.passed,
                "summary": report.summary,
                "blocking_issues": report.blocking_issues,
                "warnings": report.warnings,
                "notes": report.notes,
                "suggested_actions": report.suggested_actions,
                "raw_report": report.raw_report,
            }
            for report in reports
        ]
        return sha256_content(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )

    @staticmethod
    def _set_attempt(run: WorkflowRun, attempt: dict[str, object] | None) -> None:
        metadata = dict(run.metadata_)
        metadata["provider_attempt"] = attempt
        run.metadata_ = metadata
    async def _recover_failed_attempt(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
        kind: str,
        action_request_id: UUID | None = None,
        target_segment_ids: Sequence[UUID] = (),
        feedback_hash: str | None = None,
        report_ids: Sequence[UUID] = (),
        restore_feedback: bool = False,
    ) -> None:
        await self._require_project_owner(project_id, actor_user_id)
        await self._chapter(project_id, chapter_id, lock=True)
        run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self._locked_state(run)
        if state.status is not ChapterProductionStatus.FAILED:
            await self.session.commit()
            return
        metadata = self._run_metadata(run)
        attempt = metadata["provider_attempt"]
        expected = {
            "kind": kind,
            "action_request_id": (
                str(action_request_id) if action_request_id is not None else None
            ),
            "target_segment_ids": [str(item) for item in target_segment_ids],
            "feedback_hash": feedback_hash,
            "report_ids": [str(item) for item in report_ids],
            "status": _ATTEMPT_STATUS_FAILED,
        }
        attempt_checkpoint_index = (
            attempt.get("checkpoint_index") if type(attempt) is dict else None
        )
        expected_failed_from = (
            ChapterProductionStatus.DRAFTING
            if kind == "feedback"
            else ChapterProductionStatus.REVIEW_REVISION
        )
        if (
            state.failure_code
            not in {
                ChapterFailureCode.PROVIDER_UNAVAILABLE,
                ChapterFailureCode.PROVIDER_TIMEOUT,
                ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
            }
            or type(attempt) is not dict
            or any(attempt.get(key) != value for key, value in expected.items())
            or state.failed_from_status is not expected_failed_from
            or type(attempt_checkpoint_index) is not int
            or checkpoint.checkpoint_index != attempt_checkpoint_index + 1
            or attempt.get("source_document_id") != state.document_id
            or attempt.get("source_version_id") != state.document_version_id
        ):
            raise ChapterProductionV2ReconciliationError()
        if kind == "feedback":
            action = await self.session.scalar(
                select(ActionRequest)
                .where(
                    ActionRequest.id == action_request_id,
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.project_id == project_id,
                    ActionRequest.chapter_id == chapter_id,
                    ActionRequest.status == ActionRequestStatus.REVISED.value,
                    ActionRequest.user_decision == ChapterActionDecision.REQUEST_REVISION.value,
                    ActionRequest.resolved_by_id == actor_user_id,
                )
                .with_for_update()
            )
            if (
                action is None
                or sha256_content(action.user_feedback or "") != feedback_hash
                or action.metadata_.get("document_id") != state.document_id
                or action.metadata_.get("document_version_id") != state.document_version_id
            ):
                raise ChapterProductionV2ReconciliationError()
            if (await _resolved_source_action(self, run.id, state)).id != action.id:
                raise ChapterProductionV2ReconciliationError()
        elif kind == "review":
            report_slots = _review_report_slots(
                editor_report_id=(
                    UUID(state.editor_report_id) if state.editor_report_id is not None else None
                ),
                chief_editor_report_id=(
                    UUID(state.chief_editor_report_id)
                    if state.chief_editor_report_id is not None
                    else None
                ),
                lore_report_id=(
                    UUID(state.lore_report_id) if state.lore_report_id is not None else None
                ),
            )
            if tuple(item[0] for item in report_slots) != tuple(report_ids):
                raise ChapterProductionV2ReconciliationError()
            reports: list[ReviewReport] = []
            for report_id, expected_mode, expected_role in report_slots:
                report = await self.session.scalar(
                    select(ReviewReport)
                    .execution_options(populate_existing=True)
                    .where(
                        ReviewReport.id == report_id,
                        ReviewReport.project_id == project_id,
                        ReviewReport.chapter_id == chapter_id,
                        ReviewReport.workflow_run_id == run.id,
                        ReviewReport.target_document_id == UUID(state.document_id),
                        ReviewReport.target_version_id == UUID(state.document_version_id),
                        ReviewReport.review_mode == expected_mode,
                        ReviewReport.reviewer_agent_role == expected_role,
                    )
                    .with_for_update()
                )
                if report is None:
                    raise ChapterProductionV2ReconciliationError()
                reports.append(report)
            if self._review_report_input_hash(reports) != attempt.get("report_input_hash"):
                raise ChapterProductionV2ReconciliationError()
        recovered = state.recover()
        self._append_state(run, checkpoint, recovered)
        self._set_attempt(run, None)
        if restore_feedback:
            if attempt_checkpoint_index < 1:
                raise ChapterProductionV2ReconciliationError()
            await _restore_feedback_without_write(self,
                run,
                recovered,
                source_checkpoint_index=attempt_checkpoint_index - 1,
            )
        await self._commit()

    async def _release_attempt(
        self,
        workflow_run_id: UUID,
        *,
        expected_key: str,
        expected_attempt_id: str,
        expected_kind: str,
        expected_checkpoint_index: int,
        restore_feedback: bool = False,
    ) -> None:
        await self._rollback()
        run = await self.session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_run_id).with_for_update()
        )
        if run is None:
            return
        metadata = self._run_metadata(run)
        attempt = metadata["provider_attempt"]
        if (
            type(attempt) is not dict
            or attempt.get("key") != expected_key
            or attempt.get("attempt_id") != expected_attempt_id
            or attempt.get("kind") != expected_kind
            or attempt.get("checkpoint_index") != expected_checkpoint_index
            or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
        ):
            await self.session.commit()
            return
        _, checkpoint = await self._locked_state(run)
        if checkpoint.checkpoint_index != expected_checkpoint_index:
            await self.session.commit()
            return
        self._set_attempt(run, None)
        if restore_feedback:
            state, _ = await self._locked_state(run)
            await _restore_feedback_without_write(self, run, state)
        await self._commit()

    async def _author_context(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
    ) -> _AuthorContext:
        await self._require_project_owner(project_id, actor_user_id)
        chapter = await self._chapter(project_id, chapter_id, lock=True)
        run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self._locked_state(run)
        if (
            state.status is not ChapterProductionStatus.AUTHOR_REVISION
            or not state.awaiting_user
            or state.action_request_id != str(action_request_id)
            or state.action_kind is not ChapterActionKind.AUTHOR_REVISION
        ):
            raise _invalid()
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_request_id,
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.project_id == project_id,
                ActionRequest.chapter_id == chapter_id,
                ActionRequest.request_type == _AUTHOR_ACTION_TYPE,
            )
            .with_for_update()
        )
        pending_count = await self.session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(
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
        metadata = self._action_metadata(action)
        document_id = UUID(metadata["document_id"])
        version_id = UUID(metadata["document_version_id"])
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id == version_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.content_hash == metadata["content_hash"],
            )
            .with_for_update()
        )
        if document is None:
            stale_document = await self.session.scalar(
                select(Document)
                .options(selectinload(Document.project), selectinload(Document.current_version))
                .where(
                    Document.id == document_id,
                    Document.project_id == project_id,
                    Document.chapter_id == chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
                    Document.current_version_id.is_not(None),
                    Document.current_version_id != version_id,
                )
                .with_for_update()
            )
            stale_version = (
                await self.session.scalar(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.id == stale_document.current_version_id,
                        DocumentVersion.document_id == document_id,
                        DocumentVersion.parent_version_id == version_id,
                    )
                    .with_for_update()
                )
                if stale_document is not None
                else None
            )
            if (
                stale_document is not None
                and stale_version is not None
                and chapter.current_draft_document_id == stale_document.id
                and stale_version.source == DocumentSource.USER.value
                and stale_version.actor_user_id is not None
                and str(stale_version.actor_user_id) == str(actor_user_id)
                and stale_version.agent_role is None
                and stale_version.workflow_run_id is None
            ):
                await self.documents.derive_chapter_segment_map(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=stale_document.id,
                    version_id=stale_version.id,
                )
                stale_binding = ChapterActionBinding(
                    action_request_id=str(action.id),
                    workflow_run_id=str(run.id),
                    chapter_id=str(chapter.id),
                    request_type=action.request_type,
                    kind=ChapterActionKind.AUTHOR_REVISION,
                    status=ActionRequestStatus.PENDING,
                    pending_count=1,
                    document_id=str(document_id),
                    document_version_id=str(version_id),
                    content_hash=metadata["content_hash"],
                    current_document_id=str(stale_document.id),
                    current_document_version_id=str(stale_version.id),
                    current_content_hash=stale_version.content_hash,
                )
                adopted = state.reconcile_stale_action(action=stale_binding)
                self._resolve_action_row(
                    action,
                    status=ActionRequestStatus.CANCELLED,
                    decision=ChapterActionDecision.CANCEL,
                    actor_user_id=actor_user_id,
                )
                self._append_state(run, checkpoint, adopted)
                await self._commit()
                raise _StaleActionAdopted(
                    ChapterProductionV2Updated(
                        workflow_run_id=run.id,
                        draft_document_id=stale_document.id,
                        draft_version_id=stale_version.id,
                        action_request_id=None,
                    )
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
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
        )
        binding = ChapterActionBinding(
            action_request_id=str(action.id),
            workflow_run_id=str(run.id),
            chapter_id=str(chapter.id),
            request_type=action.request_type,
            kind=ChapterActionKind.AUTHOR_REVISION,
            status=ActionRequestStatus.PENDING,
            pending_count=1,
            document_id=str(document.id),
            document_version_id=str(version.id),
            content_hash=version.content_hash,
            current_document_id=str(document.id),
            current_document_version_id=str(version.id),
            current_content_hash=version.content_hash,
        )
        return _AuthorContext(run, state, checkpoint, action, binding, document, version)

    async def _require_project_owner(
        self, project_id: UUID, actor_user_id: UUID, *, lock: bool = True
    ) -> None:
        try:
            await self.repository.require_project_owner(
                project_id, actor_user_id, lock=lock
            )
        except _ChapterProductionRepositoryValidationError:
            pass
        else:
            return
        raise _invalid()

    @staticmethod
    def _validated_ids(*values: UUID) -> tuple[UUID, ...]:
        try:
            if any(
                isinstance(value, (str, bytes))
                or not hasattr(value, "int")
                or UUID(str(value)).int == 0
                for value in values
            ):
                raise _invalid()
        except (AttributeError, TypeError, ValueError):
            raise _invalid() from None
        return values

    @staticmethod
    def _validated_uuid_sequence(values: Sequence[UUID], *, maximum: int) -> tuple[UUID, ...]:
        if type(values) not in (tuple, list) or not 1 <= len(values) <= maximum:
            raise _invalid()
        try:
            if any(
                isinstance(value, (str, bytes)) or not hasattr(value, "int") for value in values
            ):
                raise _invalid()
            selected = tuple(UUID(str(value)) for value in values)
        except (AttributeError, TypeError, ValueError):
            raise _invalid() from None
        if any(value.int == 0 for value in selected) or len(selected) != len(set(selected)):
            raise _invalid()
        return selected

    def _feedback_request(
        self,
        *,
        context: _AuthorContext,
        project_id: UUID,
        chapter_id: UUID,
        feedback: str,
        target_segment_ids: Sequence[UUID],
        segment_map: ChapterSegmentMap,
    ) -> UserFeedbackRevisionRequest:
        if type(target_segment_ids) not in (tuple, list):
            raise _invalid()
        selected = tuple(target_segment_ids)
        known_order = {item.segment_id: item.ordinal for item in segment_map.segments}
        if (
            not 1 <= len(selected) <= 64
            or len(selected) != len(set(selected))
            or any(type(item) is not UUID or item not in known_order for item in selected)
            or selected != tuple(sorted(selected, key=known_order.__getitem__))
            or len(segment_map.segments) > 64
        ):
            raise _invalid()
        run_metadata = self._run_metadata(context.run)
        try:
            source_segments = tuple(
                SourceDraftSegment(
                    segment_id=item.segment_id,
                    index=item.ordinal,
                    title=item.structural_path,
                    content=item.content,
                )
                for item in segment_map.segments
            )
            allowed_segments = tuple(
                AllowedChapterSegment(
                    segment_id=item.segment_id,
                    index=item.ordinal,
                    title=item.structural_path,
                    brief=item.content,
                )
                for item in segment_map.segments
            )
            return UserFeedbackRevisionRequest(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=context.run.id,
                approved_outline=ApprovedOutlineReference(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=UUID(run_metadata["outline_document_id"]),
                    version_id=UUID(run_metadata["outline_version_id"]),
                ),
                source_draft=SourceDraftReference(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=context.document.id,
                    version_id=context.version.id,
                    segments=source_segments,
                ),
                allowed_segments=allowed_segments,
                target_segment_ids=selected,
                feedback_refs=(
                    UserFeedbackReference(
                        feedback_id=context.action.id,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=context.run.id,
                        source_draft_document_id=context.document.id,
                        source_draft_version_id=context.version.id,
                        instruction=feedback,
                    ),
                ),
            )
        except Exception:
            raise _invalid() from None

    async def _locked_current_revision(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        document_id: UUID,
        version_id: UUID,
        parent_version_id: UUID,
        source: DocumentSource,
        actor_user_id: UUID | None,
        agent_role: str | None,
        operation_key: str,
        expected_attempt_id: str | None = None,
    ) -> tuple[Document, DocumentVersion]:
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id == version_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.parent_version_id == parent_version_id,
                DocumentVersion.source == source.value,
                DocumentVersion.actor_user_id == actor_user_id,
                DocumentVersion.agent_role == agent_role,
                DocumentVersion.workflow_run_id == workflow_run_id,
            )
            .with_for_update()
        )
        if (
            document is None
            or version is None
            or version.metadata_
            != {
                "contract_version": _CONTRACT_VERSION,
                "operation_key": operation_key,
                **({"attempt_id": expected_attempt_id} if expected_attempt_id is not None else {}),
            }
        ):
            raise _invalid()
        await self.documents.derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
        )
        return document, version

    @staticmethod
    def _action_metadata(action: ActionRequest) -> dict[str, str]:
        metadata = action.metadata_
        if (
            type(metadata) is not dict
            or set(metadata)
            != {
                "contract_version",
                "action_kind",
                "document_id",
                "document_version_id",
                "content_hash",
                "operation_key",
            }
            or metadata.get("contract_version") != _CONTRACT_VERSION
            or metadata.get("action_kind") != ChapterActionKind.AUTHOR_REVISION.value
            or any(
                type(metadata.get(key)) is not str
                for key in (
                    "document_id",
                    "document_version_id",
                    "content_hash",
                    "operation_key",
                )
            )
            or len(metadata["content_hash"]) != 64
            or len(metadata["operation_key"]) != 64
        ):
            raise _invalid()
        try:
            UUID(metadata["document_id"])
            UUID(metadata["document_version_id"])
        except (TypeError, ValueError, AttributeError):
            raise _invalid() from None
        return metadata  # type: ignore[return-value]

    @staticmethod
    def _resolve_action_row(
        action: ActionRequest,
        *,
        status: ActionRequestStatus,
        decision: ChapterActionDecision,
        actor_user_id: UUID,
        feedback: str | None = None,
    ) -> None:
        action.status = status.value
        action.user_decision = decision.value
        action.user_feedback = feedback
        action.resolved_by_id = actor_user_id
        action.resolved_at = datetime.now(UTC)

    def _append_state(
        self,
        run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        state: ChapterProductionState,
    ) -> None:
        self._project_state(run, state)
        self.session.add(
            WorkflowCheckpoint(
                workflow_run_id=run.id,
                checkpoint_index=checkpoint.checkpoint_index + 1,
                node_name=state.current_node,
                state_json=state.to_checkpoint(),
            )
        )

    @staticmethod
    def _new_author_action(
        *,
        run: WorkflowRun,
        project_id: UUID,
        chapter_id: UUID,
        document: Document,
        version: DocumentVersion,
        operation_key: str,
    ) -> ActionRequest:
        return ActionRequest(
            workflow_run_id=run.id,
            project_id=project_id,
            chapter_id=chapter_id,
            request_type=_AUTHOR_ACTION_TYPE,
            status=ActionRequestStatus.PENDING.value,
            prompt="Review the current chapter draft.",
            options=["accept", "request_revision", "submit_manual_edit"],
            default_option="accept",
            metadata_={
                "contract_version": _CONTRACT_VERSION,
                "action_kind": ChapterActionKind.AUTHOR_REVISION.value,
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "content_hash": version.content_hash,
                "operation_key": operation_key,
            },
        )

    @staticmethod
    def _binding_for_new_action(
        *,
        action: ActionRequest,
        run: WorkflowRun,
        chapter_id: UUID,
        document: Document,
        version: DocumentVersion,
    ) -> ChapterActionBinding:
        return ChapterActionBinding(
            action_request_id=str(action.id),
            workflow_run_id=str(run.id),
            chapter_id=str(chapter_id),
            request_type=action.request_type,
            kind=ChapterActionKind.AUTHOR_REVISION,
            status=ActionRequestStatus.PENDING,
            pending_count=1,
            document_id=str(document.id),
            document_version_id=str(version.id),
            content_hash=version.content_hash,
            current_document_id=str(document.id),
            current_document_version_id=str(version.id),
            current_content_hash=version.content_hash,
        )

    @staticmethod
    def _validated_feedback(feedback: str) -> str:
        if (
            type(feedback) is not str
            or feedback != feedback.strip()
            or not feedback
            or "\x00" in feedback
        ):
            raise _invalid()
        try:
            encoded = feedback.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid() from None
        if len(encoded) > 8000:
            raise _invalid()
        return feedback

    @staticmethod
    def _decision_operation_key(
        workflow_run_id: UUID,
        action_request_id: UUID,
        source_version_id: UUID,
        kind: str,
        *,
        target_segment_ids: Sequence[UUID] = (),
        feedback_hash: str | None = None,
    ) -> str:
        return sha256_content(
            ":".join(
                (
                    _CONTRACT_VERSION,
                    str(workflow_run_id),
                    str(action_request_id),
                    str(source_version_id),
                    kind,
                    *(str(item) for item in target_segment_ids),
                    feedback_hash or "",
                )
            )
        )
    async def _outline_for_chapter(
        self, chapter: Chapter, project_id: UUID, *, lock: bool
    ) -> tuple[Document, DocumentVersion]:
        if not isinstance(chapter, Chapter):
            raise _invalid()
        try:
            result = await self.repository.outline_for_chapter(
                project_id, chapter.id, lock=lock
            )
        except _ChapterProductionRepositoryValidationError:
            pass
        else:
            return result
        raise _invalid()

    async def _chapter(self, project_id: UUID, chapter_id: UUID, *, lock: bool) -> Chapter:
        try:
            result = await self.repository.chapter(project_id, chapter_id, lock=lock)
        except _ChapterProductionRepositoryValidationError:
            pass
        else:
            return result
        raise _invalid()

    async def _run(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, lock: bool
    ) -> WorkflowRun:
        try:
            result = await self.repository.run(
                project_id, chapter_id, workflow_run_id, lock=lock
            )
        except _ChapterProductionRepositoryValidationError:
            pass
        else:
            return result
        raise _invalid()
    async def _locked_state(
        self, run: WorkflowRun
    ) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
        self._run_metadata(run)
        checkpoints = list(
            await self.session.scalars(
                select(WorkflowCheckpoint)
                .execution_options(populate_existing=True)
                .where(WorkflowCheckpoint.workflow_run_id == run.id)
                .order_by(WorkflowCheckpoint.checkpoint_index.desc())
                .limit(2)
                .with_for_update()
            )
        )
        if not checkpoints:
            raise _invalid()
        checkpoint = checkpoints[0]
        if len(checkpoints) == 2 and (
            checkpoint.checkpoint_index != checkpoints[1].checkpoint_index + 1
        ):
            raise _invalid()
        payload = checkpoint.state_json
        finalized_statuses = {
            ChapterProductionStatus.REVISION_READY.value,
            ChapterProductionStatus.ARCHIVE_UPDATE.value,
            ChapterProductionStatus.COMPLETED.value,
        }
        finalized = type(payload) is dict and (
            payload.get("status") in finalized_statuses
            or (
                payload.get("status") == ChapterProductionStatus.FAILED.value
                and payload.get("failed_from_status") in finalized_statuses
            )
        )
        try:
            if not finalized:
                state = ChapterProductionState.from_checkpoint(payload)
                state.validate_persistence_binding(
                    workflow_run_id=str(run.id),
                    chapter_id=str(run.chapter_id),
                    run_workflow_type=run.workflow_type,
                    run_status=run.status,
                    run_current_node=run.current_node,
                    run_awaiting_user=run.awaiting_user,
                    checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                    checkpoint_node_name=checkpoint.node_name,
                )
                return state, checkpoint
            if run.project_id is None or run.chapter_id is None:
                raise ChapterProductionValidationError("Finalized scope is incomplete.")
            references = _ReviewStateReferences(
                review_policy_version=payload["review_policy_version"],
                chief_editor_required=payload["chief_editor_required"],
                editor_report_id=payload["editor_report_id"],
                chief_editor_report_id=payload["chief_editor_report_id"],
                lore_report_id=payload["lore_report_id"],
            )
            document_id = UUID(payload["document_id"])
            version_id = UUID(payload["document_version_id"])
            document = await self.session.scalar(
                select(Document)
                .options(selectinload(Document.project), selectinload(Document.current_version))
                .execution_options(populate_existing=True)
                .where(
                    Document.id == document_id,
                    Document.project_id == run.project_id,
                    Document.chapter_id == run.chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
                    Document.current_version_id == version_id,
                )
                .with_for_update()
            )
            version = await self.session.scalar(
                select(DocumentVersion)
                .execution_options(populate_existing=True)
                .where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.content_hash == payload["content_hash"],
                )
                .with_for_update()
            )
            if document is None or version is None:
                raise ChapterProductionValidationError("Finalized version is stale.")
            policy, editor, chief, lore = await self._live_review_bindings_locked(
                run=run,
                state=references,
                document=document,
                version=version,
            )
            state = ChapterProductionState.from_finalized_checkpoint(
                payload,
                policy=policy,
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type,
                run_status=run.status,
                run_current_node=run.current_node,
                run_awaiting_user=run.awaiting_user,
                checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                checkpoint_node_name=checkpoint.node_name,
                document_id=str(document.id),
                current_document_version_id=str(version.id),
                version_content_hash=version.content_hash,
                editor_report=editor,
                chief_editor_report=chief,
                lore_report=lore,
            )
            await self._validate_existing_ready_pair_locked(
                run=run,
                state=state,
                policy=policy,
                document=document,
                version=version,
                editor=editor,
                chief=chief,
                lore=lore,
            )
        except (ChapterProductionValidationError, KeyError, TypeError, ValueError):
            raise _invalid() from None
        return state, checkpoint

    async def _validate_existing_ready_pair_locked(
        self,
        *,
        run: WorkflowRun,
        state: ChapterProductionState,
        policy: ChapterReviewPolicyBinding,
        document: Document,
        version: DocumentVersion,
        editor: ChapterReviewBinding,
        chief: ChapterReviewBinding | None,
        lore: ChapterReviewBinding,
    ) -> None:
        await self._readiness.validate_existing_pair(
            run=run,
            state=state,
            policy=policy,
            document=document,
            version=version,
            editor=editor,
            chief=chief,
            lore=lore,
        )

    @staticmethod
    def _project_state(run: WorkflowRun, state: ChapterProductionState) -> None:
        run.status = state.status.value
        run.current_node = state.current_node
        run.awaiting_user = state.awaiting_user
        run.next_node = None
    @staticmethod
    def _run_metadata(run: WorkflowRun) -> dict[str, str]:
        metadata = run.metadata_
        expected = {
            "contract_version",
            "review_policy_version",
            "chief_editor_required",
            "outline_document_id",
            "outline_version_id",
            "outline_content_hash",
            "segmenter_version",
            "operation_key",
            "provider_attempt",
            "reviewer_claim",
        }
        legacy_expected = expected - {"reviewer_claim"}
        if type(metadata) is dict and set(metadata) == legacy_expected:
            metadata = {**metadata, "reviewer_claim": None}
            run.metadata_ = metadata
        if (
            type(metadata) is not dict
            or set(metadata) != expected
            or metadata.get("contract_version") != _CONTRACT_VERSION
            or metadata.get("review_policy_version") != _REVIEW_POLICY_VERSION
            or type(metadata.get("chief_editor_required")) is not bool
            or metadata.get("segmenter_version") != CURRENT_CHAPTER_SEGMENTER_VERSION
            or not ChapterProductionV2Service._attempt_metadata_is_valid(
                metadata.get("provider_attempt")
            )
            or not ChapterProductionV2Service._reviewer_claim_metadata_is_valid(
                metadata.get("reviewer_claim")
            )
            or any(
                type(metadata.get(key)) is not str
                for key in (
                    "outline_document_id",
                    "outline_version_id",
                    "outline_content_hash",
                    "operation_key",
                )
            )
        ):
            raise _invalid()
        return metadata  # type: ignore[return-value]

    @staticmethod
    def _reviewer_claim_metadata_is_valid(value: object) -> bool:
        if value is None:
            return True
        if type(value) is not dict or set(value) != {
            "claim_id",
            "operation_key",
            "stage",
            "checkpoint_index",
            "document_id",
            "document_version_id",
            "content_hash",
            "review_policy_version",
            "segment_map_hash",
            "request_hash",
            "status",
        }:
            return False
        return (
            _valid_nonzero_uuid(value.get("claim_id"))
            and _valid_sha256(value.get("operation_key"))
            and value.get("stage") in {item.value for item in ChapterReviewStage}
            and type(value.get("checkpoint_index")) is int
            and value["checkpoint_index"] >= 0
            and _valid_nonzero_uuid(value.get("document_id"))
            and _valid_nonzero_uuid(value.get("document_version_id"))
            and _valid_sha256(value.get("content_hash"))
            and value.get("review_policy_version") == _REVIEW_POLICY_VERSION
            and _valid_sha256(value.get("segment_map_hash"))
            and _valid_sha256(value.get("request_hash"))
            and value.get("status")
            in {_REVIEWER_CLAIM_STATUS_CLAIMED, _REVIEWER_CLAIM_STATUS_FAILED}
        )

    @staticmethod
    def _attempt_metadata_is_valid(value: object) -> bool:
        if value is None:
            return True
        if type(value) is not dict or set(value) != {
            "attempt_id",
            "key",
            "kind",
            "checkpoint_index",
            "source_document_id",
            "source_version_id",
            "action_request_id",
            "target_segment_ids",
            "feedback_hash",
            "report_ids",
            "report_input_hash",
            "status",
        }:
            return False
        if (
            not _valid_nonzero_uuid(value.get("attempt_id"))
            or type(value.get("checkpoint_index")) is not int
            or value["checkpoint_index"] < 0
            or value.get("kind") not in {"initial", "feedback", "review"}
            or value.get("status") not in {_ATTEMPT_STATUS_CLAIMED, _ATTEMPT_STATUS_FAILED}
            or type(value.get("target_segment_ids")) is not list
            or type(value.get("report_ids")) is not list
            or len(value["target_segment_ids"]) > 64
            or len(value["report_ids"]) > 16
        ):
            return False
        is_hash = lambda item: (  # noqa: E731
            type(item) is str
            and len(item) == 64
            and all(character in "0123456789abcdef" for character in item)
        )
        is_uuid = lambda item: (  # noqa: E731
            type(item) is str and len(item) <= 36 and _valid_nonzero_uuid(item)
        )
        targets = value["target_segment_ids"]
        reports = value["report_ids"]
        if (
            not is_hash(value.get("key"))
            or any(not is_uuid(item) for item in targets)
            or any(not is_uuid(item) for item in reports)
            or len(targets) != len(set(targets))
            or len(reports) != len(set(reports))
        ):
            return False
        kind = value["kind"]
        source_document_id = value.get("source_document_id")
        source_version_id = value.get("source_version_id")
        action_request_id = value.get("action_request_id")
        feedback_hash = value.get("feedback_hash")
        report_input_hash = value.get("report_input_hash")
        if kind == "initial":
            return (
                source_document_id is None
                and source_version_id is None
                and action_request_id is None
                and targets == []
                and feedback_hash is None
                and reports == []
                and report_input_hash is None
            )
        if not is_uuid(source_document_id) or not is_uuid(source_version_id) or not targets:
            return False
        if kind == "feedback":
            return (
                is_uuid(action_request_id)
                and is_hash(feedback_hash)
                and reports == []
                and report_input_hash is None
            )
        return (
            action_request_id is None
            and feedback_hash is None
            and bool(reports)
            and is_hash(report_input_hash)
        )

    @staticmethod
    def _validate_outline_metadata(
        metadata: dict[str, str], outline: Document, version: DocumentVersion
    ) -> None:
        if (
            metadata["outline_document_id"] != str(outline.id)
            or metadata["outline_version_id"] != str(version.id)
            or metadata["outline_content_hash"] != version.content_hash
            or outline.current_version_id != version.id
        ):
            raise _invalid()

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except BaseException:
            await self._rollback()
            raise ChapterProductionV2CommitIndeterminateError() from None

    async def _rollback(self) -> None:
        try:
            await self.session.rollback()
        except BaseException:
            pass


__all__ = [
    "ChapterProductionV2CommitIndeterminateError",
    "ChapterProductionV2Finalized",
    "ChapterProductionV2ProviderError",
    "ChapterProductionV2ReviewProviderError",
    "ChapterProductionV2ReconciliationError",
    "ChapterProductionV2Service",
    "ChapterProductionV2Started",
    "ChapterProductionV2Updated",
    "ChapterProductionV2ValidationError",
    "compose_initial_markdown",
    "merge_segment_replacements",
]
