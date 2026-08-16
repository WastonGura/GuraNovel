"""Locked document/version and author/review context evidence."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

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
    WorkflowRun,
)
from app.services.author_accept_coordination import _StaleActionAdopted
from app.services.chapter_production_recovery_reconstruction import locked_state
from app.services.chapter_production_recovery_shared import (
    _AUTHOR_ACTION_TYPE,
    _CONTRACT_VERSION,
    _AuthorContext,
    _ReviewRevisionContext,
    _invalid,
    _review_report_slots,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2Updated,
)
from app.services.chapter_review_validation import (
    validated_persisted_review_report,
    validated_resolved_review_action,
)
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterReviewStage,
)


async def author_context(
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
    state, checkpoint = await locked_state(service, run)
    if (
        state.status is not ChapterProductionStatus.AUTHOR_REVISION
        or not state.awaiting_user
        or state.action_request_id != str(action_request_id)
        or state.action_kind is not ChapterActionKind.AUTHOR_REVISION
    ):
        raise _invalid()
    action, pending_count = await _locked_author_action(
        service,
        run=run,
        project_id=project_id,
        chapter_id=chapter_id,
        action_request_id=action_request_id,
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
    document, version = await _locked_author_document(
        service,
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document_id,
        version_id=version_id,
        content_hash=metadata["content_hash"],
    )
    if document is None:
        return await _adopt_stale_author_context(
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
        version is None
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
    binding = _build_author_binding(action, run, chapter, document, version)
    return _AuthorContext(run, state, checkpoint, action, binding, document, version)


async def _locked_author_action(
    service: object,
    *,
    run: WorkflowRun,
    project_id: UUID,
    chapter_id: UUID,
    action_request_id: UUID,
) -> tuple[ActionRequest | None, int]:
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
    return action, int(pending_count or 0)


async def _locked_author_document(
    service: object,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content_hash: str,
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


def _build_author_binding(
    action: ActionRequest,
    run: WorkflowRun,
    chapter: Chapter,
    document: Document,
    version: DocumentVersion,
) -> ChapterActionBinding:
    return ChapterActionBinding(
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


async def _adopt_stale_author_context(
    service: object,
    *,
    run: WorkflowRun,
    state: ChapterProductionState,
    checkpoint: object,
    chapter: Chapter,
    action: ActionRequest,
    actor_user_id: UUID,
    document_id: UUID,
    version_id: UUID,
    metadata: dict[str, object],
) -> _AuthorContext:
    stale_document, stale_version = await _locked_stale_author_document(
        service, run=run, document_id=document_id, version_id=version_id
    )
    if (
        stale_document is None
        or stale_version is None
        or chapter.current_draft_document_id != stale_document.id
        or stale_version.source != DocumentSource.USER.value
        or stale_version.actor_user_id is None
        or str(stale_version.actor_user_id) != str(actor_user_id)
        or stale_version.agent_role is not None
        or stale_version.workflow_run_id is not None
    ):
        raise _invalid()
    await _commit_stale_author_adoption(
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
        stale_document=stale_document,
        stale_version=stale_version,
    )
    raise AssertionError("unreachable")


async def _locked_stale_author_document(
    service: object,
    *,
    run: WorkflowRun,
    document_id: UUID,
    version_id: UUID,
) -> tuple[Document | None, DocumentVersion | None]:
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
    return stale_document, stale_version


async def _commit_stale_author_adoption(
    service: object,
    *,
    run: WorkflowRun,
    state: ChapterProductionState,
    checkpoint: object,
    chapter: Chapter,
    action: ActionRequest,
    actor_user_id: UUID,
    document_id: UUID,
    version_id: UUID,
    metadata: dict[str, object],
    stale_document: Document,
    stale_version: DocumentVersion,
) -> None:
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


async def review_revision_context(
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
    state, checkpoint = await locked_state(service, run)
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
    reports = await _locked_review_revision_reports(
        service, report_slots, project_id, chapter_id, run, document_id, version_id
    )
    document, version = await _locked_review_revision_document(
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
    await _validate_review_revision_reports(
        service, reports, report_slots, run, document, version
    )
    return _ReviewRevisionContext(
        run, state, checkpoint, document, version, segment_map, tuple(reports)
    )


async def _validate_review_revision_reports(
    service: object,
    reports: list[ReviewReport],
    report_slots: tuple[tuple[UUID, str, str], ...],
    run: WorkflowRun,
    document: Document,
    version: DocumentVersion,
) -> None:
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


async def _locked_review_revision_reports(
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


__all__ = [
    "_adopt_stale_author_context",
    "_build_author_binding",
    "_commit_stale_author_adoption",
    "_locked_author_document",
    "_locked_review_revision_document",
    "_locked_review_revision_reports",
    "_locked_stale_author_document",
    "_validate_review_revision_reports",
    "author_context",
    "exact_review_report_count",
    "locked_current_revision",
    "locked_review_document",
    "review_revision_context",
]
