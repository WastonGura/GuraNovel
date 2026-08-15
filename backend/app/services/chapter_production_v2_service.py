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
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    ApprovedOutlineSnapshot,
    ChapterReviewReport,
    ChapterReviewTarget,
    ChiefEditorChapterFinalAgent,
    ChiefEditorChapterFinalRequest,
    EditorAgent,
    EditorReviewRequest,
    LoreChapterFinalAgent,
    LoreChapterFinalRequest,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewFindingSeverity,
    ReviewerRole,
    ReviewSegmentSnapshot,
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
from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
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
    WorkflowEvent,
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
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Finalized,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ReviewProviderError,
    ChapterProductionV2Started,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import (
    DocumentCommitIndeterminateError,
    DocumentService,
)
from app.services.author_accept_coordination import (
    AuthorAcceptCoordinator,
    _StaleActionAdopted,
)
from app.services.initial_draft_lifecycle import InitialCandidateNotApplicable, InitialDraftLifecycle, InitialRecoveryRoute
from app.services.manual_edit_saga import ManualEditCoordinator
from app.services.feedback_revision_handoff import FeedbackRevisionHandoff
from app.services.feedback_candidate_saga import (
    FeedbackCandidateIdentity,
    FeedbackCandidateSaga,
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
    ChapterReviewOutcome,
    ChapterReviewPolicyBinding,
    ChapterReviewStage,
)
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path


_CONTRACT_VERSION = "chapter-production-v2"
_REVIEW_POLICY_VERSION = "chapter-quality-v1"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"
_ATTEMPT_STATUS_CLAIMED = "claimed"
_ATTEMPT_STATUS_FAILED = "failed"
_REVIEWER_CLAIM_STATUS_CLAIMED = "claimed"
_REVIEWER_CLAIM_STATUS_FAILED = "failed"
_REVIEW_WARNING_ACTION_TYPE = "chapter_review_warning"
_REVIEW_REVISION_ACTION_TYPE = "chapter_review_revision"
_READY_EVENT_TYPE = "revision_ready"
_REVIEW_EVENT_TYPE = "chapter_review_recorded"


def _safe_cancelled_error(_: BaseException) -> asyncio.CancelledError:
    """Return a cancellation signal that cannot disclose provider exception data."""

    return asyncio.CancelledError()


def _valid_nonzero_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.int != 0 and str(parsed) == value


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _new_attempt_id() -> str:
    """Create a content-free provider-attempt generation identifier."""

    return str(uuid4())


@dataclass(frozen=True, slots=True)
class _ReviewContext:
    run: WorkflowRun
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    document: Document
    version: DocumentVersion
    segment_map: ChapterSegmentMap
    request: EditorReviewRequest | ChiefEditorChapterFinalRequest | LoreChapterFinalRequest
    stage: ChapterReviewStage
    request_hash: str
    operation_key: str


@dataclass(frozen=True, slots=True)
class _ReviewStateReferences:
    review_policy_version: str
    chief_editor_required: bool
    editor_report_id: str | None
    chief_editor_report_id: str | None
    lore_report_id: str | None


@dataclass(frozen=True, slots=True)
class _ReadyPair:
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    event: WorkflowEvent


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
        self._feedback_saga = FeedbackCandidateSaga(
            self, merge_segment_replacements, _validated_prospective_map
        )
        self.documents = DocumentService(session)
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
        await self._recover_failed_attempt(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            kind="review",
            target_segment_ids=target_segment_ids,
            report_ids=report_ids,
        )
        try:
            context = await self._review_revision_context(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                report_ids=report_ids,
                actor_user_id=actor_user_id,
            )
            if self.revision_agent is None:
                raise ChapterProductionV2ProviderError() from None
            request = self._review_revision_request(
                context=context,
                project_id=project_id,
                chapter_id=chapter_id,
                target_segment_ids=target_segment_ids,
            )
            report_input_hash = self._review_report_input_hash(context.reports)
            operation_key = self._review_operation_key(
                workflow_run_id=workflow_run_id,
                source_version_id=context.version.id,
                report_ids=tuple(report_ids),
                target_segment_ids=tuple(target_segment_ids),
                report_input_hash=report_input_hash,
            )
            attempt_id = _new_attempt_id()
            attempt_checkpoint_index = context.checkpoint.checkpoint_index
            metadata = self._run_metadata(context.run)
            if metadata["provider_attempt"] is not None:
                raise ChapterProductionV2ReconciliationError()
            self._set_attempt(
                context.run,
                self._attempt_payload(
                    attempt_id=attempt_id,
                    key=operation_key,
                    kind="review",
                    checkpoint_index=attempt_checkpoint_index,
                    source_document_id=context.document.id,
                    source_version_id=context.version.id,
                    target_segment_ids=target_segment_ids,
                    report_ids=report_ids,
                    report_input_hash=report_input_hash,
                ),
            )
            await self._commit()
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ProviderError:
            await self._rollback()
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
        cancellation = None
        provider_failure: ChapterFailureCode | None = None
        try:
            candidate = await self.revision_agent.review_driven_revision(request)
        except asyncio.CancelledError as error:
            cancellation = _safe_cancelled_error(error)
        except ProviderTimeoutError:
            provider_failure = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            provider_failure = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except Exception:
            provider_failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await self._release_attempt(
                workflow_run_id,
                expected_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_kind="review",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise cancellation from None
        if provider_failure is not None:
            await self._fail_provider(
                workflow_run_id,
                provider_failure,
                expected_status=ChapterProductionStatus.REVIEW_REVISION,
                expected_checkpoint_index=attempt_checkpoint_index,
                expected_attempt_key=operation_key,
                expected_attempt_id=attempt_id,
            )
            raise ChapterProductionV2ProviderError() from None
        replacements = {item.segment_id: item.content for item in candidate.segments}
        try:
            source_content = await self.documents.read_version_content(
                context.document.id, context.version.id
            )
            revised_content = merge_segment_replacements(
                source_content, context.segment_map, replacements
            )
            current = await self._review_revision_context(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                report_ids=report_ids,
                actor_user_id=actor_user_id,
            )
            if (
                current.segment_map.canonical_bytes() != context.segment_map.canonical_bytes()
                or self._review_report_input_hash(current.reports) != report_input_hash
            ):
                raise _invalid()
            current_attempt = self._run_metadata(current.run)["provider_attempt"]
            if (
                type(current_attempt) is not dict
                or current_attempt.get("key") != operation_key
                or current_attempt.get("attempt_id") != attempt_id
                or current_attempt.get("kind") != "review"
                or current_attempt.get("checkpoint_index") != attempt_checkpoint_index
                or current_attempt.get("report_input_hash") != report_input_hash
                or current_attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
            ):
                raise ChapterProductionV2ReconciliationError()
            _validated_prospective_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=context.document.id,
                version_id=context.version.id,
                content=revised_content,
            )
            version = await self.documents.write_document(
                document_id=context.document.id,
                content=revised_content,
                source=DocumentSource.WRITER_AGENT,
                expected_current_version_id=context.version.id,
                agent_role="revision_agent",
                workflow_run_id=workflow_run_id,
                change_summary="Applied a Chapter Production V2 review revision.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": operation_key,
                    "attempt_id": attempt_id,
                },
            )
        except DocumentCommitIndeterminateError:
            await self._rollback()
            raise ChapterProductionV2CommitIndeterminateError() from None
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ReconciliationError:
            await self._release_attempt(
                workflow_run_id,
                expected_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_kind="review",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise
        except ChapterProductionV2ValidationError:
            await self._release_attempt(
                workflow_run_id,
                expected_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_kind="review",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise
        except Exception:
            await self._release_attempt(
                workflow_run_id,
                expected_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_kind="review",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise _invalid() from None
        return await self._finalize_review_revision(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            document_id=context.document.id,
            version_id=version.id,
            expected_parent_version_id=context.version.id,
            operation_key=operation_key,
            attempt_id=attempt_id,
            report_ids=tuple(report_ids),
            report_input_hash=report_input_hash,
            actor_user_id=actor_user_id,
        )

    async def execute_current_review(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        """Run exactly the server-selected reviewer for the locked current version."""

        self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        context = await self._claim_current_review(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )
        agent = {
            ChapterReviewStage.EDITOR: self.editor_agent,
            ChapterReviewStage.CHIEF_EDITOR: self.chief_editor_agent,
            ChapterReviewStage.LORE: self.lore_agent,
        }[context.stage]
        if agent is None:
            await self._release_reviewer_claim(
                workflow_run_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=self._run_metadata(context.run)["reviewer_claim"]["claim_id"],
            )
            raise _invalid() from None

        cancellation: asyncio.CancelledError | None = None
        failure_code: ChapterFailureCode | None = None
        report: ChapterReviewReport | None = None
        try:
            report = await agent.review(context.request)  # type: ignore[arg-type, union-attr]
        except asyncio.CancelledError as error:
            cancellation = _safe_cancelled_error(error)
        except ProviderTimeoutError:
            failure_code = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            failure_code = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except Exception:
            failure_code = ChapterFailureCode.PROVIDER_UNAVAILABLE
        claim_id = self._run_metadata(context.run)["reviewer_claim"]["claim_id"]
        if cancellation is not None:
            await self._release_reviewer_claim(
                workflow_run_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
            )
            raise cancellation from None
        if failure_code is not None or report is None:
            await self._fail_reviewer(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                actor_user_id=actor_user_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
                failure_code=failure_code or ChapterFailureCode.PROVIDER_UNAVAILABLE,
            )
            raise ChapterProductionV2ReviewProviderError() from None
        return await self._persist_current_review(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            expected_operation_key=context.operation_key,
            expected_claim_id=claim_id,
            expected_request_hash=context.request_hash,
            report=report,
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

        self._validated_ids(
            project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id
        )
        try:
            typed_decision = ChapterActionDecision(decision)
        except (TypeError, ValueError):
            raise _invalid() from None
        if typed_decision not in {
            ChapterActionDecision.ACCEPT_WARNING,
            ChapterActionDecision.REQUEST_REVISION,
        }:
            raise _invalid() from None
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            if (
                not state.awaiting_user
                or state.action_request_id != str(action_request_id)
                or state.action_kind
                not in {ChapterActionKind.REVIEW_WARNING, ChapterActionKind.REVIEW_REVISION}
            ):
                raise _invalid()
            action = await self.session.scalar(
                select(ActionRequest)
                .execution_options(populate_existing=True)
                .where(
                    ActionRequest.id == action_request_id,
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.project_id == project_id,
                    ActionRequest.chapter_id == chapter_id,
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
            if action is None or pending_count != 1:
                raise _invalid()
            metadata = self._review_action_metadata(action)
            expected_report_id = {
                ChapterReviewStage.EDITOR.value: state.editor_report_id,
                ChapterReviewStage.CHIEF_EDITOR.value: state.chief_editor_report_id,
                ChapterReviewStage.LORE.value: state.lore_report_id,
            }[metadata["review_stage"]]
            if (
                metadata["action_kind"] != state.action_kind.value
                or metadata["review_report_id"] != expected_report_id
                or metadata["document_id"] != state.document_id
                or metadata["document_version_id"] != state.document_version_id
                or metadata["content_hash"] != state.content_hash
            ):
                raise _invalid()
            document, version = await self._locked_review_document(
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            report = await self.session.scalar(
                select(ReviewReport)
                .execution_options(populate_existing=True)
                .where(
                    ReviewReport.id == UUID(metadata["review_report_id"]),
                    ReviewReport.project_id == project_id,
                    ReviewReport.chapter_id == chapter_id,
                    ReviewReport.workflow_run_id == run.id,
                    ReviewReport.target_document_id == document.id,
                    ReviewReport.target_version_id == version.id,
                )
                .with_for_update()
            )
            stage = ChapterReviewStage(metadata["review_stage"])
            if report is None:
                raise _invalid()
            await self._validated_persisted_review_report(
                row=report,
                run=run,
                document=document,
                version=version,
                stage=stage,
            )
            if metadata["operation_key"] != report.raw_report.get("operation_key"):
                raise _invalid()
            if (
                state.action_kind is ChapterActionKind.REVIEW_WARNING
                and (report.passed is not True or not report.warnings)
            ) or (
                state.action_kind is ChapterActionKind.REVIEW_REVISION
                and (report.passed is not False or not report.blocking_issues)
            ):
                raise _invalid()
            binding = ChapterActionBinding(
                action_request_id=str(action.id),
                workflow_run_id=str(run.id),
                chapter_id=str(chapter.id),
                request_type=action.request_type,
                kind=state.action_kind,
                status=ActionRequestStatus(action.status),
                pending_count=pending_count,
                document_id=str(document.id),
                document_version_id=str(version.id),
                content_hash=version.content_hash,
                current_document_id=str(document.id),
                current_document_version_id=str(version.id),
                current_content_hash=version.content_hash,
            )
            next_state = state.resolve_action(action=binding, decision=typed_decision)
            self._resolve_action_row(
                action,
                status=(
                    ActionRequestStatus.APPROVED
                    if typed_decision is ChapterActionDecision.ACCEPT_WARNING
                    else ActionRequestStatus.REVISED
                ),
                decision=typed_decision,
                actor_user_id=actor_user_id,
            )
            if (
                next_state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
                and next_state.lore_report_id is not None
                and not next_state.awaiting_user
            ):
                next_state = await self._enter_revision_ready_locked(
                    run=run,
                    checkpoint=checkpoint,
                    state=next_state,
                    document=document,
                    version=version,
                )
            else:
                self._append_state(run, checkpoint, next_state)
            self.session.add(
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type="chapter_review_action_resolved",
                    node_name=next_state.current_node,
                    actor_type="user",
                    actor_id=str(actor_user_id),
                    payload={
                        "action_request_id": str(action.id),
                        "chapter_id": str(chapter.id),
                        "decision": typed_decision.value,
                        "document_version_id": str(version.id),
                        "status": next_state.status.value,
                    },
                )
            )
            await self._commit()
            return ChapterProductionV2Updated(
                workflow_run_id=run.id,
                draft_document_id=document.id,
                draft_version_id=version.id,
                action_request_id=(
                    UUID(next_state.action_request_id)
                    if next_state.action_request_id is not None
                    else None
                ),
            )
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

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

        self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        if not _valid_sha256(expected_operation_key) or not _valid_nonzero_uuid(
            expected_claim_id
        ):
            raise _invalid() from None
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            if state.status not in {
                ChapterProductionStatus.EDITOR_REVIEW,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW,
                ChapterProductionStatus.LORE_FINAL_REVIEW,
            } or state.awaiting_user:
                raise ChapterProductionV2ReconciliationError()
            claim = self._run_metadata(run)["reviewer_claim"]
            stage = {
                ChapterProductionStatus.EDITOR_REVIEW: ChapterReviewStage.EDITOR,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW: ChapterReviewStage.CHIEF_EDITOR,
                ChapterProductionStatus.LORE_FINAL_REVIEW: ChapterReviewStage.LORE,
            }[state.status]
            document, version = await self._locked_review_document(
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            if (
                type(claim) is not dict
                or claim.get("operation_key") != expected_operation_key
                or claim.get("claim_id") != expected_claim_id
                or claim.get("status") != _REVIEWER_CLAIM_STATUS_CLAIMED
                or claim.get("checkpoint_index") != checkpoint.checkpoint_index
                or claim.get("stage") != stage.value
                or await self._exact_review_report_count(
                    run=run, version=version, stage=stage
                )
                != 0
            ):
                raise ChapterProductionV2ReconciliationError()
            self._set_reviewer_claim(run, None)
            self._append_state(run, checkpoint, state)
            await self._commit()
            return state
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

    async def finalize_without_reader_panel(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Finalized:
        """Promote the exact ready version through a restart-safe DB/filesystem saga."""

        self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            if state.status is ChapterProductionStatus.COMPLETED:
                result = await self._finalized_result_locked(
                    chapter=chapter, run=run, state=state
                )
                await self.session.commit()
                return result
            document, version = await self._locked_review_document(
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            if state.status is ChapterProductionStatus.REVISION_READY:
                policy, editor, chief, lore = await self._live_review_bindings_locked(
                    run=run, state=state, document=document, version=version
                )
                try:
                    archive_state = state.begin_archive_update(
                        policy=policy,
                        document_id=str(document.id),
                        current_document_version_id=str(version.id),
                        version_content_hash=version.content_hash,
                        editor_report=editor,
                        chief_editor_report=chief,
                        lore_report=lore,
                    )
                except ChapterProductionValidationError:
                    raise _invalid() from None
                self._append_state(run, checkpoint, archive_state)
                chapter.status = ChapterProductionStatus.ARCHIVE_UPDATE.value
                await self._commit()
                state = archive_state
            elif state.status is not ChapterProductionStatus.ARCHIVE_UPDATE:
                raise _invalid()
            else:
                await self.session.commit()
            draft_document_id = document.id
            draft_version_id = version.id
            draft_hash = version.content_hash
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

        try:
            content = self._verified_snapshot_content(document, version)
            if version.id != draft_version_id or sha256_content(content) != draft_hash:
                raise _invalid()
            await self.session.commit()
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, _ = await self._locked_state(run)
            if state.status is ChapterProductionStatus.COMPLETED:
                result = await self._finalized_result_locked(
                    chapter=chapter, run=run, state=state
                )
                await self.session.commit()
                return result
            if state.status is not ChapterProductionStatus.ARCHIVE_UPDATE:
                raise _invalid()
            document, version = await self._locked_review_document(
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            if (
                document.id != draft_document_id
                or version.id != draft_version_id
                or version.content_hash != draft_hash
            ):
                raise ChapterProductionV2ReconciliationError()
            final_operation_key = self._final_operation_key(run, state)
            final_documents = list(
                await self.session.scalars(
                    select(Document)
                    .options(selectinload(Document.project))
                    .execution_options(populate_existing=True)
                    .where(
                        Document.project_id == project_id,
                        Document.chapter_id == chapter_id,
                        Document.type == DocumentType.CHAPTER_FINAL.value,
                    )
                    .with_for_update()
                )
            )
            writes: tuple[tuple[str, str], ...]
            if not final_documents:
                final_document, current_write, snapshot_write = (
                    await self.documents.stage_create_document(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        document_type=DocumentType.CHAPTER_FINAL,
                        title=f"Chapter {chapter.chapter_number} final",
                        path=self._final_document_path(chapter=chapter, run=run),
                        content=content,
                        source=DocumentSource.SYSTEM,
                        workflow_run_id=run.id,
                        change_summary="Promoted the reviewed Chapter Production V2 version.",
                        version_metadata={
                            "contract_version": _CONTRACT_VERSION,
                            "operation_key": final_operation_key,
                        },
                    )
                )
                final_version = final_document.current_version
                if final_version is None:
                    raise _invalid()
                writes = (current_write, snapshot_write)
                chapter.final_document_id = final_document.id
                await self._commit()
            elif len(final_documents) == 1:
                final_document = final_documents[0]
                final_version = await self._locked_current_document_version(
                    final_document
                )
                if (
                    final_version is None
                    or chapter.final_document_id != final_document.id
                    or not self._valid_final_document_paths(
                        chapter=chapter,
                        run=run,
                        document=final_document,
                        version=final_version,
                    )
                    or final_version.content_hash != draft_hash
                    or final_version.workflow_run_id != run.id
                    or final_version.source != DocumentSource.SYSTEM.value
                    or final_version.metadata_
                    != {
                        "contract_version": _CONTRACT_VERSION,
                        "operation_key": final_operation_key,
                    }
                    or final_version.snapshot_path is None
                ):
                    raise ChapterProductionV2ReconciliationError()
                writes = (
                    (final_document.path, content),
                    (final_version.snapshot_path, content),
                )
                await self.session.commit()
            else:
                raise ChapterProductionV2ReconciliationError()
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

        try:
            self.documents.write_staged_files(final_document, writes)
        except DocumentCommitIndeterminateError:
            raise ChapterProductionV2CommitIndeterminateError() from None

        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            if state.status is ChapterProductionStatus.COMPLETED:
                result = await self._finalized_result_locked(
                    chapter=chapter, run=run, state=state
                )
                await self.session.commit()
                return result
            if state.status is not ChapterProductionStatus.ARCHIVE_UPDATE:
                raise ChapterProductionV2ReconciliationError()
            document, version = await self._locked_review_document(
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            policy, editor, chief, lore = await self._live_review_bindings_locked(
                run=run, state=state, document=document, version=version
            )
            result = await self._finalized_result_locked(
                chapter=chapter, run=run, state=state
            )
            try:
                completed = state.complete(
                    policy=policy,
                    document_id=str(document.id),
                    current_document_version_id=str(version.id),
                    version_content_hash=version.content_hash,
                    editor_report=editor,
                    chief_editor_report=chief,
                    lore_report=lore,
                )
            except ChapterProductionValidationError:
                raise _invalid() from None
            self._append_state(run, checkpoint, completed)
            chapter.status = ChapterProductionStatus.COMPLETED.value
            self.session.add(
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type="chapter_finalized",
                    node_name=completed.current_node,
                    payload={
                        "chapter_id": str(chapter.id),
                        "document_version_id": state.document_version_id,
                        "final_document_id": str(result.final_document_id),
                        "final_version_id": str(result.final_version_id),
                        "status": completed.status.value,
                    },
                )
            )
            await self._commit()
            return result
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
        initial = await self._initial_drafts.reconcile(project_id, chapter_id, workflow_run_id, actor_user_id=actor_user_id)
        if initial is not InitialRecoveryRoute.LEGACY:
            return initial
        try:
            self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            if state.status not in {
                ChapterProductionStatus.DRAFTING,
                ChapterProductionStatus.AUTHOR_REVISION,
                ChapterProductionStatus.EDITOR_REVIEW,
                ChapterProductionStatus.REVIEW_REVISION,
            }:
                raise ChapterProductionV2ReconciliationError()
            candidates = await self._reconciliation_candidates(run, state)
            attempt = self._run_metadata(run)["provider_attempt"]
            if len(candidates) > 1:
                raise ChapterProductionV2ReconciliationError()
            if state.status is ChapterProductionStatus.EDITOR_REVIEW and candidates:
                raise ChapterProductionV2ReconciliationError()
            if not candidates:
                if type(attempt) is dict and attempt.get("status") == _ATTEMPT_STATUS_CLAIMED:
                    raise ChapterProductionV2ReconciliationError()
                if state.status is ChapterProductionStatus.AUTHOR_REVISION:
                    raise ChapterProductionV2ReconciliationError()
                if state.document_id is None:
                    raise ChapterProductionV2ReconciliationError()
                else:
                    canonical = await self.session.scalar(
                        select(Document)
                        .where(
                            Document.id == UUID(state.document_id),
                            Document.project_id == project_id,
                            Document.chapter_id == chapter_id,
                            Document.current_version_id == UUID(state.document_version_id),
                        )
                        .with_for_update()
                    )
                    if canonical is None:
                        raise ChapterProductionV2ReconciliationError()
                if state.status is ChapterProductionStatus.DRAFTING and state.document_id:
                    state = await self._restore_feedback_without_write(run, state)
                await self.session.commit()
                return state
            document, version = candidates[0]
            if (
                document.current_version_id != version.id
                or chapter.current_draft_document_id != document.id
            ):
                raise ChapterProductionV2ReconciliationError()
            operation_key = version.metadata_.get("operation_key")
            if type(operation_key) is not str or len(operation_key) != 64:
                raise ChapterProductionV2ReconciliationError()
            if state.status in {
                ChapterProductionStatus.DRAFTING,
                ChapterProductionStatus.REVIEW_REVISION,
            } and (
                type(attempt) is not dict
                or attempt.get("checkpoint_index") != checkpoint.checkpoint_index
                or not await self._candidate_matches_provider_attempt(
                    run=run,
                    state=state,
                    attempt=attempt,
                    document=document,
                    version=version,
                )
            ):
                raise ChapterProductionV2ReconciliationError()
            await self.session.commit()
            if state.status is ChapterProductionStatus.DRAFTING:
                if state.document_id is None:
                    raise ChapterProductionV2ReconciliationError()
                else:
                    old_action = await self._resolved_source_action(run.id, state)
                    identity = FeedbackCandidateIdentity(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=run.id,
                        action_request_id=old_action.id,
                        document_id=document.id,
                        version_id=version.id,
                        source_version_id=UUID(state.document_version_id),
                        source_content_hash=state.content_hash,
                        content_hash=version.content_hash,
                        operation_key=operation_key,
                        attempt_id=version.metadata_["attempt_id"],
                    )
                    await self.session.commit()
                    await self._feedback_saga.finalize(identity, actor_user_id=actor_user_id)
            elif state.status is ChapterProductionStatus.REVIEW_REVISION:
                await self._finalize_review_revision(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=run.id,
                    document_id=document.id,
                    version_id=version.id,
                    expected_parent_version_id=UUID(state.document_version_id),
                    operation_key=operation_key,
                    attempt_id=version.metadata_["attempt_id"],
                    report_ids=tuple(
                        UUID(item)
                        for item in (
                            state.editor_report_id,
                            state.chief_editor_report_id,
                            state.lore_report_id,
                        )
                        if item is not None
                    ),
                    report_input_hash=attempt["report_input_hash"],
                    actor_user_id=actor_user_id,
                )
            elif state.status is ChapterProductionStatus.AUTHOR_REVISION:
                action = await self._resolved_source_action(run.id, state)
                binding = self._binding_from_checkpoint_action(state, action)
                await self._manual_edit._finalize_manual_edit(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=run.id,
                    action_request_id=action.id,
                    actor_user_id=action.resolved_by_id,
                    document_id=document.id,
                    version_id=version.id,
                    old_binding=binding,
                    expected_parent_version_id=UUID(state.document_version_id),
                    operation_key=operation_key,
                    finalize_actor_user_id=actor_user_id,
                )
            return await self.load_state(
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
        if (self._initial_drafts is None or not _valid_sha256(expected_attempt_key)
                or not _valid_nonzero_uuid(expected_attempt_id)):
            raise _invalid() from None
        try:
            return await self._initial_drafts.acknowledge_no_write(
                project_id, chapter_id, workflow_run_id, actor_user_id=actor_user_id,
                operation_key=expected_attempt_key, attempt_id=UUID(expected_attempt_id))
        except InitialCandidateNotApplicable:
            pass
        try:
            await self._require_project_owner(project_id, actor_user_id)
            await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            attempt = self._run_metadata(run)["provider_attempt"]
            if (
                type(attempt) is not dict
                or attempt.get("key") != expected_attempt_key
                or attempt.get("attempt_id") != expected_attempt_id
                or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
                or attempt.get("checkpoint_index") != checkpoint.checkpoint_index
                or state.status
                not in {
                    ChapterProductionStatus.DRAFTING,
                    ChapterProductionStatus.REVIEW_REVISION,
                }
                or await self._reconciliation_candidates(run, state)
            ):
                raise ChapterProductionV2ReconciliationError()
            if state.document_id is None:
                raise ChapterProductionV2ReconciliationError()
            else:
                canonical = await self.session.scalar(
                    select(Document.id)
                    .where(
                        Document.id == UUID(state.document_id),
                        Document.project_id == project_id,
                        Document.chapter_id == chapter_id,
                        Document.current_version_id == UUID(state.document_version_id),
                    )
                    .with_for_update()
                )
                if canonical != UUID(state.document_id):
                    raise ChapterProductionV2ReconciliationError()
                if (
                    state.status is ChapterProductionStatus.DRAFTING
                    and attempt.get("kind") != "feedback"
                ) or (
                    state.status is ChapterProductionStatus.REVIEW_REVISION
                    and attempt.get("kind") != "review"
                ):
                    raise ChapterProductionV2ReconciliationError()
            self._set_attempt(run, None)
            if attempt.get("kind") == "feedback":
                state = await self._restore_feedback_without_write(
                    run,
                    state,
                    source_checkpoint_index=checkpoint.checkpoint_index - 1,
                )
                if state.status is not ChapterProductionStatus.AUTHOR_REVISION:
                    raise ChapterProductionV2ReconciliationError()
            else:
                self._append_state(run, checkpoint, state)
            await self._commit()
            return state
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
            await self._validated_persisted_review_report(
                row=report,
                run=run,
                document=document,
                version=version,
                stage=stage,
            )
        trigger_mode = report_slots[-1][1]
        await self._validated_resolved_review_action(
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

    async def _claim_current_review(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> _ReviewContext:
        retry_recovered = False
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            metadata = self._run_metadata(run)
            claim = metadata["reviewer_claim"]
            review_statuses = {
                ChapterProductionStatus.EDITOR_REVIEW,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW,
                ChapterProductionStatus.LORE_FINAL_REVIEW,
            }
            if state.status is ChapterProductionStatus.FAILED:
                if (
                    state.failed_from_status not in review_statuses
                    or state.failure_code
                    not in {
                        ChapterFailureCode.PROVIDER_UNAVAILABLE,
                        ChapterFailureCode.PROVIDER_TIMEOUT,
                        ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
                    }
                    or type(claim) is not dict
                    or claim.get("status") != _REVIEWER_CLAIM_STATUS_FAILED
                ):
                    raise ChapterProductionV2ReconciliationError()
                recovered = state.recover()
                self._set_reviewer_claim(run, None)
                self._append_state(run, checkpoint, recovered)
                await self._commit()
                retry_recovered = True
            elif state.status not in review_statuses or state.awaiting_user:
                raise _invalid()
            elif claim is not None:
                raise ChapterProductionV2ReconciliationError()
            if not retry_recovered:
                context = await self._build_review_context_locked(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    chapter=chapter,
                    run=run,
                    state=state,
                    checkpoint=checkpoint,
                )
                if await self._exact_review_report_count(
                    run=run,
                    version=context.version,
                    stage=context.stage,
                ):
                    raise ChapterProductionV2ReconciliationError()
                claim_id = _new_attempt_id()
                self._set_reviewer_claim(
                    run,
                    {
                        "claim_id": claim_id,
                        "operation_key": context.operation_key,
                        "stage": context.stage.value,
                        "checkpoint_index": checkpoint.checkpoint_index,
                        "document_id": str(context.document.id),
                        "document_version_id": str(context.version.id),
                        "content_hash": context.version.content_hash,
                        "review_policy_version": state.review_policy_version,
                        "segment_map_hash": context.segment_map.map_hash,
                        "request_hash": context.request_hash,
                        "status": _REVIEWER_CLAIM_STATUS_CLAIMED,
                    },
                )
                await self._commit()
                return context
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
        if retry_recovered:
            return await self._claim_current_review(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                actor_user_id=actor_user_id,
            )
        raise _invalid() from None

    async def _build_review_context_locked(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        chapter: Chapter,
        run: WorkflowRun,
        state: ChapterProductionState,
        checkpoint: WorkflowCheckpoint,
    ) -> _ReviewContext:
        stage = {
            ChapterProductionStatus.EDITOR_REVIEW: ChapterReviewStage.EDITOR,
            ChapterProductionStatus.CHIEF_FINAL_REVIEW: ChapterReviewStage.CHIEF_EDITOR,
            ChapterProductionStatus.LORE_FINAL_REVIEW: ChapterReviewStage.LORE,
        }.get(state.status)
        if stage is None:
            raise _invalid()
        metadata = self._run_metadata(run)
        if (
            state.review_policy_version != metadata["review_policy_version"]
            or state.chief_editor_required is not metadata["chief_editor_required"]
            or (stage is ChapterReviewStage.CHIEF_EDITOR and not state.chief_editor_required)
        ):
            raise _invalid()
        document, version = await self._locked_review_document(
            project_id=project_id,
            chapter_id=chapter_id,
            state=state,
            chapter=chapter,
        )
        segment_map = await self.documents.derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
        )
        if len(segment_map.segments) > 64 or segment_map.map_hash != segment_map.map_hash.lower():
            raise _invalid()
        outline, outline_version = await self._outline_for_chapter(
            chapter, project_id, lock=True
        )
        self._validate_outline_metadata(metadata, outline, outline_version)
        outline_content = self._verified_snapshot_content(outline, outline_version)
        contexts = await self._review_context_snapshots(project_id=project_id, stage=stage)
        target = ChapterReviewTarget(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
            segments=tuple(
                ReviewSegmentSnapshot(
                    segment_id=item.segment_id,
                    index=item.ordinal,
                    title=item.structural_path,
                    content=item.content,
                )
                for item in segment_map.segments
            ),
        )
        outline_snapshot = ApprovedOutlineSnapshot(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=outline.id,
            version_id=outline_version.id,
            content=outline_content.strip(),
        )
        request_type = {
            ChapterReviewStage.EDITOR: EditorReviewRequest,
            ChapterReviewStage.CHIEF_EDITOR: ChiefEditorChapterFinalRequest,
            ChapterReviewStage.LORE: LoreChapterFinalRequest,
        }[stage]
        request = request_type(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=run.id,
            target=target,
            approved_outline=outline_snapshot,
            contexts=contexts,
        )
        request_hash = sha256_content(
            json.dumps(
                request.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        operation_key = sha256_content(
            ":".join(
                (
                    _CONTRACT_VERSION,
                    str(run.id),
                    str(version.id),
                    state.review_policy_version,
                    stage.value,
                    str(checkpoint.checkpoint_index),
                    segment_map.map_hash,
                    request_hash,
                )
            )
        )
        return _ReviewContext(
            run,
            state,
            checkpoint,
            document,
            version,
            segment_map,
            request,
            stage,
            request_hash,
            operation_key,
        )

    async def _review_context_snapshots(
        self, *, project_id: UUID, stage: ChapterReviewStage
    ) -> tuple[ReviewContextSnapshot, ...]:
        if stage in {ChapterReviewStage.EDITOR, ChapterReviewStage.CHIEF_EDITOR}:
            allowed_types = {
                DocumentType.STYLE_GUIDE.value: ReviewContextKind.STYLE_GUIDE,
                DocumentType.CHAPTER_SUMMARY.value: ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY,
            }
        else:
            allowed_types = {
                DocumentType.WORLD_OVERVIEW.value: ReviewContextKind.LORE_BOUNDARY,
                DocumentType.POWER_SYSTEM.value: ReviewContextKind.LORE_BOUNDARY,
                DocumentType.FACTIONS.value: ReviewContextKind.LORE_BOUNDARY,
                DocumentType.GEOGRAPHY.value: ReviewContextKind.LORE_BOUNDARY,
                DocumentType.HISTORY.value: ReviewContextKind.TIMELINE,
                DocumentType.CHARACTER_PROFILE.value: ReviewContextKind.CHARACTER_STATE,
                DocumentType.MAIN_CAST.value: ReviewContextKind.CHARACTER_STATE,
                DocumentType.FORESHADOWING.value: ReviewContextKind.FORESHADOWING,
                DocumentType.UNRESOLVED_THREADS.value: ReviewContextKind.FORESHADOWING,
            }
        documents = list(
            await self.session.scalars(
                select(Document)
                .options(selectinload(Document.project))
                .execution_options(populate_existing=True)
                .where(
                    Document.project_id == project_id,
                    Document.type.in_(tuple(allowed_types)),
                    Document.current_version_id.is_not(None),
                )
                .order_by(Document.type, Document.path, Document.id)
                .limit(16)
                .with_for_update()
            )
        )
        snapshots: list[ReviewContextSnapshot] = []
        for document in documents:
            version_id = document.current_version_id
            if version_id is None:
                raise _invalid()
            version = await self.session.scalar(
                select(DocumentVersion)
                .execution_options(populate_existing=True)
                .where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.document_id == document.id,
                )
                .with_for_update()
            )
            if version is None:
                raise _invalid()
            content = self._verified_snapshot_content(document, version)
            snapshots.append(
                ReviewContextSnapshot(
                    project_id=project_id,
                    document_id=document.id,
                    version_id=version.id,
                    kind=allowed_types[document.type],
                    content=content.strip(),
                )
            )
        if not snapshots:
            raise _invalid()
        return tuple(snapshots)

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

    async def _persist_current_review(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
        expected_operation_key: str,
        expected_claim_id: str,
        expected_request_hash: str,
        report: ChapterReviewReport,
    ) -> ChapterProductionV2Updated:
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            context = await self._build_review_context_locked(
                project_id=project_id,
                chapter_id=chapter_id,
                chapter=chapter,
                run=run,
                state=state,
                checkpoint=checkpoint,
            )
            claim = self._run_metadata(run)["reviewer_claim"]
            if (
                type(claim) is not dict
                or claim.get("status") != _REVIEWER_CLAIM_STATUS_CLAIMED
                or claim.get("claim_id") != expected_claim_id
                or claim.get("operation_key") != expected_operation_key
                or claim.get("request_hash") != expected_request_hash
                or claim.get("checkpoint_index") != checkpoint.checkpoint_index
                or claim.get("stage") != context.stage.value
                or context.operation_key != expected_operation_key
                or context.request_hash != expected_request_hash
                or report.project_id != project_id
                or report.chapter_id != chapter_id
                or report.workflow_run_id != run.id
                or report.target_document_id != context.document.id
                or report.target_version_id != context.version.id
                or report.reviewer_role.value
                != {
                    ChapterReviewStage.EDITOR: ReviewerRole.EDITOR.value,
                    ChapterReviewStage.CHIEF_EDITOR: ReviewerRole.CHIEF_EDITOR.value,
                    ChapterReviewStage.LORE: ReviewerRole.LORE.value,
                }[context.stage]
                or await self._exact_review_report_count(
                    run=run, version=context.version, stage=context.stage
                )
                != 0
            ):
                raise ChapterProductionV2ReconciliationError()
            findings_by_severity: dict[ReviewFindingSeverity, list[dict[str, object]]] = {
                severity: [] for severity in ReviewFindingSeverity
            }
            for finding in report.findings:
                findings_by_severity[finding.severity].append(
                    {
                        "sequence": finding.sequence,
                        "code": finding.code,
                        "severity": finding.severity.value,
                        "required": finding.required,
                        "evidence_segment_ids": [
                            str(item) for item in finding.evidence_segment_ids
                        ],
                        "rationale": finding.rationale,
                        "suggested_action": finding.suggested_action,
                        "segmenter_version": context.segment_map.segmenter_version,
                        "segment_map_hash": context.segment_map.map_hash,
                    }
                )
            persisted = ReviewReport(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=run.id,
                review_mode=report.review_mode,
                reviewer_agent_role=report.reviewer_role.value,
                target_document_id=context.document.id,
                target_version_id=context.version.id,
                passed=report.passed,
                summary=report.summary,
                blocking_issues=findings_by_severity[ReviewFindingSeverity.BLOCKING],
                warnings=findings_by_severity[ReviewFindingSeverity.WARNING],
                notes=findings_by_severity[ReviewFindingSeverity.NOTE],
                suggested_actions=list(report.suggested_actions),
                raw_report={
                    "claim_id": expected_claim_id,
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": expected_operation_key,
                    "request_hash": expected_request_hash,
                    "segment_map_hash": context.segment_map.map_hash,
                    "segmenter_version": context.segment_map.segmenter_version,
                },
            )
            self.session.add(persisted)
            await self.session.flush()
            outcome = (
                ChapterReviewOutcome.BLOCKING
                if persisted.blocking_issues
                else ChapterReviewOutcome.WARNING
                if persisted.warnings
                else ChapterReviewOutcome.PASSED
            )
            action: ActionRequest | None = None
            action_binding: ChapterActionBinding | None = None
            if outcome is not ChapterReviewOutcome.PASSED:
                pending_count = await self.session.scalar(
                    select(func.count())
                    .select_from(ActionRequest)
                    .where(
                        ActionRequest.workflow_run_id == run.id,
                        ActionRequest.status == ActionRequestStatus.PENDING.value,
                    )
                )
                if pending_count != 0:
                    raise ChapterProductionV2ReconciliationError()
                action_kind = (
                    ChapterActionKind.REVIEW_WARNING
                    if outcome is ChapterReviewOutcome.WARNING
                    else ChapterActionKind.REVIEW_REVISION
                )
                action = self._new_review_action(
                    run=run,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document=context.document,
                    version=context.version,
                    report=persisted,
                    stage=context.stage,
                    action_kind=action_kind,
                    operation_key=expected_operation_key,
                )
                self.session.add(action)
                await self.session.flush()
                action_binding = ChapterActionBinding(
                    action_request_id=str(action.id),
                    workflow_run_id=str(run.id),
                    chapter_id=str(chapter_id),
                    request_type=action.request_type,
                    kind=action_kind,
                    status=ActionRequestStatus.PENDING,
                    pending_count=1,
                    document_id=str(context.document.id),
                    document_version_id=str(context.version.id),
                    content_hash=context.version.content_hash,
                    current_document_id=str(context.document.id),
                    current_document_version_id=str(context.version.id),
                    current_content_hash=context.version.content_hash,
                )
            review_binding = ChapterReviewBinding(
                report_id=str(persisted.id),
                stage=context.stage,
                workflow_run_id=str(run.id),
                chapter_id=str(chapter_id),
                document_id=str(context.document.id),
                document_version_id=str(context.version.id),
                review_mode=persisted.review_mode,
                reviewer_agent_role=persisted.reviewer_agent_role,
                passed=persisted.passed,
            )
            next_state = state.record_review(
                outcome=outcome,
                review=review_binding,
                action=action_binding,
            )
            self._set_reviewer_claim(run, None)
            if (
                context.stage is ChapterReviewStage.LORE
                and outcome is ChapterReviewOutcome.PASSED
            ):
                next_state = await self._enter_revision_ready_locked(
                    run=run,
                    checkpoint=checkpoint,
                    state=next_state,
                    document=context.document,
                    version=context.version,
                )
            else:
                self._append_state(run, checkpoint, next_state)
            self.session.add(
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type=_REVIEW_EVENT_TYPE,
                    node_name=next_state.current_node,
                    payload={
                        "chapter_id": str(chapter_id),
                        "document_version_id": str(context.version.id),
                        "finding_codes": [item.code for item in report.findings],
                        "review_outcome": outcome.value,
                        "review_report_id": str(persisted.id),
                        "review_stage": context.stage.value,
                        "segment_map_hash": context.segment_map.map_hash,
                        "status": next_state.status.value,
                    },
                )
            )
            await self._commit()
            return ChapterProductionV2Updated(
                workflow_run_id=run.id,
                draft_document_id=context.document.id,
                draft_version_id=context.version.id,
                action_request_id=action.id if action is not None else None,
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

    @staticmethod
    def _set_reviewer_claim(
        run: WorkflowRun, claim: dict[str, object] | None
    ) -> None:
        run.metadata_ = {**run.metadata_, "reviewer_claim": claim}

    async def _release_reviewer_claim(
        self,
        workflow_run_id: UUID,
        *,
        expected_operation_key: str,
        expected_claim_id: str,
    ) -> None:
        try:
            projection = await self.session.scalar(
                select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
            )
            if (
                projection is None
                or projection.project_id is None
                or projection.chapter_id is None
            ):
                await self._rollback()
                return
            await self._chapter(projection.project_id, projection.chapter_id, lock=True)
            run = await self._run(
                projection.project_id,
                projection.chapter_id,
                workflow_run_id,
                lock=True,
            )
            claim = self._run_metadata(run)["reviewer_claim"]
            if (
                type(claim) is dict
                and claim.get("operation_key") == expected_operation_key
                and claim.get("claim_id") == expected_claim_id
                and claim.get("status") == _REVIEWER_CLAIM_STATUS_CLAIMED
            ):
                self._set_reviewer_claim(run, None)
                await self._commit()
            else:
                await self._rollback()
        except BaseException:
            await self._rollback()

    async def _fail_reviewer(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
        expected_operation_key: str,
        expected_claim_id: str,
        failure_code: ChapterFailureCode,
    ) -> None:
        try:
            await self._require_project_owner(project_id, actor_user_id)
            await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            claim = self._run_metadata(run)["reviewer_claim"]
            if (
                state.status
                not in {
                    ChapterProductionStatus.EDITOR_REVIEW,
                    ChapterProductionStatus.CHIEF_FINAL_REVIEW,
                    ChapterProductionStatus.LORE_FINAL_REVIEW,
                }
                or type(claim) is not dict
                or claim.get("operation_key") != expected_operation_key
                or claim.get("claim_id") != expected_claim_id
                or claim.get("checkpoint_index") != checkpoint.checkpoint_index
                or claim.get("status") != _REVIEWER_CLAIM_STATUS_CLAIMED
            ):
                raise ChapterProductionV2ReconciliationError()
            failed = state.fail(failure_code)
            self._set_reviewer_claim(
                run, {**claim, "status": _REVIEWER_CLAIM_STATUS_FAILED}
            )
            self._append_state(run, checkpoint, failed)
            await self._commit()
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ReconciliationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()

    @staticmethod
    def _new_review_action(
        *,
        run: WorkflowRun,
        project_id: UUID,
        chapter_id: UUID,
        document: Document,
        version: DocumentVersion,
        report: ReviewReport,
        stage: ChapterReviewStage,
        action_kind: ChapterActionKind,
        operation_key: str,
    ) -> ActionRequest:
        if action_kind is ChapterActionKind.REVIEW_WARNING:
            request_type = _REVIEW_WARNING_ACTION_TYPE
            options = ["accept_warning", "request_revision"]
            default_option = None
            prompt = "Review the warning for the current chapter version."
        elif action_kind is ChapterActionKind.REVIEW_REVISION:
            request_type = _REVIEW_REVISION_ACTION_TYPE
            options = ["request_revision"]
            default_option = "request_revision"
            prompt = "Request a revision for the blocking chapter review."
        else:
            raise _invalid()
        return ActionRequest(
            workflow_run_id=run.id,
            project_id=project_id,
            chapter_id=chapter_id,
            request_type=request_type,
            status=ActionRequestStatus.PENDING.value,
            prompt=prompt,
            options=options,
            default_option=default_option,
            metadata_={
                "contract_version": _CONTRACT_VERSION,
                "action_kind": action_kind.value,
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "content_hash": version.content_hash,
                "operation_key": operation_key,
                "review_report_id": str(report.id),
                "review_stage": stage.value,
            },
        )

    @staticmethod
    def _review_action_metadata(action: ActionRequest) -> dict[str, str]:
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
                "review_report_id",
                "review_stage",
            }
            or metadata.get("contract_version") != _CONTRACT_VERSION
            or metadata.get("action_kind")
            not in {
                ChapterActionKind.REVIEW_WARNING.value,
                ChapterActionKind.REVIEW_REVISION.value,
            }
            or metadata.get("review_stage") not in {item.value for item in ChapterReviewStage}
            or not _valid_nonzero_uuid(metadata.get("document_id"))
            or not _valid_nonzero_uuid(metadata.get("document_version_id"))
            or not _valid_nonzero_uuid(metadata.get("review_report_id"))
            or not _valid_sha256(metadata.get("content_hash"))
            or not _valid_sha256(metadata.get("operation_key"))
            or action.status != ActionRequestStatus.PENDING.value
            or action.user_decision is not None
            or action.user_feedback is not None
            or action.resolved_by_id is not None
            or action.resolved_at is not None
        ):
            raise _invalid()
        expected_type = (
            _REVIEW_WARNING_ACTION_TYPE
            if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
            else _REVIEW_REVISION_ACTION_TYPE
        )
        expected_options = (
            (["accept_warning", "request_revision"], None)
            if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
            else (["request_revision"], "request_revision")
        )
        expected_prompt = (
            "Review the warning for the current chapter version."
            if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
            else "Request a revision for the blocking chapter review."
        )
        if (
            action.request_type != expected_type
            or action.options != expected_options[0]
            or action.default_option != expected_options[1]
            or action.prompt != expected_prompt
        ):
            raise _invalid()
        return metadata  # type: ignore[return-value]

    async def _validated_resolved_review_action(
        self,
        *,
        run: WorkflowRun,
        document: Document,
        version: DocumentVersion,
        report: ReviewReport,
        stage: ChapterReviewStage,
    ) -> ActionRequest:
        actions = list(
            await self.session.scalars(
                select(ActionRequest)
                .execution_options(populate_existing=True)
                .where(ActionRequest.workflow_run_id == run.id)
                .with_for_update()
            )
        )
        candidates = [
            action
            for action in actions
            if type(action.metadata_) is dict
            and action.metadata_.get("document_id") == str(document.id)
            and action.metadata_.get("document_version_id") == str(version.id)
            and action.metadata_.get("review_report_id") == str(report.id)
        ]
        if len(candidates) != 1:
            raise ChapterProductionV2ReconciliationError()
        action = candidates[0]
        metadata = action.metadata_
        expected_keys = {
            "contract_version",
            "action_kind",
            "document_id",
            "document_version_id",
            "content_hash",
            "operation_key",
            "review_report_id",
            "review_stage",
        }
        action_kind = (
            ChapterActionKind.REVIEW_REVISION
            if report.blocking_issues
            else ChapterActionKind.REVIEW_WARNING
            if report.warnings
            else None
        )
        expected_type = (
            _REVIEW_REVISION_ACTION_TYPE
            if action_kind is ChapterActionKind.REVIEW_REVISION
            else _REVIEW_WARNING_ACTION_TYPE
        )
        expected_options = (
            (["request_revision"], "request_revision")
            if action_kind is ChapterActionKind.REVIEW_REVISION
            else (["accept_warning", "request_revision"], None)
        )
        if (
            action.project_id != run.project_id
            or action.chapter_id != run.chapter_id
            or type(metadata) is not dict
            or set(metadata) != expected_keys
            or action_kind is None
            or metadata.get("contract_version") != _CONTRACT_VERSION
            or metadata.get("action_kind") != action_kind.value
            or metadata.get("content_hash") != version.content_hash
            or metadata.get("operation_key") != report.raw_report.get("operation_key")
            or metadata.get("review_stage") != stage.value
            or action.request_type != expected_type
            or action.options != expected_options[0]
            or action.default_option != expected_options[1]
            or action.status != ActionRequestStatus.REVISED.value
            or action.user_decision != ChapterActionDecision.REQUEST_REVISION.value
            or action.user_feedback is not None
            or action.resolved_by_id is None
            or action.resolved_at is None
        ):
            raise ChapterProductionV2ReconciliationError()
        return action

    async def _validated_persisted_review_report(
        self,
        *,
        row: ReviewReport,
        run: WorkflowRun,
        document: Document,
        version: DocumentVersion,
        stage: ChapterReviewStage,
    ) -> ChapterReviewReport:
        mode, role = {
            ChapterReviewStage.EDITOR: (ReviewMode.CHAPTER_EDITOR.value, "editor_agent"),
            ChapterReviewStage.CHIEF_EDITOR: (
                ReviewMode.CHAPTER_CHIEF_FINAL.value,
                "chief_editor_agent",
            ),
            ChapterReviewStage.LORE: (ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent"),
        }[stage]
        provenance_keys = {
            "claim_id",
            "contract_version",
            "operation_key",
            "request_hash",
            "segment_map_hash",
            "segmenter_version",
        }
        finding_keys = {
            "sequence",
            "code",
            "severity",
            "required",
            "evidence_segment_ids",
            "rationale",
            "suggested_action",
            "segmenter_version",
            "segment_map_hash",
        }
        if (
            row.project_id != run.project_id
            or row.chapter_id != run.chapter_id
            or row.workflow_run_id != run.id
            or row.target_document_id != document.id
            or row.target_version_id != version.id
            or row.review_mode != mode
            or row.reviewer_agent_role != role
            or type(row.raw_report) is not dict
            or set(row.raw_report) != provenance_keys
            or row.raw_report.get("contract_version") != _CONTRACT_VERSION
            or row.raw_report.get("segmenter_version")
            != CURRENT_CHAPTER_SEGMENTER_VERSION
            or not _valid_nonzero_uuid(row.raw_report.get("claim_id"))
            or not _valid_sha256(row.raw_report.get("operation_key"))
            or not _valid_sha256(row.raw_report.get("request_hash"))
            or not _valid_sha256(row.raw_report.get("segment_map_hash"))
            or type(row.blocking_issues) is not list
            or type(row.warnings) is not list
            or type(row.notes) is not list
            or type(row.suggested_actions) is not list
        ):
            raise ChapterProductionV2ReconciliationError()
        segment_map = await self.documents.derive_chapter_segment_map(
            project_id=run.project_id,
            chapter_id=run.chapter_id,
            document_id=document.id,
            version_id=version.id,
        )
        if row.raw_report["segment_map_hash"] != segment_map.map_hash:
            raise ChapterProductionV2ReconciliationError()
        findings: list[dict[str, object]] = []
        for bucket, severity in (
            (row.blocking_issues, ReviewFindingSeverity.BLOCKING),
            (row.warnings, ReviewFindingSeverity.WARNING),
            (row.notes, ReviewFindingSeverity.NOTE),
        ):
            for item in bucket:
                if (
                    type(item) is not dict
                    or set(item) != finding_keys
                    or item.get("severity") != severity.value
                    or item.get("segmenter_version") != segment_map.segmenter_version
                    or item.get("segment_map_hash") != segment_map.map_hash
                ):
                    raise ChapterProductionV2ReconciliationError()
                findings.append(
                    {key: value for key, value in item.items() if key not in {
                        "segmenter_version",
                        "segment_map_hash",
                    }}
                )
        findings.sort(key=lambda item: item.get("sequence", 0))
        try:
            validated = ChapterReviewReport(
                project_id=run.project_id,
                chapter_id=run.chapter_id,
                workflow_run_id=run.id,
                reviewer_role=role,
                review_mode=mode,
                target_document_id=document.id,
                target_version_id=version.id,
                passed=row.passed,
                summary=row.summary,
                findings=tuple(findings),
                suggested_actions=tuple(row.suggested_actions),
            )
        except Exception:
            raise ChapterProductionV2ReconciliationError() from None
        known_segments = {item.segment_id for item in segment_map.segments}
        if any(
            not set(finding.evidence_segment_ids) <= known_segments
            for finding in validated.findings
        ):
            raise ChapterProductionV2ReconciliationError()
        return validated

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
        metadata = self._run_metadata(run)
        policy = ChapterReviewPolicyBinding(
            workflow_run_id=str(run.id),
            chapter_id=str(run.chapter_id),
            review_policy_version=metadata["review_policy_version"],
            chief_editor_required=metadata["chief_editor_required"],
        )
        expected = _review_report_slots(
            editor_report_id=UUID(state.editor_report_id) if state.editor_report_id else None,
            chief_editor_report_id=(
                UUID(state.chief_editor_report_id) if state.chief_editor_report_id else None
            ),
            lore_report_id=UUID(state.lore_report_id) if state.lore_report_id else None,
        )
        required_count = 3 if state.chief_editor_required else 2
        if len(expected) != required_count:
            raise _invalid()
        rows = list(
            await self.session.scalars(
                select(ReviewReport)
                .execution_options(populate_existing=True)
                .where(
                    ReviewReport.project_id == run.project_id,
                    ReviewReport.chapter_id == run.chapter_id,
                    ReviewReport.workflow_run_id == run.id,
                    ReviewReport.target_document_id == document.id,
                    ReviewReport.target_version_id == version.id,
                    ReviewReport.review_mode.in_(
                        (
                            ReviewMode.CHAPTER_EDITOR.value,
                            ReviewMode.CHAPTER_CHIEF_FINAL.value,
                            ReviewMode.CHAPTER_FINAL_LORE.value,
                        )
                    ),
                )
                .with_for_update()
            )
        )
        if len(rows) != required_count or {row.id for row in rows} != {
            item[0] for item in expected
        }:
            raise ChapterProductionV2ReconciliationError()
        by_id = {row.id: row for row in rows}
        bindings: dict[ChapterReviewStage, ChapterReviewBinding] = {}
        for report_id, mode, role in expected:
            row = by_id[report_id]
            stage = {
                ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
                ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
                ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
            }[mode]
            if (
                row.review_mode != mode
                or row.reviewer_agent_role != role
                or row.passed is not True
                or row.target_document_id != document.id
                or row.target_version_id != version.id
            ):
                raise ChapterProductionV2ReconciliationError()
            await self._validated_persisted_review_report(
                row=row,
                run=run,
                document=document,
                version=version,
                stage=stage,
            )
            bindings[stage] = ChapterReviewBinding(
                report_id=str(row.id),
                stage=stage,
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                document_id=str(document.id),
                document_version_id=str(version.id),
                review_mode=row.review_mode,
                reviewer_agent_role=row.reviewer_agent_role,
                passed=row.passed,
            )
        return (
            policy,
            bindings[ChapterReviewStage.EDITOR],
            bindings.get(ChapterReviewStage.CHIEF_EDITOR),
            bindings[ChapterReviewStage.LORE],
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
        policy, editor, chief, lore = await self._live_review_bindings_locked(
            run=run, state=state, document=document, version=version
        )
        try:
            ready = state.finalize_revision_ready(
                policy=policy,
                document_id=str(document.id),
                current_document_version_id=str(version.id),
                version_content_hash=version.content_hash,
                editor_report=editor,
                chief_editor_report=chief,
                lore_report=lore,
            )
        except ChapterProductionValidationError:
            raise _invalid() from None
        pairs = await self._validated_ready_pairs_locked(run)
        semantic_key = (str(run.id), str(version.id), policy.review_policy_version)
        matches = [pair for pair in pairs if pair.state.semantic_ready_key == semantic_key]
        if not matches:
            ready_checkpoint = WorkflowCheckpoint(
                workflow_run_id=run.id,
                checkpoint_index=checkpoint.checkpoint_index + 1,
                node_name=ready.current_node,
                state_json=ready.to_checkpoint(),
            )
            self.session.add(ready_checkpoint)
            await self.session.flush()
            self.session.add(
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type=_READY_EVENT_TYPE,
                    node_name=ready.current_node,
                    payload={
                        "chapter_id": str(run.chapter_id),
                        "checkpoint_id": str(ready_checkpoint.id),
                        "checkpoint_index": ready_checkpoint.checkpoint_index,
                        "document_id": str(document.id),
                        "document_version_id": str(version.id),
                        "content_hash": version.content_hash,
                        "review_policy_version": policy.review_policy_version,
                        "status": ChapterProductionStatus.REVISION_READY.value,
                    },
                )
            )
            self._project_state(run, ready)
            return ready
        if len(matches) == 1:
            ready_checkpoint = matches[0].checkpoint
            event = matches[0].event
            expected_payload = {
                "chapter_id": str(run.chapter_id),
                "checkpoint_id": str(ready_checkpoint.id),
                "checkpoint_index": ready_checkpoint.checkpoint_index,
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "content_hash": version.content_hash,
                "review_policy_version": policy.review_policy_version,
                "status": ChapterProductionStatus.REVISION_READY.value,
            }
            if (
                ready_checkpoint.node_name != ready.current_node
                or ready_checkpoint.state_json != ready.to_checkpoint()
                or event.node_name != ready.current_node
                or event.payload != expected_payload
            ):
                raise ChapterProductionV2ReconciliationError()
            self._project_state(run, ready)
            return ready
        raise ChapterProductionV2ReconciliationError()

    async def _validated_ready_pairs_locked(
        self, run: WorkflowRun
    ) -> tuple[_ReadyPair, ...]:
        checkpoints = list(
            await self.session.scalars(
                select(WorkflowCheckpoint)
                .execution_options(populate_existing=True)
                .where(WorkflowCheckpoint.workflow_run_id == run.id)
                .order_by(WorkflowCheckpoint.checkpoint_index)
                .with_for_update()
            )
        )
        markers = [
            item
            for item in checkpoints
            if item.node_name == ChapterProductionStatus.REVISION_READY.value
            or (
                type(item.state_json) is dict
                and item.state_json.get("status")
                == ChapterProductionStatus.REVISION_READY.value
            )
        ]
        restored: list[tuple[ChapterProductionState, WorkflowCheckpoint]] = []
        for marker in markers:
            if (
                marker.node_name != ChapterProductionStatus.REVISION_READY.value
                or type(marker.state_json) is not dict
                or marker.state_json.get("status")
                != ChapterProductionStatus.REVISION_READY.value
                or sum(
                    item.checkpoint_index == marker.checkpoint_index - 1
                    for item in checkpoints
                )
                != 1
            ):
                raise ChapterProductionV2ReconciliationError()
            restored.append((await self._restore_ready_marker_locked(run, marker), marker))

        events = list(
            await self.session.scalars(
                select(WorkflowEvent)
                .execution_options(populate_existing=True)
                .where(
                    WorkflowEvent.workflow_run_id == run.id,
                    WorkflowEvent.event_type == _READY_EVENT_TYPE,
                )
                .with_for_update()
            )
        )
        expected_event_keys = {
            "chapter_id",
            "checkpoint_id",
            "checkpoint_index",
            "document_id",
            "document_version_id",
            "content_hash",
            "review_policy_version",
            "status",
        }
        if len(events) != len(restored) or any(
            type(item.payload) is not dict or set(item.payload) != expected_event_keys
            for item in events
        ):
            raise ChapterProductionV2ReconciliationError()
        pairs: list[_ReadyPair] = []
        used_events: set[UUID] = set()
        for state, marker in restored:
            matching = [
                item
                for item in events
                if item.payload.get("checkpoint_id") == str(marker.id)
            ]
            expected_payload = {
                "chapter_id": str(run.chapter_id),
                "checkpoint_id": str(marker.id),
                "checkpoint_index": marker.checkpoint_index,
                "document_id": state.document_id,
                "document_version_id": state.document_version_id,
                "content_hash": state.content_hash,
                "review_policy_version": state.review_policy_version,
                "status": ChapterProductionStatus.REVISION_READY.value,
            }
            if (
                len(matching) != 1
                or matching[0].id in used_events
                or matching[0].node_name != ChapterProductionStatus.REVISION_READY.value
                or matching[0].payload != expected_payload
            ):
                raise ChapterProductionV2ReconciliationError()
            used_events.add(matching[0].id)
            pairs.append(_ReadyPair(state, marker, matching[0]))
        return tuple(pairs)

    async def _restore_ready_marker_locked(
        self, run: WorkflowRun, checkpoint: WorkflowCheckpoint
    ) -> ChapterProductionState:
        payload = checkpoint.state_json
        try:
            if type(payload) is not dict:
                raise ChapterProductionValidationError("READY payload is malformed.")
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
                .options(selectinload(Document.project))
                .execution_options(populate_existing=True)
                .where(
                    Document.id == document_id,
                    Document.project_id == run.project_id,
                    Document.chapter_id == run.chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
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
                raise ChapterProductionValidationError("READY version is stale.")
            policy, editor, chief, lore = await self._live_review_bindings_locked(
                run=run,
                state=references,
                document=document,
                version=version,
            )
            return ChapterProductionState.from_revision_ready_checkpoint(
                payload,
                policy=policy,
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type,
                run_status=ChapterProductionStatus.REVISION_READY.value,
                run_current_node=ChapterProductionStatus.REVISION_READY.value,
                run_awaiting_user=False,
                checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                checkpoint_node_name=checkpoint.node_name,
                document_id=str(document.id),
                current_document_version_id=str(version.id),
                version_content_hash=version.content_hash,
                editor_report=editor,
                chief_editor_report=chief,
                lore_report=lore,
            )
        except ChapterProductionV2ReconciliationError:
            raise
        except (ChapterProductionValidationError, KeyError, TypeError, ValueError):
            raise ChapterProductionV2ReconciliationError() from None

    @staticmethod
    def _final_operation_key(
        run: WorkflowRun, state: ChapterProductionState
    ) -> str:
        if state.document_version_id is None:
            raise _invalid()
        return sha256_content(
            ":".join(
                (
                    _CONTRACT_VERSION,
                    str(run.id),
                    state.document_version_id,
                    state.review_policy_version,
                    "non-panel-final-promotion",
                )
            )
        )

    @staticmethod
    def _final_document_path(*, chapter: Chapter, run: WorkflowRun) -> str:
        return f"chapters/chapter-{chapter.chapter_number:04d}-{run.id}-final.md"

    @classmethod
    def _valid_final_document_paths(
        cls,
        *,
        chapter: Chapter,
        run: WorkflowRun,
        document: Document,
        version: DocumentVersion,
    ) -> bool:
        return (
            document.path == cls._final_document_path(chapter=chapter, run=run)
            and type(version.version_number) is int
            and version.version_number == 1
            and version.file_path == document.path
            and version.snapshot_path
            == version_snapshot_path(str(document.id), version.version_number).as_posix()
        )

    def _verify_final_artifacts(
        self, *, document: Document, version: DocumentVersion
    ) -> None:
        read_failed = False
        try:
            snapshot_content = self._verified_snapshot_content(document, version)
            current_content = MarkdownStore(
                Path(document.project.workspace_root)
            ).read_bounded(
                document.path,
                max_bytes=MAX_CHAPTER_CONTENT_BYTES,
            )
        except Exception:
            read_failed = True
            snapshot_content = ""
            current_content = ""
        if read_failed:
            raise ChapterProductionV2ReconciliationError()
        if (
            current_content != snapshot_content
            or len(current_content.encode("utf-8")) != version.byte_size
            or sha256_content(current_content) != version.content_hash
        ):
            raise ChapterProductionV2ReconciliationError()

    async def _finalized_result_locked(
        self,
        *,
        chapter: Chapter,
        run: WorkflowRun,
        state: ChapterProductionState,
    ) -> ChapterProductionV2Finalized:
        if chapter.final_document_id is None or state.content_hash is None:
            raise ChapterProductionV2ReconciliationError()
        documents = list(
            await self.session.scalars(
                select(Document)
                .options(selectinload(Document.project))
                .execution_options(populate_existing=True)
                .where(
                    Document.project_id == run.project_id,
                    Document.chapter_id == chapter.id,
                    Document.type == DocumentType.CHAPTER_FINAL.value,
                )
                .with_for_update()
            )
        )
        if len(documents) != 1 or documents[0].id != chapter.final_document_id:
            raise ChapterProductionV2ReconciliationError()
        document = documents[0]
        version = await self._locked_current_document_version(document)
        if (
            version is None
            or document.current_version_id != version.id
            or version.document_id != document.id
            or not self._valid_final_document_paths(
                chapter=chapter,
                run=run,
                document=document,
                version=version,
            )
            or version.content_hash != state.content_hash
            or version.workflow_run_id != run.id
            or version.source != DocumentSource.SYSTEM.value
            or version.parent_version_id is not None
            or version.metadata_
            != {
                "contract_version": _CONTRACT_VERSION,
                "operation_key": self._final_operation_key(run, state),
            }
        ):
            raise ChapterProductionV2ReconciliationError()
        self._verify_final_artifacts(document=document, version=version)
        return ChapterProductionV2Finalized(run.id, document.id, version.id)

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

    async def _finalize_review_revision(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        document_id: UUID,
        version_id: UUID,
        expected_parent_version_id: UUID,
        operation_key: str,
        attempt_id: str,
        report_ids: tuple[UUID, ...],
        report_input_hash: str,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        try:
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
            attempt = self._run_metadata(run)["provider_attempt"]
            pending_count = await self.session.scalar(
                select(func.count())
                .select_from(ActionRequest)
                .where(
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
            )
            if (
                state.status is not ChapterProductionStatus.REVIEW_REVISION
                or state.awaiting_user
                or report_ids != expected_reports
                or pending_count != 0
            ):
                raise _invalid()
            if (
                type(attempt) is not dict
                or attempt.get("key") != operation_key
                or attempt.get("attempt_id") != attempt_id
                or attempt.get("kind") != "review"
                or attempt.get("checkpoint_index") != checkpoint.checkpoint_index
                or attempt.get("report_input_hash") != report_input_hash
                or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
            ):
                raise ChapterProductionV2ReconciliationError()
            source_document = await self.session.scalar(
                select(Document)
                .execution_options(populate_existing=True)
                .where(
                    Document.id == document_id,
                    Document.project_id == project_id,
                    Document.chapter_id == chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
                )
                .with_for_update()
            )
            source_version = await self.session.scalar(
                select(DocumentVersion)
                .execution_options(populate_existing=True)
                .where(
                    DocumentVersion.id == expected_parent_version_id,
                    DocumentVersion.document_id == document_id,
                )
                .with_for_update()
            )
            if source_document is None or source_version is None:
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
                        ReviewReport.target_document_id == document_id,
                        ReviewReport.target_version_id == expected_parent_version_id,
                        ReviewReport.review_mode == expected_mode,
                        ReviewReport.reviewer_agent_role == expected_role,
                    )
                    .with_for_update()
                )
                if report is None:
                    raise ChapterProductionV2ReconciliationError()
                stage = {
                    ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
                    ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
                    ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
                }[expected_mode]
                await self._validated_persisted_review_report(
                    row=report,
                    run=run,
                    document=source_document,
                    version=source_version,
                    stage=stage,
                )
                reports.append(report)
            trigger_mode = report_slots[-1][1]
            await self._validated_resolved_review_action(
                run=run,
                document=source_document,
                version=source_version,
                report=reports[-1],
                stage={
                    ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
                    ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
                    ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
                }[trigger_mode],
            )
            if self._review_report_input_hash(reports) != report_input_hash:
                raise ChapterProductionV2ReconciliationError()
            document, version = await self._locked_current_revision(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=run.id,
                document_id=document_id,
                version_id=version_id,
                parent_version_id=expected_parent_version_id,
                source=DocumentSource.WRITER_AGENT,
                actor_user_id=None,
                agent_role="revision_agent",
                operation_key=operation_key,
                expected_attempt_id=attempt_id,
            )
            if chapter.current_draft_document_id != document.id:
                raise _invalid()
            next_state = state.submit_review_revision(
                document_id=str(document.id),
                document_version_id=str(version.id),
                content_hash=version.content_hash,
            )
            self._set_attempt(run, None)
            self._append_state(run, checkpoint, next_state)
            await self._commit()
            return ChapterProductionV2Updated(
                workflow_run_id=run.id,
                draft_document_id=document.id,
                draft_version_id=version.id,
                action_request_id=None,
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

    async def _reconciliation_candidates(
        self, run: WorkflowRun, state: ChapterProductionState
    ) -> list[tuple[Document, DocumentVersion]]:
        versions = list(
            await self.session.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.workflow_run_id == run.id,
                    DocumentVersion.parent_version_id
                    == (
                        UUID(state.document_version_id)
                        if state.document_version_id is not None
                        else None
                    ),
                )
                .with_for_update()
            )
        )
        candidates: list[tuple[Document, DocumentVersion]] = []
        for version in versions:
            if version.metadata_.get("contract_version") != _CONTRACT_VERSION:
                raise ChapterProductionV2ReconciliationError()
            document = await self.session.scalar(
                select(Document)
                .options(selectinload(Document.project), selectinload(Document.current_version))
                .where(
                    Document.id == version.document_id,
                    Document.project_id == run.project_id,
                    Document.chapter_id == run.chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
                )
                .with_for_update()
            )
            if document is None:
                raise ChapterProductionV2ReconciliationError()
            await self.documents.derive_chapter_segment_map(
                project_id=run.project_id,
                chapter_id=run.chapter_id,
                document_id=document.id,
                version_id=version.id,
            )
            candidates.append((document, version))
        return candidates

    async def _candidate_matches_provider_attempt(
        self,
        *,
        run: WorkflowRun,
        state: ChapterProductionState,
        attempt: object,
        document: Document,
        version: DocumentVersion,
    ) -> bool:
        if (
            type(attempt) is not dict
            or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
            or version.workflow_run_id != run.id
            or version.metadata_.get("contract_version") != _CONTRACT_VERSION
            or version.metadata_.get("operation_key") != attempt.get("key")
            or version.metadata_.get("attempt_id") != attempt.get("attempt_id")
            or set(version.metadata_) != {"contract_version", "operation_key", "attempt_id"}
            or document.current_version_id != version.id
        ):
            return False
        if state.document_id is None:
            return False
        kind = attempt.get("kind")
        if (
            attempt.get("source_document_id") != state.document_id
            or attempt.get("source_version_id") != state.document_version_id
            or version.document_id != UUID(state.document_id)
            or version.parent_version_id != UUID(state.document_version_id)
            or version.source != DocumentSource.WRITER_AGENT.value
            or version.actor_user_id is not None
            or version.agent_role != "revision_agent"
        ):
            return False
        targets = tuple(UUID(item) for item in attempt["target_segment_ids"])
        if kind == "feedback":
            action_id = UUID(attempt["action_request_id"])
            action = await self.session.get(ActionRequest, action_id, with_for_update=True)
            if (
                action is None
                or action.workflow_run_id != run.id
                or action.user_decision != ChapterActionDecision.REQUEST_REVISION.value
                or sha256_content(action.user_feedback or "") != attempt.get("feedback_hash")
            ):
                return False
            expected_key = self._decision_operation_key(
                run.id,
                action_id,
                UUID(state.document_version_id),
                "feedback",
                target_segment_ids=targets,
                feedback_hash=attempt["feedback_hash"],
            )
            return attempt.get("key") == expected_key
        if kind != "review":
            return False
        report_ids = tuple(UUID(item) for item in attempt["report_ids"])
        reports_by_id = {
            report.id: report
            for report in await self.session.scalars(
                select(ReviewReport)
                .execution_options(populate_existing=True)
                .where(ReviewReport.id.in_(report_ids))
                .with_for_update()
            )
        }
        if set(reports_by_id) != set(report_ids):
            return False
        reports = tuple(reports_by_id[item] for item in report_ids)
        report_input_hash = self._review_report_input_hash(reports)
        expected_key = self._review_operation_key(
            workflow_run_id=run.id,
            source_version_id=UUID(state.document_version_id),
            report_ids=report_ids,
            target_segment_ids=targets,
            report_input_hash=report_input_hash,
        )
        return (
            attempt.get("report_input_hash") == report_input_hash
            and attempt.get("key") == expected_key
        )

    async def _resolved_source_action(
        self, run_id: UUID, state: ChapterProductionState
    ) -> ActionRequest:
        actions = list(
            await self.session.scalars(
                select(ActionRequest)
                .where(
                    ActionRequest.workflow_run_id == run_id,
                )
                .with_for_update()
            )
        )
        matches = [
            action
            for action in actions
            if action.status == ActionRequestStatus.REVISED.value
            and action.metadata_.get("document_id") == state.document_id
            and action.metadata_.get("document_version_id") == state.document_version_id
        ]
        if len(matches) != 1 or any(action.status == ActionRequestStatus.PENDING.value for action in actions):
            raise ChapterProductionV2ReconciliationError()
        return matches[0]

    def _binding_from_checkpoint_action(
        self, state: ChapterProductionState, action: ActionRequest
    ) -> ChapterActionBinding:
        metadata = self._action_metadata(action)
        if state.document_id is None or state.document_version_id is None:
            raise ChapterProductionV2ReconciliationError()
        return ChapterActionBinding(
            action_request_id=str(action.id),
            workflow_run_id=state.chapter_workflow_run_id,
            chapter_id=state.chapter_id,
            request_type=action.request_type,
            kind=ChapterActionKind.AUTHOR_REVISION,
            status=ActionRequestStatus.PENDING,
            pending_count=1,
            document_id=state.document_id,
            document_version_id=state.document_version_id,
            content_hash=metadata["content_hash"],
            current_document_id=state.document_id,
            current_document_version_id=state.document_version_id,
            current_content_hash=metadata["content_hash"],
        )

    async def _restore_feedback_without_write(
        self,
        run: WorkflowRun,
        state: ChapterProductionState,
        *,
        source_checkpoint_index: int | None = None,
    ) -> ChapterProductionState:
        action = await self._resolved_source_action(run.id, state)
        if action.user_decision != ChapterActionDecision.REQUEST_REVISION.value:
            raise ChapterProductionV2ReconciliationError()
        latest = await self.session.scalar(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .with_for_update()
        )
        if latest is None or latest.checkpoint_index < 1:
            raise ChapterProductionV2ReconciliationError()
        previous_index = (
            source_checkpoint_index
            if source_checkpoint_index is not None
            else latest.checkpoint_index - 1
        )
        previous = await self.session.scalar(
            select(WorkflowCheckpoint)
            .where(
                WorkflowCheckpoint.workflow_run_id == run.id,
                WorkflowCheckpoint.checkpoint_index == previous_index,
            )
            .with_for_update()
        )
        try:
            restored = ChapterProductionState.from_checkpoint(previous.state_json)
        except (AttributeError, ChapterProductionValidationError):
            raise ChapterProductionV2ReconciliationError() from None
        if (
            restored.status is not ChapterProductionStatus.AUTHOR_REVISION
            or restored.action_request_id != str(action.id)
        ):
            raise ChapterProductionV2ReconciliationError()
        action.status = ActionRequestStatus.PENDING.value
        action.user_decision = None
        action.user_feedback = None
        action.resolved_by_id = None
        action.resolved_at = None
        self._append_state(run, latest, restored)
        return restored

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
            if (await self._resolved_source_action(run.id, state)).id != action.id:
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
            await self._restore_feedback_without_write(
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
            await self._restore_feedback_without_write(run, state)
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
        semantic_key = (str(run.id), str(version.id), policy.review_policy_version)
        matches = [
            pair
            for pair in await self._validated_ready_pairs_locked(run)
            if pair.state.semantic_ready_key == semantic_key
        ]
        if len(matches) != 1:
            raise ChapterProductionV2ReconciliationError()
        ready_checkpoint = matches[0].checkpoint
        event = matches[0].event
        ready = ChapterProductionState.from_revision_ready_checkpoint(
            ready_checkpoint.state_json,
            policy=policy,
            workflow_run_id=str(run.id),
            chapter_id=str(run.chapter_id),
            run_workflow_type=run.workflow_type,
            run_status=ChapterProductionStatus.REVISION_READY.value,
            run_current_node="REVISION_READY",
            run_awaiting_user=False,
            checkpoint_workflow_run_id=str(ready_checkpoint.workflow_run_id),
            checkpoint_node_name=ready_checkpoint.node_name,
            document_id=str(document.id),
            current_document_version_id=str(version.id),
            version_content_hash=version.content_hash,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=lore,
        )
        if ready.semantic_ready_key != (
            str(run.id),
            str(version.id),
            policy.review_policy_version,
        ) or event.payload != {
            "chapter_id": str(run.chapter_id),
            "checkpoint_id": str(ready_checkpoint.id),
            "checkpoint_index": ready_checkpoint.checkpoint_index,
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "content_hash": version.content_hash,
            "review_policy_version": policy.review_policy_version,
            "status": ChapterProductionStatus.REVISION_READY.value,
        }:
            raise ChapterProductionV2ReconciliationError()
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
