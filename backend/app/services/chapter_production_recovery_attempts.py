"""Recovery of failed and claimed provider attempts."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.models import (
    ActionRequest,
    ActionRequestStatus,
    ReviewReport,
    WorkflowRun,
)
from app.services.chapter_production_recovery_reconstruction import locked_state
from app.services.chapter_production_recovery_shared import (
    _ATTEMPT_STATUS_CLAIMED,
    _ATTEMPT_STATUS_FAILED,
    _review_report_slots,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
)
from app.services.feedback_candidate_saga import _restore_feedback_without_write
from app.services.manual_edit_saga import _resolved_source_action
from app.workflows.chapter_production import (
    ChapterActionDecision,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
)
from app.workspace.hashing import sha256_content


async def fail_provider(
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
    state, checkpoint = await locked_state(service, run)
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
    state, checkpoint = await locked_state(service, run)
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
        await _recover_feedback_attempt(
            service, run, state, project_id, chapter_id, actor_user_id,
            action_request_id, feedback_hash,
        )
    elif kind == "review":
        await _recover_review_attempt(
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
    _, checkpoint = await locked_state(service, run)
    if checkpoint.checkpoint_index != expected_checkpoint_index:
        await service.session.commit()
        return
    service._set_attempt(run, None)
    if restore_feedback:
        state, _ = await locked_state(service, run)
        await _restore_feedback_without_write(service, run, state)
    await service._commit()


__all__ = [
    "_recover_feedback_attempt",
    "_recover_review_attempt",
    "fail_provider",
    "recover_failed_attempt",
    "release_attempt",
]
