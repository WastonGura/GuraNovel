"""Review report persistence and action-binding primitives for Chapter Production V2."""

from __future__ import annotations

import traceback
from uuid import UUID

from sqlalchemy import func, select

from app.agents import ChapterReviewReport, ReviewFindingSeverity, ReviewerRole
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentVersion,
    ReviewReport,
    WorkflowEvent,
    WorkflowRun,
)
from app.services.chapter_production_v2_contracts import (
    CONTRACT_VERSION,
    REVIEWER_CLAIM_STATUS_CLAIMED,
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_review_claim import (
    ReviewClaimContext,
    build_review_context_locked,
    set_reviewer_claim,
)
from app.services.chapter_review_protocols import ChapterReviewService
from app.services.chapter_review_validation import (
    new_review_action,
    review_action_metadata,
    validated_persisted_review_report,
    validated_resolved_review_action,
)
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterReviewBinding,
    ChapterReviewOutcome,
    ChapterReviewStage,
)


_REVIEW_EVENT_TYPE = "chapter_review_recorded"


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()









def _review_outcome(persisted: ReviewReport) -> ChapterReviewOutcome:
    return (
        ChapterReviewOutcome.BLOCKING
        if persisted.blocking_issues
        else ChapterReviewOutcome.WARNING
        if persisted.warnings
        else ChapterReviewOutcome.PASSED
    )


def _validated_persist_review_claim(
    *,
    claim: object,
    context: ReviewClaimContext,
    checkpoint_index: int,
    expected_operation_key: str,
    expected_claim_id: str,
    expected_request_hash: str,
    report: ChapterReviewReport,
    project_id: UUID,
    chapter_id: UUID,
) -> None:
    if (
        type(claim) is not dict
        or claim.get("status") != REVIEWER_CLAIM_STATUS_CLAIMED
        or claim.get("claim_id") != expected_claim_id
        or claim.get("operation_key") != expected_operation_key
        or claim.get("request_hash") != expected_request_hash
        or claim.get("checkpoint_index") != checkpoint_index
        or claim.get("stage") != context.stage.value
        or context.operation_key != expected_operation_key
        or context.request_hash != expected_request_hash
        or report.project_id != project_id
        or report.chapter_id != chapter_id
        or report.workflow_run_id != context.run.id
        or report.target_document_id != context.document.id
        or report.target_version_id != context.version.id
        or report.reviewer_role.value
        != {
            ChapterReviewStage.EDITOR: ReviewerRole.EDITOR.value,
            ChapterReviewStage.CHIEF_EDITOR: ReviewerRole.CHIEF_EDITOR.value,
            ChapterReviewStage.LORE: ReviewerRole.LORE.value,
        }[context.stage]
    ):
        raise ChapterProductionV2ReconciliationError()


async def _persist_review_report_row(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    report: ChapterReviewReport,
    run: WorkflowRun,
    context: ReviewClaimContext,
    expected_operation_key: str,
    expected_claim_id: str,
    expected_request_hash: str,
) -> ReviewReport:
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
            "contract_version": CONTRACT_VERSION,
            "operation_key": expected_operation_key,
            "request_hash": expected_request_hash,
            "segment_map_hash": context.segment_map.map_hash,
            "segmenter_version": context.segment_map.segmenter_version,
        },
    )
    service.session.add(persisted)
    await service.session.flush()
    return persisted


async def _persist_review_action(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    run: WorkflowRun,
    chapter_id: UUID,
    context: ReviewClaimContext,
    persisted: ReviewReport,
    outcome: ChapterReviewOutcome,
    expected_operation_key: str,
) -> tuple[ActionRequest | None, ChapterActionBinding | None]:
    if outcome is ChapterReviewOutcome.PASSED:
        return None, None
    pending_count = await service.session.scalar(
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
    action = new_review_action(
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
    service.session.add(action)
    await service.session.flush()
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
    return action, action_binding


async def _persist_review_transition(
    service: ChapterReviewService,
    *,
    run: WorkflowRun,
    checkpoint: object,
    state: ChapterProductionState,
    chapter_id: UUID,
    context: ReviewClaimContext,
    persisted: ReviewReport,
    outcome: ChapterReviewOutcome,
    action: ActionRequest | None,
    action_binding: ChapterActionBinding | None,
    report: ChapterReviewReport,
) -> ChapterProductionV2Updated:
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
    set_reviewer_claim(run, None)
    if context.stage is ChapterReviewStage.LORE and outcome is ChapterReviewOutcome.PASSED:
        next_state = await service._enter_revision_ready_locked(
            run=run,
            checkpoint=checkpoint,
            state=next_state,
            document=context.document,
            version=context.version,
        )
    else:
        service._append_state(run, checkpoint, next_state)
    service.session.add(
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
    await service._commit()
    return ChapterProductionV2Updated(
        workflow_run_id=run.id,
        draft_document_id=context.document.id,
        draft_version_id=context.version.id,
        action_request_id=action.id if action is not None else None,
    )


async def _persist_current_review_locked(
    service: ChapterReviewService,
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
    await service._require_project_owner(project_id, actor_user_id)
    chapter = await service._chapter(project_id, chapter_id, lock=True)
    run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
    state, checkpoint = await service._locked_state(run)
    context = await build_review_context_locked(
        service=service,
        project_id=project_id,
        chapter_id=chapter_id,
        chapter=chapter,
        run=run,
        state=state,
        checkpoint=checkpoint,
    )
    claim = service._run_metadata(run)["reviewer_claim"]
    _validated_persist_review_claim(
        claim=claim,
        context=context,
        checkpoint_index=checkpoint.checkpoint_index,
        expected_operation_key=expected_operation_key,
        expected_claim_id=expected_claim_id,
        expected_request_hash=expected_request_hash,
        report=report,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if await service._exact_review_report_count(
        run=run, version=context.version, stage=context.stage
    ) != 0:
        raise ChapterProductionV2ReconciliationError()
    persisted = await _persist_review_report_row(
        service=service,
        project_id=project_id,
        chapter_id=chapter_id,
        report=report,
        run=run,
        context=context,
        expected_operation_key=expected_operation_key,
        expected_claim_id=expected_claim_id,
        expected_request_hash=expected_request_hash,
    )
    outcome = _review_outcome(persisted)
    action, action_binding = await _persist_review_action(
        service=service,
        project_id=project_id,
        run=run,
        chapter_id=chapter_id,
        context=context,
        persisted=persisted,
        outcome=outcome,
        expected_operation_key=expected_operation_key,
    )
    return await _persist_review_transition(
        service=service,
        run=run,
        checkpoint=checkpoint,
        state=state,
        chapter_id=chapter_id,
        context=context,
        persisted=persisted,
        outcome=outcome,
        action=action,
        action_binding=action_binding,
        report=report,
    )


async def persist_current_review(
    service: ChapterReviewService,
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
        return await _persist_current_review_locked(
            service=service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            expected_operation_key=expected_operation_key,
            expected_claim_id=expected_claim_id,
            expected_request_hash=expected_request_hash,
            report=report,
        )
    except ChapterProductionV2CommitIndeterminateError:
        raise
    except ChapterProductionV2ReconciliationError:
        await service._rollback()
        raise
    except ChapterProductionV2ValidationError:
        await service._rollback()
        raise
    except Exception as error:
        await service._rollback()
        if type(error) is TypeError:
            _frames = [item.name for item in traceback.extract_tb(error.__traceback__)]
            print(
                f"DEBUG persist_current_review error_type=TypeError "
                f"signature={str(error)} frames={_frames}"
            )
        else:
            _tb = error.__traceback__
            while _tb is not None and _tb.tb_next is not None:
                _tb = _tb.tb_next
            _frame_name = (
                _tb.tb_frame.f_code.co_name
                if _tb is not None and _tb.tb_frame is not None
                else None
            )
            print(
                f"DEBUG persist_current_review error_type={type(error).__name__} "
                f"frame={_frame_name}"
            )
        raise _invalid() from None


async def _resolve_review_action_pending(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    run: WorkflowRun,
    action_request_id: UUID,
) -> tuple[ActionRequest, int]:
    action = await service.session.scalar(
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
    pending_count = await service.session.scalar(
        select(func.count())
        .select_from(ActionRequest)
        .where(
            ActionRequest.workflow_run_id == run.id,
            ActionRequest.status == ActionRequestStatus.PENDING.value,
        )
    )
    if action is None or pending_count != 1:
        raise _invalid()
    return action, int(pending_count)


def _validated_resolve_action_metadata(
    metadata: dict[str, str], state: ChapterProductionState
) -> None:
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


async def _resolve_review_action_report(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    run: WorkflowRun,
    state: ChapterProductionState,
    chapter: object,
    action: ActionRequest,
    metadata: dict[str, str],
    pending_count: int,
) -> tuple[Document, DocumentVersion, ReviewReport, ChapterReviewStage, ChapterActionBinding]:
    document, version = await service._locked_review_document(
        project_id=project_id,
        chapter_id=chapter_id,
        state=state,
        chapter=chapter,
    )
    report = await service.session.scalar(
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
    await validated_persisted_review_report(
        service=service,
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
    return document, version, report, stage, binding


async def _resolve_review_action_apply(
    service: ChapterReviewService,
    *,
    run: WorkflowRun,
    checkpoint: object,
    chapter: object,
    state: ChapterProductionState,
    action: ActionRequest,
    document: Document,
    version: DocumentVersion,
    stage: ChapterReviewStage,
    binding: ChapterActionBinding,
    typed_decision: ChapterActionDecision,
    actor_user_id: UUID,
) -> ChapterProductionV2Updated:
    next_state = state.resolve_action(action=binding, decision=typed_decision)
    service._resolve_action_row(
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
        next_state = await service._enter_revision_ready_locked(
            run=run,
            checkpoint=checkpoint,
            state=next_state,
            document=document,
            version=version,
        )
    else:
        service._append_state(run, checkpoint, next_state)
    service.session.add(
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
    await service._commit()
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


async def _resolve_review_action_locked(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_request_id: UUID,
    typed_decision: ChapterActionDecision,
    actor_user_id: UUID,
) -> ChapterProductionV2Updated:
    await service._require_project_owner(project_id, actor_user_id)
    chapter = await service._chapter(project_id, chapter_id, lock=True)
    run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
    state, checkpoint = await service._locked_state(run)
    if (
        not state.awaiting_user
        or state.action_request_id != str(action_request_id)
        or state.action_kind
        not in {ChapterActionKind.REVIEW_WARNING, ChapterActionKind.REVIEW_REVISION}
    ):
        raise _invalid()
    action, pending_count = await _resolve_review_action_pending(
        service=service,
        project_id=project_id,
        chapter_id=chapter_id,
        run=run,
        action_request_id=action_request_id,
    )
    metadata = review_action_metadata(action)
    _validated_resolve_action_metadata(metadata, state)
    document, version, report, stage, binding = await _resolve_review_action_report(
        service=service,
        project_id=project_id,
        chapter_id=chapter_id,
        run=run,
        state=state,
        chapter=chapter,
        action=action,
        metadata=metadata,
        pending_count=pending_count,
    )
    return await _resolve_review_action_apply(
        service=service,
        run=run,
        checkpoint=checkpoint,
        chapter=chapter,
        state=state,
        action=action,
        document=document,
        version=version,
        stage=stage,
        binding=binding,
        typed_decision=typed_decision,
        actor_user_id=actor_user_id,
    )


async def resolve_review_action_locked(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_request_id: UUID,
    typed_decision: ChapterActionDecision,
    actor_user_id: UUID,
) -> ChapterProductionV2Updated:
    try:
        return await _resolve_review_action_locked(
            service=service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_request_id=action_request_id,
            typed_decision=typed_decision,
            actor_user_id=actor_user_id,
        )
    except ChapterProductionV2CommitIndeterminateError:
        raise
    except ChapterProductionV2ValidationError:
        await service._rollback()
        raise
    except Exception:
        await service._rollback()
        raise _invalid() from None


__all__ = [
    "new_review_action",
    "persist_current_review",
    "resolve_review_action_locked",
    "review_action_metadata",
    "validated_persisted_review_report",
    "validated_resolved_review_action",
]
