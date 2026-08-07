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
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    InitialDraftRequest,
    RevisionAgent,
    ReviewDrivenRevisionRequest,
    ReviewReportReference,
    SourceDraftReference,
    SourceDraftSegment,
    UserFeedbackReference,
    UserFeedbackRevisionRequest,
    WriterAgent,
)
from app.core.errors import AppError
from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    MAX_CHAPTER_CONTENT_BYTES,
    ChapterSegmentMap,
    ChapterSegmentError,
    derive_chapter_segment_map,
    normalize_chapter_content,
)
from app.llm import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
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
    ReviewMode,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowRun,
    WorkflowType,
)
from app.services.document_service import (
    DocumentCommitIndeterminateError,
    DocumentService,
)
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
_REVIEW_POLICY_VERSION = "chapter-quality-v1"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"
_ATTEMPT_STATUS_CLAIMED = "claimed"
_ATTEMPT_STATUS_FAILED = "failed"


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


class ChapterProductionV2ValidationError(AppError):
    """A fixed, content-free failure at the V2 orchestration boundary."""

    code = "chapter_production_v2_invalid"
    default_message = "Chapter production input is invalid."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2ProviderError(AppError):
    """A provider failure whose text and causal chain are intentionally fixed."""

    status_code = 503
    code = "chapter_production_v2_provider_failed"
    default_message = "Chapter drafting failed safely."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2CommitIndeterminateError(AppError):
    status_code = 500
    code = "chapter_production_v2_commit_indeterminate"
    default_message = "Chapter drafting requires reconciliation before retrying."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2ReconciliationError(AppError):
    status_code = 409
    code = "chapter_production_v2_reconciliation_required"
    default_message = "Chapter production requires explicit reconciliation."

    def __init__(self) -> None:
        super().__init__()


@dataclass(frozen=True, slots=True)
class ChapterProductionV2Started:
    workflow_run_id: UUID
    action_request_id: UUID
    outline_document_id: UUID
    outline_version_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID


@dataclass(frozen=True, slots=True)
class ChapterProductionV2Updated:
    workflow_run_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID
    action_request_id: UUID | None


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


class _StaleActionAdopted(Exception):
    def __init__(self, result: ChapterProductionV2Updated) -> None:
        self.result = result
        super().__init__()


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
    ) -> None:
        self.session = session
        self.writer_agent = writer_agent
        self.revision_agent = revision_agent
        self.documents = DocumentService(session)

    async def start_from_approved_outline(
        self,
        project_id: UUID,
        chapter_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Started:
        """Create or resume the one V2 draft operation for the approved outline."""

        try:
            self._validated_ids(project_id, chapter_id, actor_user_id)
            await self._require_project_owner(project_id, actor_user_id)
            chapter, outline, outline_version = await self._approved_outline(
                project_id, chapter_id, lock=True
            )
            outline_map = await self.documents.derive_chapter_production_segment_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=outline.id,
                version_id=outline_version.id,
            )
            operation_key = self._operation_key(
                project_id=project_id,
                chapter_id=chapter_id,
                outline_document_id=outline.id,
                outline_version_id=outline_version.id,
                outline_content_hash=outline_version.content_hash,
            )
            existing = await self._operation_run(chapter_id, operation_key)
            if existing is None:
                run = WorkflowRun(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
                    status=ChapterProductionStatus.DRAFTING.value,
                    current_node="drafting",
                    next_node=None,
                    awaiting_user=False,
                    metadata_={
                        "contract_version": _CONTRACT_VERSION,
                        "review_policy_version": _REVIEW_POLICY_VERSION,
                        "chief_editor_required": True,
                        "outline_document_id": str(outline.id),
                        "outline_version_id": str(outline_version.id),
                        "outline_content_hash": outline_version.content_hash,
                        "segmenter_version": CURRENT_CHAPTER_SEGMENTER_VERSION,
                        "operation_key": operation_key,
                        "provider_attempt": None,
                    },
                )
                self.session.add(run)
                await self.session.flush()
                state = ChapterProductionState.initial(
                    chapter_workflow_run_id=str(run.id),
                    chapter_id=str(chapter_id),
                    review_policy_version=_REVIEW_POLICY_VERSION,
                    chief_editor_required=True,
                )
                self.session.add(
                    WorkflowCheckpoint(
                        workflow_run_id=run.id,
                        checkpoint_index=0,
                        node_name=state.current_node,
                        state_json=state.to_checkpoint(),
                    )
                )
                await self._commit()
                run_id = run.id
                resume_existing = False
            else:
                run_id = existing.id
                resume_existing = True
                await self.session.commit()
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None
        if resume_existing:
            return await self.resume_drafting(
                project_id,
                chapter_id,
                run_id,
                actor_user_id=actor_user_id,
            )
        return await self._resume_with_outline_map(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=run_id,
            outline_map=outline_map,
            actor_user_id=actor_user_id,
        )

    async def resume_drafting(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Started:
        """Resume an exact V2 draft without duplicating committed artifacts."""

        try:
            self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            metadata = self._run_metadata(run)
            if run.status == ChapterProductionStatus.AUTHOR_REVISION.value:
                result = await self._completed_result(run, metadata)
                await self.session.commit()
                return result
            if run.status not in {
                ChapterProductionStatus.DRAFTING.value,
                ChapterProductionStatus.FAILED.value,
            }:
                raise _invalid()
            outline, outline_version = await self._outline_for_chapter(
                chapter, project_id, lock=True
            )
            self._validate_outline_metadata(metadata, outline, outline_version)
            outline_map = await self.documents.derive_chapter_production_segment_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=outline.id,
                version_id=outline_version.id,
            )
            committed = await self._committed_draft(run.id, metadata["operation_key"])
            await self.session.commit()
            if committed is not None:
                return await self._finalize_draft(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=run.id,
                    draft=committed[0],
                    version=committed[1],
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
        return await self._resume_with_outline_map(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            outline_map=outline_map,
            actor_user_id=actor_user_id,
        )

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
            context = await self._author_context(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
            next_state = context.state.resolve_action(
                action=context.binding,
                decision=ChapterActionDecision.ACCEPT,
            )
            self._resolve_action_row(
                context.action,
                status=ActionRequestStatus.APPROVED,
                decision=ChapterActionDecision.ACCEPT,
                actor_user_id=actor_user_id,
            )
            self._append_state(context.run, context.checkpoint, next_state)
            await self._commit()
            return ChapterProductionV2Updated(
                workflow_run_id=context.run.id,
                draft_document_id=context.document.id,
                draft_version_id=context.version.id,
                action_request_id=None,
            )
        except _StaleActionAdopted as adopted:
            return adopted.result
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
        feedback_hash = sha256_content(feedback)
        await self._recover_failed_attempt(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            kind="feedback",
            action_request_id=action_request_id,
            target_segment_ids=target_segment_ids,
            feedback_hash=feedback_hash,
            restore_feedback=True,
        )
        try:
            context = await self._author_context(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
            feedback = self._validated_feedback(feedback)
            if self.revision_agent is None:
                raise ChapterProductionV2ProviderError() from None
            segment_map = await self.documents.derive_chapter_segment_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=context.document.id,
                version_id=context.version.id,
            )
            request = self._feedback_request(
                context=context,
                project_id=project_id,
                chapter_id=chapter_id,
                feedback=feedback,
                target_segment_ids=target_segment_ids,
                segment_map=segment_map,
            )
            next_state = context.state.resolve_action(
                action=context.binding,
                decision=ChapterActionDecision.REQUEST_REVISION,
            )
            operation_key = self._decision_operation_key(
                workflow_run_id,
                action_request_id,
                context.version.id,
                "feedback",
                target_segment_ids=target_segment_ids,
                feedback_hash=feedback_hash,
            )
            attempt_id = _new_attempt_id()
            self._resolve_action_row(
                context.action,
                status=ActionRequestStatus.REVISED,
                decision=ChapterActionDecision.REQUEST_REVISION,
                actor_user_id=actor_user_id,
                feedback=feedback,
            )
            self._append_state(context.run, context.checkpoint, next_state)
            attempt_checkpoint_index = context.checkpoint.checkpoint_index + 1
            self._set_attempt(
                context.run,
                self._attempt_payload(
                    attempt_id=attempt_id,
                    key=operation_key,
                    kind="feedback",
                    checkpoint_index=attempt_checkpoint_index,
                    source_document_id=context.document.id,
                    source_version_id=context.version.id,
                    action_request_id=action_request_id,
                    target_segment_ids=target_segment_ids,
                    feedback_hash=feedback_hash,
                ),
            )
            await self._commit()
        except _StaleActionAdopted as adopted:
            return adopted.result
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ProviderError:
            await self._rollback()
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None

        cancellation: asyncio.CancelledError | None = None
        provider_failure: ChapterFailureCode | None = None
        try:
            candidate = await self.revision_agent.user_feedback_revision(request)
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
                expected_kind="feedback",
                expected_checkpoint_index=attempt_checkpoint_index,
                restore_feedback=True,
            )
            raise cancellation from None
        if provider_failure is not None:
            await self._fail_provider(
                workflow_run_id,
                provider_failure,
                expected_status=ChapterProductionStatus.DRAFTING,
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
            revised_content = merge_segment_replacements(source_content, segment_map, replacements)
            await self._revalidate_revision_prewrite(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                source_document_id=context.document.id,
                source_version_id=context.version.id,
                source_content_hash=context.version.content_hash,
                feedback=feedback,
                actor_user_id=actor_user_id,
                expected_attempt_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_checkpoint_index=attempt_checkpoint_index,
            )
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
                change_summary="Applied a Chapter Production V2 feedback revision.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": operation_key,
                    "attempt_id": attempt_id,
                },
            )
        except _StaleActionAdopted as adopted:
            return adopted.result
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
                expected_kind="feedback",
                expected_checkpoint_index=attempt_checkpoint_index,
                restore_feedback=True,
            )
            raise
        except ChapterProductionV2ValidationError:
            await self._release_attempt(
                workflow_run_id,
                expected_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_kind="feedback",
                expected_checkpoint_index=attempt_checkpoint_index,
                restore_feedback=True,
            )
            raise
        except Exception:
            await self._release_attempt(
                workflow_run_id,
                expected_key=operation_key,
                expected_attempt_id=attempt_id,
                expected_kind="feedback",
                expected_checkpoint_index=attempt_checkpoint_index,
                restore_feedback=True,
            )
            raise _invalid() from None
        return await self._finalize_feedback_revision(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            old_action_request_id=action_request_id,
            document_id=context.document.id,
            version_id=version.id,
            expected_parent_version_id=context.version.id,
            operation_key=operation_key,
            attempt_id=attempt_id,
            actor_user_id=actor_user_id,
        )

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
            context = await self._author_context(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
            normalized = normalize_chapter_content(content)
            if normalized != content or not normalized.strip():
                raise _invalid()
            operation_key = self._decision_operation_key(
                workflow_run_id, action_request_id, context.version.id, "manual"
            )
            self._resolve_action_row(
                context.action,
                status=ActionRequestStatus.REVISED,
                decision=ChapterActionDecision.SUBMIT_MANUAL_EDIT,
                actor_user_id=actor_user_id,
            )
            _validated_prospective_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=context.document.id,
                version_id=context.version.id,
                content=normalized,
            )
            version = await self.documents.write_document(
                document_id=context.document.id,
                content=normalized,
                source=DocumentSource.USER,
                expected_current_version_id=context.version.id,
                actor_user_id=actor_user_id,
                workflow_run_id=workflow_run_id,
                change_summary="Applied an authorized Chapter Production V2 manual edit.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": operation_key,
                },
            )
        except _StaleActionAdopted as adopted:
            return adopted.result
        except DocumentCommitIndeterminateError:
            await self._rollback()
            raise ChapterProductionV2CommitIndeterminateError() from None
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
            raise _invalid() from None
        return await self._finalize_manual_edit(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_request_id=action_request_id,
            actor_user_id=actor_user_id,
            document_id=context.document.id,
            version_id=version.id,
            old_binding=context.binding,
            expected_parent_version_id=context.version.id,
            operation_key=operation_key,
            finalize_actor_user_id=actor_user_id,
        )

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
                if state.document_id is None:
                    if chapter.current_draft_document_id is not None:
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
            initial_candidate = (
                state.status is ChapterProductionStatus.DRAFTING and state.document_id is None
            )
            if (
                document.current_version_id != version.id
                or (
                    initial_candidate
                    and chapter.current_draft_document_id not in {None, document.id}
                )
                or (not initial_candidate and chapter.current_draft_document_id != document.id)
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
                    await self._finalize_draft(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=run.id,
                        draft=document,
                        version=version,
                        actor_user_id=actor_user_id,
                    )
                else:
                    old_action = await self._resolved_source_action(run.id, state)
                    await self._finalize_feedback_revision(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=run.id,
                        old_action_request_id=old_action.id,
                        document_id=document.id,
                        version_id=version.id,
                        expected_parent_version_id=UUID(state.document_version_id),
                        operation_key=operation_key,
                        attempt_id=version.metadata_["attempt_id"],
                        actor_user_id=actor_user_id,
                    )
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
                await self._finalize_manual_edit(
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
            raise ChapterProductionV2ReconciliationError() from None
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

        try:
            self._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
            if not _valid_sha256(expected_attempt_key) or not _valid_nonzero_uuid(
                expected_attempt_id
            ):
                raise _invalid()
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
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
                if (
                    chapter.current_draft_document_id is not None
                    or attempt.get("kind") != "initial"
                ):
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

    async def _resume_with_outline_map(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        outline_map: ChapterSegmentMap,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Started:
        request_failed = False
        try:
            request = InitialDraftRequest(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                approved_outline=ApprovedOutlineReference(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=outline_map.document_id,
                    version_id=outline_map.version_id,
                ),
                allowed_segments=tuple(
                    AllowedChapterSegment(
                        segment_id=item.segment_id,
                        index=item.ordinal,
                        title=item.structural_path,
                        brief=item.content,
                    )
                    for item in outline_map.segments
                ),
            )
        except Exception:
            request_failed = True
        if request_failed:
            raise ChapterProductionV2ProviderError() from None

        attempt_key, attempt_checkpoint_index, attempt_id = await self._claim_initial_attempt(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )
        cancellation = None
        provider_failure: ChapterFailureCode | None = None
        try:
            candidate = await self.writer_agent.initial_draft(request)
        except asyncio.CancelledError as error:
            cancellation = _safe_cancelled_error(error)
        except ProviderTimeoutError:
            provider_failure = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            provider_failure = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except (
            ProviderConfigurationError,
            ProviderRateLimitedError,
            ProviderUnavailableError,
        ):
            provider_failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        except Exception:
            provider_failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await self._release_attempt(
                workflow_run_id,
                expected_key=attempt_key,
                expected_attempt_id=attempt_id,
                expected_kind="initial",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise cancellation from None
        if provider_failure is not None:
            await self._fail_provider(
                workflow_run_id,
                provider_failure,
                expected_status=ChapterProductionStatus.DRAFTING,
                expected_checkpoint_index=attempt_checkpoint_index,
                expected_attempt_key=attempt_key,
                expected_attempt_id=attempt_id,
            )
            raise ChapterProductionV2ProviderError() from None

        try:
            content = compose_initial_markdown(
                tuple(segment.content for segment in candidate.segments)
            )
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            metadata = self._run_metadata(run)
            state, checkpoint = await self._locked_state(run)
            attempt = metadata["provider_attempt"]
            if state.status is not ChapterProductionStatus.DRAFTING:
                if state.status is ChapterProductionStatus.AUTHOR_REVISION:
                    result = await self._completed_result(run, metadata)
                    await self.session.commit()
                    return result
                raise _invalid()
            if (
                checkpoint.checkpoint_index != attempt_checkpoint_index
                or type(attempt) is not dict
                or attempt.get("key") != attempt_key
                or attempt.get("attempt_id") != attempt_id
                or attempt.get("kind") != "initial"
                or attempt.get("checkpoint_index") != attempt_checkpoint_index
                or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
            ):
                raise ChapterProductionV2ReconciliationError()
            outline, outline_version = await self._outline_for_chapter(
                chapter, project_id, lock=True
            )
            self._validate_outline_metadata(metadata, outline, outline_version)
            current_map = await self.documents.derive_chapter_production_segment_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=outline.id,
                version_id=outline_version.id,
            )
            if current_map.canonical_bytes() != outline_map.canonical_bytes():
                raise _invalid()
            operation_key = metadata["operation_key"]
            if operation_key != attempt_key:
                raise ChapterProductionV2ReconciliationError()
            committed = await self._committed_draft(run.id, operation_key)
            if committed is not None:
                await self.session.commit()
                return await self._finalize_draft(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=run.id,
                    draft=committed[0],
                    version=committed[1],
                    actor_user_id=actor_user_id,
                )
            _validated_prospective_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=outline.id,
                version_id=outline_version.id,
                content=content,
            )
            draft = await self.documents.create_document(
                project_id=project_id,
                chapter_id=chapter_id,
                document_type=DocumentType.CHAPTER_DRAFT,
                title=f"Chapter {chapter.chapter_number} draft",
                path=f"chapters/chapter-{chapter.chapter_number:04d}-{run.id}-draft.md",
                content=content,
                source=DocumentSource.WRITER_AGENT,
                agent_role="writer_agent",
                workflow_run_id=run.id,
                change_summary="Generated Chapter Production V2 draft.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": operation_key,
                    "attempt_id": attempt_id,
                },
            )
            version = draft.current_version
            if version is None:
                raise _invalid()
        except DocumentCommitIndeterminateError:
            await self._rollback()
            raise ChapterProductionV2CommitIndeterminateError() from None
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ReconciliationError:
            await self._release_attempt(
                workflow_run_id,
                expected_key=attempt_key,
                expected_attempt_id=attempt_id,
                expected_kind="initial",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise
        except ChapterProductionV2ValidationError:
            await self._release_attempt(
                workflow_run_id,
                expected_key=attempt_key,
                expected_attempt_id=attempt_id,
                expected_kind="initial",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise
        except Exception:
            await self._release_attempt(
                workflow_run_id,
                expected_key=attempt_key,
                expected_attempt_id=attempt_id,
                expected_kind="initial",
                expected_checkpoint_index=attempt_checkpoint_index,
            )
            raise _invalid() from None
        return await self._finalize_draft(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            draft=draft,
            version=version,
            actor_user_id=actor_user_id,
        )

    async def _finalize_draft(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        draft: Document,
        version: DocumentVersion,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Started:
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            metadata = self._run_metadata(run)
            state, checkpoint = await self._locked_state(run)
            if state.status is ChapterProductionStatus.AUTHOR_REVISION:
                result = await self._completed_result(run, metadata)
                await self.session.commit()
                return result
            if state.status is not ChapterProductionStatus.DRAFTING:
                raise _invalid()
            attempt = metadata["provider_attempt"]
            if (
                type(attempt) is not dict
                or attempt.get("kind") != "initial"
                or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
                or attempt.get("checkpoint_index") != checkpoint.checkpoint_index
            ):
                raise ChapterProductionV2ReconciliationError()
            live_document = await self.session.scalar(
                select(Document)
                .options(selectinload(Document.project), selectinload(Document.current_version))
                .where(
                    Document.id == draft.id,
                    Document.project_id == project_id,
                    Document.chapter_id == chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
                    Document.current_version_id == version.id,
                )
                .with_for_update()
            )
            live_version = await self.session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == version.id,
                    DocumentVersion.document_id == draft.id,
                    DocumentVersion.workflow_run_id == run.id,
                    DocumentVersion.source == DocumentSource.WRITER_AGENT.value,
                    DocumentVersion.agent_role == "writer_agent",
                    DocumentVersion.parent_version_id.is_(None),
                )
                .with_for_update()
            )
            if live_document is None or live_version is None:
                raise _invalid()
            if live_version.metadata_ != {
                "contract_version": _CONTRACT_VERSION,
                "operation_key": metadata["operation_key"],
                "attempt_id": attempt["attempt_id"],
            }:
                raise _invalid()
            await self.documents.derive_chapter_production_segment_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=live_document.id,
                version_id=live_version.id,
            )
            pending = list(
                await self.session.scalars(
                    select(ActionRequest)
                    .where(
                        ActionRequest.workflow_run_id == run.id,
                        ActionRequest.status == ActionRequestStatus.PENDING.value,
                    )
                    .with_for_update()
                )
            )
            if pending:
                raise _invalid()
            action = ActionRequest(
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
                    "document_id": str(live_document.id),
                    "document_version_id": str(live_version.id),
                    "content_hash": live_version.content_hash,
                    "operation_key": metadata["operation_key"],
                },
            )
            self.session.add(action)
            await self.session.flush()
            binding = ChapterActionBinding(
                action_request_id=str(action.id),
                workflow_run_id=str(run.id),
                chapter_id=str(chapter.id),
                request_type=action.request_type,
                kind=ChapterActionKind.AUTHOR_REVISION,
                status=ActionRequestStatus.PENDING,
                pending_count=1,
                document_id=str(live_document.id),
                document_version_id=str(live_version.id),
                content_hash=live_version.content_hash,
                current_document_id=str(live_document.id),
                current_document_version_id=str(live_version.id),
                current_content_hash=live_version.content_hash,
            )
            next_state = state.submit_draft(
                document_id=str(live_document.id),
                document_version_id=str(live_version.id),
                content_hash=live_version.content_hash,
                action=binding,
            )
            chapter.current_draft_document_id = live_document.id
            self._set_attempt(run, None)
            self._project_state(run, next_state)
            self.session.add(
                WorkflowCheckpoint(
                    workflow_run_id=run.id,
                    checkpoint_index=checkpoint.checkpoint_index + 1,
                    node_name=next_state.current_node,
                    state_json=next_state.to_checkpoint(),
                )
            )
            await self._commit()
            return ChapterProductionV2Started(
                workflow_run_id=run.id,
                action_request_id=action.id,
                outline_document_id=UUID(metadata["outline_document_id"]),
                outline_version_id=UUID(metadata["outline_version_id"]),
                draft_document_id=live_document.id,
                draft_version_id=live_version.id,
            )
        except ChapterProductionV2CommitIndeterminateError:
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
                reports.append(report)
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
        kind = attempt.get("kind")
        if state.document_id is None:
            return (
                kind == "initial"
                and version.parent_version_id is None
                and version.source == DocumentSource.WRITER_AGENT.value
                and version.actor_user_id is None
                and version.agent_role == "writer_agent"
                and attempt.get("key") == self._run_metadata(run)["operation_key"]
            )
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
                    ActionRequest.status == ActionRequestStatus.REVISED.value,
                )
                .with_for_update()
            )
        )
        matches = [
            action
            for action in actions
            if action.metadata_.get("document_id") == state.document_id
            and action.metadata_.get("document_version_id") == state.document_version_id
        ]
        if len(matches) != 1:
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

    async def _claim_initial_attempt(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> tuple[str, int, str]:
        await self._require_project_owner(project_id, actor_user_id)
        await self._chapter(project_id, chapter_id, lock=True)
        run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self._locked_state(run)
        metadata = self._run_metadata(run)
        key = metadata["operation_key"]
        attempt = metadata["provider_attempt"]
        if state.status is ChapterProductionStatus.FAILED:
            failed_attempt_checkpoint = (
                attempt.get("checkpoint_index") if type(attempt) is dict else None
            )
            if (
                state.failed_from_status is not ChapterProductionStatus.DRAFTING
                or state.document_id is not None
                or state.document_version_id is not None
                or state.failure_code
                not in {
                    ChapterFailureCode.PROVIDER_UNAVAILABLE,
                    ChapterFailureCode.PROVIDER_TIMEOUT,
                    ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
                }
                or type(attempt) is not dict
                or attempt.get("key") != key
                or attempt.get("kind") != "initial"
                or attempt.get("status") != _ATTEMPT_STATUS_FAILED
                or type(failed_attempt_checkpoint) is not int
                or checkpoint.checkpoint_index != failed_attempt_checkpoint + 1
            ):
                raise ChapterProductionV2ReconciliationError()
            state = state.recover()
            self._append_state(run, checkpoint, state)
            checkpoint_index = checkpoint.checkpoint_index + 1
        else:
            if (
                state.status is not ChapterProductionStatus.DRAFTING
                or state.document_id is not None
            ):
                raise _invalid()
            checkpoint_index = checkpoint.checkpoint_index
            if attempt is not None:
                raise ChapterProductionV2ReconciliationError()
        attempt_id = _new_attempt_id()
        self._set_attempt(
            run,
            self._attempt_payload(
                attempt_id=attempt_id,
                key=key,
                kind="initial",
                checkpoint_index=checkpoint_index,
            ),
        )
        await self._commit()
        return key, checkpoint_index, attempt_id

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
        self._validated_ids(project_id, actor_user_id)
        statement = select(Project.id).where(
            Project.id == project_id,
            Project.owner_id == actor_user_id,
        )
        if lock:
            statement = statement.with_for_update()
        authorized = await self.session.scalar(statement)
        if authorized is None:
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

    async def _revalidate_revision_prewrite(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        source_document_id: UUID,
        source_version_id: UUID,
        source_content_hash: str,
        feedback: str,
        actor_user_id: UUID,
        expected_attempt_key: str,
        expected_attempt_id: str,
        expected_checkpoint_index: int,
    ) -> None:
        await self._require_project_owner(project_id, actor_user_id)
        chapter = await self._chapter(project_id, chapter_id, lock=True)
        run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self._locked_state(run)
        attempt = self._run_metadata(run)["provider_attempt"]
        if (
            type(attempt) is not dict
            or attempt.get("key") != expected_attempt_key
            or attempt.get("attempt_id") != expected_attempt_id
            or attempt.get("kind") != "feedback"
            or attempt.get("checkpoint_index") != expected_checkpoint_index
            or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
        ):
            raise ChapterProductionV2ReconciliationError()
        if (
            state.status is not ChapterProductionStatus.DRAFTING
            or state.awaiting_user
            or state.document_id != str(source_document_id)
            or state.document_version_id != str(source_version_id)
            or state.content_hash != source_content_hash
            or checkpoint.checkpoint_index != expected_checkpoint_index
        ):
            raise _invalid()
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_request_id,
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.status == ActionRequestStatus.REVISED.value,
                ActionRequest.user_decision == ChapterActionDecision.REQUEST_REVISION.value,
            )
            .with_for_update()
        )
        if (
            action is None
            or action.user_feedback != feedback
            or action.resolved_by_id is None
            or str(action.resolved_by_id) != str(actor_user_id)
            or action.metadata_.get("document_id") != str(source_document_id)
            or action.metadata_.get("document_version_id") != str(source_version_id)
        ):
            raise _invalid()
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == source_document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id == source_version_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == source_version_id,
                DocumentVersion.document_id == source_document_id,
                DocumentVersion.content_hash == source_content_hash,
            )
            .with_for_update()
        )
        if document is None or version is None or chapter.current_draft_document_id != document.id:
            raise _invalid()
        await self.documents.derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
        )

    async def _finalize_feedback_revision(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        old_action_request_id: UUID,
        document_id: UUID,
        version_id: UUID,
        expected_parent_version_id: UUID,
        operation_key: str,
        attempt_id: str,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        try:
            await self._require_project_owner(project_id, actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            if state.status is not ChapterProductionStatus.DRAFTING or state.awaiting_user:
                raise _invalid()
            attempt = self._run_metadata(run)["provider_attempt"]
            if (
                type(attempt) is not dict
                or attempt.get("key") != operation_key
                or attempt.get("attempt_id") != attempt_id
                or attempt.get("kind") != "feedback"
                or attempt.get("checkpoint_index") != checkpoint.checkpoint_index
                or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
            ):
                raise ChapterProductionV2ReconciliationError()
            old_action = await self.session.scalar(
                select(ActionRequest)
                .where(
                    ActionRequest.id == old_action_request_id,
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status == ActionRequestStatus.REVISED.value,
                )
                .with_for_update()
            )
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
            if old_action is None or chapter.current_draft_document_id != document.id:
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
            action = self._new_author_action(
                run=run,
                project_id=project_id,
                chapter_id=chapter_id,
                document=document,
                version=version,
                operation_key=operation_key,
            )
            self.session.add(action)
            await self.session.flush()
            binding = self._binding_for_new_action(
                action=action,
                run=run,
                chapter_id=chapter_id,
                document=document,
                version=version,
            )
            next_state = state.submit_draft(
                document_id=str(document.id),
                document_version_id=str(version.id),
                content_hash=version.content_hash,
                action=binding,
            )
            self._set_attempt(run, None)
            self._append_state(run, checkpoint, next_state)
            await self._commit()
            return ChapterProductionV2Updated(
                workflow_run_id=run.id,
                draft_document_id=document.id,
                draft_version_id=version.id,
                action_request_id=action.id,
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

    async def _finalize_manual_edit(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        version_id: UUID,
        old_binding: ChapterActionBinding,
        expected_parent_version_id: UUID,
        operation_key: str,
        finalize_actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        try:
            await self._require_project_owner(project_id, finalize_actor_user_id)
            chapter = await self._chapter(project_id, chapter_id, lock=True)
            await self._require_project_owner(project_id, actor_user_id)
            run = await self._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await self._locked_state(run)
            action = await self.session.scalar(
                select(ActionRequest)
                .where(
                    ActionRequest.id == action_request_id,
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status == ActionRequestStatus.REVISED.value,
                    ActionRequest.user_decision == ChapterActionDecision.SUBMIT_MANUAL_EDIT.value,
                    ActionRequest.resolved_by_id == actor_user_id,
                )
                .with_for_update()
            )
            document, version = await self._locked_current_revision(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=run.id,
                document_id=document_id,
                version_id=version_id,
                parent_version_id=expected_parent_version_id,
                source=DocumentSource.USER,
                actor_user_id=actor_user_id,
                agent_role=None,
                operation_key=operation_key,
            )
            if action is None or chapter.current_draft_document_id != document.id:
                raise _invalid()
            next_state = state.resolve_action(
                action=old_binding,
                decision=ChapterActionDecision.SUBMIT_MANUAL_EDIT,
                document_id=str(document.id),
                document_version_id=str(version.id),
                content_hash=version.content_hash,
            )
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
        except ChapterProductionV2ValidationError:
            await self._rollback()
            raise
        except Exception:
            await self._rollback()
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

    async def _approved_outline(
        self, project_id: UUID, chapter_id: UUID, *, lock: bool
    ) -> tuple[Chapter, Document, DocumentVersion]:
        chapter = await self._chapter(project_id, chapter_id, lock=lock)
        outline, version = await self._outline_for_chapter(chapter, project_id, lock=lock)
        return chapter, outline, version

    async def _outline_for_chapter(
        self, chapter: Chapter, project_id: UUID, *, lock: bool
    ) -> tuple[Document, DocumentVersion]:
        if chapter.status != "OUTLINE_APPROVED" or chapter.current_outline_document_id is None:
            raise _invalid()
        statement = (
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == chapter.current_outline_document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter.id,
                Document.type.in_(
                    (
                        DocumentType.CHAPTER_SELECTED_OUTLINE.value,
                        DocumentType.CHAPTER_OUTLINE_OPTIONS.value,
                    )
                ),
            )
        )
        if lock:
            statement = statement.with_for_update()
        outline = await self.session.scalar(statement)
        if outline is None or outline.current_version_id is None:
            raise _invalid()
        version_statement = select(DocumentVersion).where(
            DocumentVersion.id == outline.current_version_id,
            DocumentVersion.document_id == outline.id,
        )
        if lock:
            version_statement = version_statement.with_for_update()
        version = await self.session.scalar(version_statement)
        if version is None:
            raise _invalid()
        return outline, version

    async def _chapter(self, project_id: UUID, chapter_id: UUID, *, lock: bool) -> Chapter:
        try:
            project_id, chapter_id = (UUID(str(value)) for value in (project_id, chapter_id))
        except (AttributeError, TypeError, ValueError):
            raise _invalid() from None
        if any(value.int == 0 for value in (project_id, chapter_id)):
            raise _invalid()
        if lock:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"chapter-production-v2:{chapter_id}"},
            )
        statement = select(Chapter).where(
            Chapter.id == chapter_id, Chapter.project_id == project_id
        )
        if lock:
            statement = statement.with_for_update()
        chapter = await self.session.scalar(statement)
        if chapter is None:
            raise _invalid()
        return chapter

    async def _run(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, lock: bool
    ) -> WorkflowRun:
        try:
            project_id, chapter_id, workflow_run_id = (
                UUID(str(value)) for value in (project_id, chapter_id, workflow_run_id)
            )
        except (AttributeError, TypeError, ValueError):
            raise _invalid() from None
        if any(value.int == 0 for value in (project_id, chapter_id, workflow_run_id)):
            raise _invalid()
        statement = select(WorkflowRun).where(
            WorkflowRun.id == workflow_run_id,
            WorkflowRun.project_id == project_id,
            WorkflowRun.chapter_id == chapter_id,
            WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
        )
        if lock:
            statement = statement.with_for_update()
        run = await self.session.scalar(statement)
        if run is None or run.metadata_.get("contract_version") != _CONTRACT_VERSION:
            raise _invalid()
        return run

    async def _operation_run(self, chapter_id: UUID, operation_key: str) -> WorkflowRun | None:
        runs = list(
            await self.session.scalars(
                select(WorkflowRun)
                .where(
                    WorkflowRun.chapter_id == chapter_id,
                    WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
                )
                .with_for_update()
            )
        )
        matches = [
            run
            for run in runs
            if run.metadata_.get("contract_version") == _CONTRACT_VERSION
            and run.metadata_.get("operation_key") == operation_key
        ]
        if len(matches) > 1:
            raise _invalid()
        active_other = [
            run
            for run in runs
            if run.metadata_.get("contract_version") == _CONTRACT_VERSION
            and run.metadata_.get("operation_key") != operation_key
            and run.status
            not in {
                ChapterProductionStatus.COMPLETED.value,
                ChapterProductionStatus.CANCELLED.value,
            }
        ]
        if active_other:
            raise _invalid()
        return matches[0] if matches else None

    async def _locked_state(
        self, run: WorkflowRun
    ) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
        checkpoints = list(
            await self.session.scalars(
                select(WorkflowCheckpoint)
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
        try:
            state = ChapterProductionState.from_checkpoint(checkpoint.state_json)
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
        except ChapterProductionValidationError:
            raise _invalid() from None
        return state, checkpoint

    async def _committed_draft(
        self, run_id: UUID, operation_key: str
    ) -> tuple[Document, DocumentVersion] | None:
        versions = list(
            await self.session.scalars(
                select(DocumentVersion).where(DocumentVersion.workflow_run_id == run_id)
            )
        )
        matches = [
            version
            for version in versions
            if version.metadata_.get("contract_version") == _CONTRACT_VERSION
            and version.metadata_.get("operation_key") == operation_key
            and _valid_nonzero_uuid(version.metadata_.get("attempt_id"))
            and set(version.metadata_) == {"contract_version", "operation_key", "attempt_id"}
        ]
        if len(matches) > 1:
            raise _invalid()
        if not matches:
            return None
        version = matches[0]
        document = await self.session.get(Document, version.document_id)
        if document is None or document.current_version_id != version.id:
            raise _invalid()
        return document, version

    async def _completed_result(
        self, run: WorkflowRun, metadata: dict[str, str]
    ) -> ChapterProductionV2Started:
        state, _ = await self._locked_state(run)
        if (
            state.status is not ChapterProductionStatus.AUTHOR_REVISION
            or state.action_request_id is None
            or state.document_id is None
            or state.document_version_id is None
        ):
            raise _invalid()
        pending = list(
            await self.session.scalars(
                select(ActionRequest).where(
                    ActionRequest.id == UUID(state.action_request_id),
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
            )
        )
        if len(pending) != 1:
            raise _invalid()
        return ChapterProductionV2Started(
            workflow_run_id=run.id,
            action_request_id=pending[0].id,
            outline_document_id=UUID(metadata["outline_document_id"]),
            outline_version_id=UUID(metadata["outline_version_id"]),
            draft_document_id=UUID(state.document_id),
            draft_version_id=UUID(state.document_version_id),
        )

    @staticmethod
    def _project_state(run: WorkflowRun, state: ChapterProductionState) -> None:
        run.status = state.status.value
        run.current_node = state.current_node
        run.awaiting_user = state.awaiting_user
        run.next_node = None

    @staticmethod
    def _operation_key(
        *,
        project_id: UUID,
        chapter_id: UUID,
        outline_document_id: UUID,
        outline_version_id: UUID,
        outline_content_hash: str,
    ) -> str:
        return sha256_content(
            ":".join(
                (
                    _CONTRACT_VERSION,
                    str(project_id),
                    str(chapter_id),
                    str(outline_document_id),
                    str(outline_version_id),
                    outline_content_hash,
                    CURRENT_CHAPTER_SEGMENTER_VERSION,
                )
            )
        )

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
        }
        if (
            type(metadata) is not dict
            or set(metadata) != expected
            or metadata.get("contract_version") != _CONTRACT_VERSION
            or metadata.get("review_policy_version") != _REVIEW_POLICY_VERSION
            or metadata.get("chief_editor_required") is not True
            or metadata.get("segmenter_version") != CURRENT_CHAPTER_SEGMENTER_VERSION
            or not ChapterProductionV2Service._attempt_metadata_is_valid(
                metadata.get("provider_attempt")
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
    "ChapterProductionV2ProviderError",
    "ChapterProductionV2ReconciliationError",
    "ChapterProductionV2Service",
    "ChapterProductionV2Started",
    "ChapterProductionV2Updated",
    "ChapterProductionV2ValidationError",
    "compose_initial_markdown",
    "merge_segment_replacements",
]
