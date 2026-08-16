"""Review report/action validation primitives for Chapter Production V2."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.agents import ChapterReviewReport, ReviewFindingSeverity
from app.documents.chapter_segments import CURRENT_CHAPTER_SEGMENTER_VERSION
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentVersion,
    ReviewMode,
    ReviewReport,
    WorkflowRun,
)
from app.services.chapter_production_v2_contracts import (
    CONTRACT_VERSION,
    REVIEW_WARNING_ACTION_TYPE,
    REVIEW_REVISION_ACTION_TYPE,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
    valid_nonzero_uuid,
    valid_sha256,
)
from app.services.chapter_review_protocols import ChapterReviewService
from app.workflows.chapter_production import (
    ChapterActionDecision,
    ChapterActionKind,
    ChapterReviewStage,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def new_review_action(
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
        request_type = REVIEW_WARNING_ACTION_TYPE
        options = ["accept_warning", "request_revision"]
        default_option = None
        prompt = "Review the warning for the current chapter version."
    elif action_kind is ChapterActionKind.REVIEW_REVISION:
        request_type = REVIEW_REVISION_ACTION_TYPE
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
            "contract_version": CONTRACT_VERSION,
            "action_kind": action_kind.value,
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "content_hash": version.content_hash,
            "operation_key": operation_key,
            "review_report_id": str(report.id),
            "review_stage": stage.value,
        },
    )


def review_action_metadata(action: ActionRequest) -> dict[str, str]:
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
        or metadata.get("contract_version") != CONTRACT_VERSION
        or metadata.get("action_kind")
        not in {
            ChapterActionKind.REVIEW_WARNING.value,
            ChapterActionKind.REVIEW_REVISION.value,
        }
        or metadata.get("review_stage") not in {item.value for item in ChapterReviewStage}
        or not valid_nonzero_uuid(metadata.get("document_id"))
        or not valid_nonzero_uuid(metadata.get("document_version_id"))
        or not valid_nonzero_uuid(metadata.get("review_report_id"))
        or not valid_sha256(metadata.get("content_hash"))
        or not valid_sha256(metadata.get("operation_key"))
        or action.status != ActionRequestStatus.PENDING.value
        or action.user_decision is not None
        or action.user_feedback is not None
        or action.resolved_by_id is not None
        or action.resolved_at is not None
    ):
        raise _invalid()
    expected_type = (
        REVIEW_WARNING_ACTION_TYPE
        if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
        else REVIEW_REVISION_ACTION_TYPE
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
    return metadata


def _review_mode_role(stage: ChapterReviewStage) -> tuple[str, str]:
    return {
        ChapterReviewStage.EDITOR: (ReviewMode.CHAPTER_EDITOR.value, "editor_agent"),
        ChapterReviewStage.CHIEF_EDITOR: (
            ReviewMode.CHAPTER_CHIEF_FINAL.value,
            "chief_editor_agent",
        ),
        ChapterReviewStage.LORE: (ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent"),
    }[stage]


def _validated_report_row_shape(
    *,
    row: ReviewReport,
    run: WorkflowRun,
    document: Document,
    version: DocumentVersion,
    mode: str,
    role: str,
) -> None:
    provenance_keys = {
        "claim_id",
        "contract_version",
        "operation_key",
        "request_hash",
        "segment_map_hash",
        "segmenter_version",
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
        or row.raw_report.get("contract_version") != CONTRACT_VERSION
        or row.raw_report.get("segmenter_version")
        != CURRENT_CHAPTER_SEGMENTER_VERSION
        or not valid_nonzero_uuid(row.raw_report.get("claim_id"))
        or not valid_sha256(row.raw_report.get("operation_key"))
        or not valid_sha256(row.raw_report.get("request_hash"))
        or not valid_sha256(row.raw_report.get("segment_map_hash"))
        or type(row.blocking_issues) is not list
        or type(row.warnings) is not list
        or type(row.notes) is not list
        or type(row.suggested_actions) is not list
    ):
        raise ChapterProductionV2ReconciliationError()


def _validated_report_findings(
    *, row: ReviewReport, segment_map: object
) -> list[dict[str, object]]:
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
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"segmenter_version", "segment_map_hash"}
                }
            )
    findings.sort(key=lambda item: item.get("sequence", 0))
    return findings


async def validated_persisted_review_report(
    service: ChapterReviewService,
    *,
    row: ReviewReport,
    run: WorkflowRun,
    document: Document,
    version: DocumentVersion,
    stage: ChapterReviewStage,
) -> ChapterReviewReport:
    mode, role = _review_mode_role(stage)
    _validated_report_row_shape(
        row=row, run=run, document=document, version=version, mode=mode, role=role
    )
    segment_map = await service.documents.derive_chapter_segment_map(
        project_id=run.project_id,
        chapter_id=run.chapter_id,
        document_id=document.id,
        version_id=version.id,
    )
    if row.raw_report["segment_map_hash"] != segment_map.map_hash:
        raise ChapterProductionV2ReconciliationError()
    findings = _validated_report_findings(row=row, segment_map=segment_map)
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


async def validated_resolved_review_action(
    service: ChapterReviewService,
    *,
    run: WorkflowRun,
    document: Document,
    version: DocumentVersion,
    report: ReviewReport,
    stage: ChapterReviewStage,
) -> ActionRequest:
    actions = list(
        await service.session.scalars(
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
        REVIEW_REVISION_ACTION_TYPE
        if action_kind is ChapterActionKind.REVIEW_REVISION
        else REVIEW_WARNING_ACTION_TYPE
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
        or metadata.get("contract_version") != CONTRACT_VERSION
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
