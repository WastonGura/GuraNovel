"""Scoped recovery and state-loading orchestration for Chapter Production V2.

This module owns the facade's durable-state rehydration and failure-recovery
bodies.  Every method receives the facade ``service`` as its first argument
after ``self`` and reuses the facade's shared lock/query/commit primitives, so
the recovery layer never imports the facade and never providers or graphs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.documents.chapter_segments import MAX_CHAPTER_CONTENT_BYTES
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
from app.services.author_accept_coordination import _StaleActionAdopted
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
    valid_sha256 as _valid_sha256,
)
from app.services.chapter_review_validation import (
    validated_persisted_review_report,
    validated_resolved_review_action,
)
from app.services.feedback_candidate_saga import _restore_feedback_without_write
from app.services.manual_edit_saga import _resolved_source_action
from app.services.review_revision_saga import _reconciliation_candidates
from app.services.revision_readiness_store import _ReviewStateReferences
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
    ChapterReviewStage,
)
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path

_CONTRACT_VERSION = "chapter-production-v2"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"
_ATTEMPT_STATUS_CLAIMED = "claimed"
_ATTEMPT_STATUS_FAILED = "failed"


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconciliation() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


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
    segment_map: object
    reports: tuple[ReviewReport, ...]


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


def verified_snapshot_content(
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


class _ChapterProductionRecoveryImpl:
    """Recovery and state-loading bodies moved out of the V2 facade."""

    def __init__(self, service: object) -> None:
        self.service = service

    async def load_state(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        """Load only the exact latest V2 checkpoint and validate its run projection."""

        try:
            service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
            await service._require_project_owner(project_id, actor_user_id)
            await service._chapter(project_id, chapter_id, lock=False)
            run = await service._run(project_id, chapter_id, workflow_run_id, lock=False)
            state, _ = await self.locked_state(service, run)
            await service.session.commit()
            return state
        except ChapterProductionV2ValidationError:
            await service._rollback()
            raise
        except Exception:
            await service._rollback()
            raise _invalid() from None

    async def reconcile_review_route(
        self,
        service: object,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        await service._require_project_owner(project_id, actor_user_id)
        await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, _ = await self.locked_state(service, run)
        if state.status is not ChapterProductionStatus.EDITOR_REVIEW:
            raise ChapterProductionV2ReconciliationError()
        candidates = await _reconciliation_candidates(
            service, run,
            parent_version_id=(
                UUID(state.document_version_id)
                if state.document_version_id is not None
                else None
            ),
        )
        if len(candidates) > 1 or candidates:
            raise ChapterProductionV2ReconciliationError()
        attempt = service._run_metadata(run)["provider_attempt"]
        if type(attempt) is dict and attempt.get("status") == _ATTEMPT_STATUS_CLAIMED:
            raise ChapterProductionV2ReconciliationError()
        if state.document_id is None:
            raise ChapterProductionV2ReconciliationError()
        canonical = await service.session.scalar(
            select(Document).where(
                Document.id == UUID(state.document_id),
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.current_version_id == UUID(state.document_version_id),
            ).with_for_update()
        )
        if canonical is None:
            raise ChapterProductionV2ReconciliationError()
        await service.session.commit()
        return state

    async def fail_provider(
        self,
        service: object,
        workflow_run_id: UUID,
        failure_code: ChapterFailureCode,
        *,
        expected_status: ChapterProductionStatus,
        expected_checkpoint_index: int,
        expected_attempt_key: str,
        expected_attempt_id: str,
    ) -> bool:
        await service._rollback()
        run = await service.session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_run_id).with_for_update()
        )
        if run is None:
            return False
        state, checkpoint = await self.locked_state(service, run)
        metadata = service._run_metadata(run)
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
            await service.session.commit()
            return False
        failed = state.fail(failure_code)
        failed_attempt = dict(attempt)
        failed_attempt["status"] = _ATTEMPT_STATUS_FAILED
        service._set_attempt(run, failed_attempt)
        service._append_state(run, checkpoint, failed)
        await service._commit()
        return True

    async def recover_failed_attempt(
        self,
        service: object,
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
        await service._require_project_owner(project_id, actor_user_id)
        await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self.locked_state(service, run)
        if state.status is not ChapterProductionStatus.FAILED:
            await service.session.commit()
            return
        metadata = service._run_metadata(run)
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
            await self._recover_feedback_attempt(
                service, run, state, project_id, chapter_id, actor_user_id,
                action_request_id, feedback_hash,
            )
        elif kind == "review":
            await self._recover_review_attempt(
                service, run, state, project_id, chapter_id, report_ids, attempt,
            )
        recovered = state.recover()
        service._append_state(run, checkpoint, recovered)
        service._set_attempt(run, None)
        if restore_feedback:
            if attempt_checkpoint_index < 1:
                raise ChapterProductionV2ReconciliationError()
            await _restore_feedback_without_write(
                service,
                run,
                recovered,
                source_checkpoint_index=attempt_checkpoint_index - 1,
            )
        await service._commit()

    async def _recover_feedback_attempt(
        self,
        service: object,
        run: WorkflowRun,
        state: ChapterProductionState,
        project_id: UUID,
        chapter_id: UUID,
        actor_user_id: UUID,
        action_request_id: UUID | None,
        feedback_hash: str | None,
    ) -> None:
        action = await service.session.scalar(
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
        if (await _resolved_source_action(service, run.id, state)).id != action.id:
            raise ChapterProductionV2ReconciliationError()

    async def _recover_review_attempt(
        self,
        service: object,
        run: WorkflowRun,
        state: ChapterProductionState,
        project_id: UUID,
        chapter_id: UUID,
        report_ids: Sequence[UUID],
        attempt: dict[str, object],
    ) -> None:
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
            report = await service.session.scalar(
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
        if service._review_report_input_hash(reports) != attempt.get("report_input_hash"):
            raise ChapterProductionV2ReconciliationError()

    async def release_attempt(
        self,
        service: object,
        workflow_run_id: UUID,
        *,
        expected_key: str,
        expected_attempt_id: str,
        expected_kind: str,
        expected_checkpoint_index: int,
        restore_feedback: bool = False,
    ) -> None:
        await service._rollback()
        run = await service.session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_run_id).with_for_update()
        )
        if run is None:
            return
        metadata = service._run_metadata(run)
        attempt = metadata["provider_attempt"]
        if (
            type(attempt) is not dict
            or attempt.get("key") != expected_key
            or attempt.get("attempt_id") != expected_attempt_id
            or attempt.get("kind") != expected_kind
            or attempt.get("checkpoint_index") != expected_checkpoint_index
            or attempt.get("status") != _ATTEMPT_STATUS_CLAIMED
        ):
            await service.session.commit()
            return
        _, checkpoint = await self.locked_state(service, run)
        if checkpoint.checkpoint_index != expected_checkpoint_index:
            await service.session.commit()
            return
        service._set_attempt(run, None)
        if restore_feedback:
            state, _ = await self.locked_state(service, run)
            await _restore_feedback_without_write(service, run, state)
        await service._commit()

    async def author_context(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
    ) -> _AuthorContext:
        await service._require_project_owner(project_id, actor_user_id)
        chapter = await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self.locked_state(service, run)
        if (
            state.status is not ChapterProductionStatus.AUTHOR_REVISION
            or not state.awaiting_user
            or state.action_request_id != str(action_request_id)
            or state.action_kind is not ChapterActionKind.AUTHOR_REVISION
        ):
            raise _invalid()
        action = await service.session.scalar(
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
        pending_count = await service.session.scalar(
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
        metadata = service._action_metadata(action)
        document_id = UUID(metadata["document_id"])
        version_id = UUID(metadata["document_version_id"])
        document = await service.session.scalar(
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
        version = await service.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.content_hash == metadata["content_hash"],
            )
            .with_for_update()
        )
        if document is None:
            return await self._adopt_stale_author_context(
                service,
                run=run,
                state=state,
                checkpoint=checkpoint,
                chapter=chapter,
                action=action,
                actor_user_id=actor_user_id,
                document_id=document_id,
                version_id=version_id,
                metadata=metadata,
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
        await service.documents.derive_chapter_segment_map(
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

    async def _adopt_stale_author_context(
        self,
        service: object,
        *,
        run: WorkflowRun,
        state: ChapterProductionState,
        checkpoint: WorkflowCheckpoint,
        chapter: Chapter,
        action: ActionRequest,
        actor_user_id: UUID,
        document_id: UUID,
        version_id: UUID,
        metadata: dict[str, object],
    ) -> _AuthorContext:
        stale_document = await service.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(
                Document.id == document_id,
                Document.project_id == run.project_id,
                Document.chapter_id == run.chapter_id,
                Document.type == DocumentType.CHAPTER_DRAFT.value,
                Document.current_version_id.is_not(None),
                Document.current_version_id != version_id,
            )
            .with_for_update()
        )
        stale_version = (
            await service.session.scalar(
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
            await service.documents.derive_chapter_segment_map(
                project_id=run.project_id,
                chapter_id=run.chapter_id,
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
                content_hash=str(metadata["content_hash"]),
                current_document_id=str(stale_document.id),
                current_document_version_id=str(stale_version.id),
                current_content_hash=stale_version.content_hash,
            )
            adopted = state.reconcile_stale_action(action=stale_binding)
            service._resolve_action_row(
                action,
                status=ActionRequestStatus.CANCELLED,
                decision=ChapterActionDecision.CANCEL,
                actor_user_id=actor_user_id,
            )
            service._append_state(run, checkpoint, adopted)
            await service._commit()
            raise _StaleActionAdopted(
                ChapterProductionV2Updated(
                    workflow_run_id=run.id,
                    draft_document_id=stale_document.id,
                    draft_version_id=stale_version.id,
                    action_request_id=None,
                )
            )
        raise _invalid()

    async def review_revision_context(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        report_ids: Sequence[UUID],
        actor_user_id: UUID,
    ) -> _ReviewRevisionContext:
        await service._require_project_owner(project_id, actor_user_id)
        chapter = await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await self.locked_state(service, run)
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
        pending_count = await service.session.scalar(
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
        reports = await self._locked_review_revision_reports(
            service, report_slots, project_id, chapter_id, run, document_id, version_id
        )
        document, version = await self._locked_review_revision_document(
            service, project_id, chapter_id, document_id, version_id, state.content_hash
        )
        if document is None or version is None or chapter.current_draft_document_id != document.id:
            raise _invalid()
        segment_map = await service.documents.derive_chapter_segment_map(
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
                service,
                row=report,
                run=run,
                document=document,
                version=version,
                stage=stage,
            )
        trigger_mode = report_slots[-1][1]
        await validated_resolved_review_action(
            service,
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

    async def _locked_review_revision_reports(
        self,
        service: object,
        report_slots: tuple[tuple[UUID, str, str], ...],
        project_id: UUID,
        chapter_id: UUID,
        run: WorkflowRun,
        document_id: UUID,
        version_id: UUID,
    ) -> list[ReviewReport]:
        reports: list[ReviewReport] = []
        for report_id, expected_mode, expected_role in report_slots:
            report = await service.session.scalar(
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
        return reports

    async def _locked_review_revision_document(
        self,
        service: object,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID,
        version_id: UUID,
        content_hash: str | None,
    ) -> tuple[Document | None, DocumentVersion | None]:
        document = await service.session.scalar(
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
        version = await service.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.content_hash == content_hash,
            )
            .with_for_update()
        )
        return document, version

    async def locked_current_revision(
        self,
        service: object,
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
        document = await service.session.scalar(
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
        version = await service.session.scalar(
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
        await service.documents.derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document.id,
            version_id=version.id,
        )
        return document, version

    async def locked_review_document(
        self,
        service: object,
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
        document = await service.session.scalar(
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
        version = await service.session.scalar(
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

    async def exact_review_report_count(
        self,
        service: object,
        *,
        run: WorkflowRun,
        version: DocumentVersion,
        stage: ChapterReviewStage,
    ) -> int:
        mode, role = {
            ChapterReviewStage.EDITOR: (ReviewMode.CHAPTER_EDITOR.value, "editor_agent"),
            ChapterReviewStage.CHIEF_EDITOR: (
                ReviewMode.CHAPTER_CHIEF_FINAL.value,
                "chief_editor_agent",
            ),
            ChapterReviewStage.LORE: (ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent"),
        }[stage]
        count = await service.session.scalar(
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

    async def locked_state(
        self, service: object, run: WorkflowRun
    ) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
        service._run_metadata(run)
        checkpoints = list(
            await service.session.scalars(
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
            return await self._locked_finalized_state(service, run, checkpoint, payload)
        except (ChapterProductionValidationError, KeyError, TypeError, ValueError):
            raise _invalid() from None

    async def _locked_finalized_state(
        self,
        service: object,
        run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        payload: dict[str, object],
    ) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
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
        document = await service.session.scalar(
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
        version = await service.session.scalar(
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
        policy, editor, chief, lore = await service._live_review_bindings_locked(
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
        await service._validate_existing_ready_pair_locked(
            run=run,
            state=state,
            policy=policy,
            document=document,
            version=version,
            editor=editor,
            chief=chief,
            lore=lore,
        )
        return state, checkpoint


__all__ = [
    "_ChapterProductionRecoveryImpl",
    "_AuthorContext",
    "_ReviewRevisionContext",
    "_review_report_slots",
]
