"""Thin HTTP routes for Chapter Production V2 workflows."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_chapter_production_v2_service,
    get_db_session,
    get_default_actor_user_id,
)
from app.api.schemas_chapter_production import (
    ChapterProductionRunSummaryResponse,
    ChapterProductionStateResponse,
    ChapterProductionV2FinalizedResponse,
    ChapterProductionV2StartedResponse,
    ChapterProductionV2UpdatedResponse,
    FinalizeChapterProductionRequest,
    ReconcileChapterProductionRequest,
    ResolveChapterProductionV2ActionRequest,
    ResumeChapterProductionRequest,
    StartChapterProductionRequest,
    TriggerChapterReviewRequest,
)
from app.core.errors import NotFoundError
from app.models import ActionRequest, Chapter, Project, WorkflowRun, WorkflowType
from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError
from app.services.chapter_production_v2_service import ChapterProductionV2Service

router = APIRouter(prefix="/projects/{project_id}/chapters/{chapter_id}/production-v2")
_ERROR_RESPONSES = {
    404: {"description": "The scoped project, chapter, run, or action was not found."},
    409: {"description": "The production run state requires reconciliation or conflicts."},
    422: {"description": "The request failed strict validation."},
    500: {"description": "Commit outcome is indeterminate; reconciliation is required."},
    503: {"description": "The provider is unavailable or timed out."},
}


async def _resolve_project_and_actor(
    session: AsyncSession,
    project_id: UUID,
    chapter_id: UUID,
    default_actor_id: UUID,
) -> tuple[Project, Chapter, UUID]:
    chapter = await session.scalar(
        select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
        )
    )
    if chapter is None:
        raise NotFoundError("Chapter not found.")
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")
    actor_id = project.owner_id if project.owner_id is not None else default_actor_id
    return project, chapter, actor_id


async def _require_run(
    session: AsyncSession,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
) -> WorkflowRun:
    run = await session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.id == workflow_run_id,
            WorkflowRun.project_id == project_id,
            WorkflowRun.chapter_id == chapter_id,
            WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
        )
    )
    if run is None:
        raise NotFoundError("Workflow run not found.")
    return run


async def _require_action(
    session: AsyncSession,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_id: UUID,
) -> ActionRequest:
    action = await session.scalar(
        select(ActionRequest).where(
            ActionRequest.id == action_id,
            ActionRequest.project_id == project_id,
            ActionRequest.chapter_id == chapter_id,
            ActionRequest.workflow_run_id == workflow_run_id,
        )
    )
    if action is None:
        raise NotFoundError("Action request not found.")
    return action


@router.post(
    "",
    response_model=ChapterProductionV2StartedResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
@router.post(
    "/start",
    response_model=ChapterProductionV2StartedResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def start_chapter_production_v2(
    project_id: UUID,
    chapter_id: UUID,
    payload: StartChapterProductionRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionV2StartedResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    started = await service.start_from_approved_outline(
        project_id, chapter_id, actor_user_id=actor_id
    )
    return ChapterProductionV2StartedResponse(
        workflow_run_id=started.workflow_run_id,
        action_request_id=started.action_request_id,
        outline_document_id=started.outline_document_id,
        outline_version_id=started.outline_version_id,
        draft_document_id=started.draft_document_id,
        draft_version_id=started.draft_version_id,
    )


@router.get(
    "",
    response_model=list[ChapterProductionRunSummaryResponse],
    responses=_ERROR_RESPONSES,
)
async def list_chapter_production_runs(
    project_id: UUID,
    chapter_id: UUID,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    session: AsyncSession = Depends(get_db_session),
) -> list[ChapterProductionRunSummaryResponse]:
    chapter = await session.scalar(
        select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
        )
    )
    if chapter is None:
        raise NotFoundError("Chapter not found.")
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found.")

    runs_result = await session.scalars(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.chapter_id == chapter_id,
            WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
        )
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        .offset(offset)
        .limit(limit)
    )
    runs = list(runs_result.all())
    return [
        ChapterProductionRunSummaryResponse(
            workflow_run_id=run.id,
            project_id=run.project_id,
            chapter_id=run.chapter_id,
            status=run.status,
            current_node=run.current_node,
            started_at=run.started_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]


@router.get(
    "/{workflow_run_id}",
    response_model=ChapterProductionStateResponse,
    responses=_ERROR_RESPONSES,
)
async def get_chapter_production_run(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionStateResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    await _require_run(session, project_id, chapter_id, workflow_run_id)
    state = await service.load_state(
        project_id, chapter_id, workflow_run_id, actor_user_id=actor_id
    )
    return ChapterProductionStateResponse(
        chapter_workflow_run_id=UUID(state.chapter_workflow_run_id),
        chapter_id=UUID(state.chapter_id),
        status=state.status.value,
        current_node=state.current_node,
        awaiting_user=state.awaiting_user,
        review_policy_version=state.review_policy_version,
        chief_editor_required=state.chief_editor_required,
        document_id=UUID(state.document_id) if state.document_id else None,
        document_version_id=UUID(state.document_version_id) if state.document_version_id else None,
        content_hash=state.content_hash,
        editor_report_id=UUID(state.editor_report_id) if state.editor_report_id else None,
        chief_editor_report_id=UUID(state.chief_editor_report_id)
        if state.chief_editor_report_id
        else None,
        lore_report_id=UUID(state.lore_report_id) if state.lore_report_id else None,
        action_request_id=UUID(state.action_request_id) if state.action_request_id else None,
        action_kind=state.action_kind.value if state.action_kind else None,
        failed_from_status=state.failed_from_status.value if state.failed_from_status else None,
        failure_code=state.failure_code.value if state.failure_code else None,
    )


@router.post(
    "/{workflow_run_id}/resume",
    response_model=ChapterProductionV2StartedResponse,
    responses=_ERROR_RESPONSES,
)
async def resume_chapter_production(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    payload: ResumeChapterProductionRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionV2StartedResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    await _require_run(session, project_id, chapter_id, workflow_run_id)
    started = await service.resume_drafting(
        project_id, chapter_id, workflow_run_id, actor_user_id=actor_id
    )
    return ChapterProductionV2StartedResponse(
        workflow_run_id=started.workflow_run_id,
        action_request_id=started.action_request_id,
        outline_document_id=started.outline_document_id,
        outline_version_id=started.outline_version_id,
        draft_document_id=started.draft_document_id,
        draft_version_id=started.draft_version_id,
    )


@router.post(
    "/{workflow_run_id}/actions/{action_id}/resolve",
    response_model=ChapterProductionV2UpdatedResponse,
    responses=_ERROR_RESPONSES,
)
async def resolve_chapter_production_action(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_id: UUID,
    payload: ResolveChapterProductionV2ActionRequest,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionV2UpdatedResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    await _require_run(session, project_id, chapter_id, workflow_run_id)
    await _require_action(session, project_id, chapter_id, workflow_run_id, action_id)

    decision = payload.decision
    if decision == "accept":
        updated = await service.resolve_author_action(
            project_id,
            chapter_id,
            workflow_run_id,
            action_id,
            actor_user_id=actor_id,
            decision="accept",
        )
    elif decision == "request_feedback_revision":
        if payload.feedback is None or payload.target_segment_ids is None:
            raise ChapterProductionV2ValidationError()
        updated = await service.request_user_feedback_revision(
            project_id,
            chapter_id,
            workflow_run_id,
            action_id,
            actor_user_id=actor_id,
            feedback=payload.feedback,
            target_segment_ids=payload.target_segment_ids,
        )
    elif decision == "submit_manual_edit":
        if payload.content is None:
            raise ChapterProductionV2ValidationError()
        updated = await service.submit_manual_edit(
            project_id,
            chapter_id,
            workflow_run_id,
            action_id,
            actor_user_id=actor_id,
            content=payload.content,
        )
    elif decision in {"proceed_with_warnings", "accept_warning"}:
        updated = await service.resolve_review_action(
            project_id,
            chapter_id,
            workflow_run_id,
            action_id,
            actor_user_id=actor_id,
            decision="accept_warning",
        )
    elif decision in {"request_review_revision", "request_revision"}:
        if payload.report_ids is None or payload.target_segment_ids is None:
            raise ChapterProductionV2ValidationError()
        await service.resolve_review_action(
            project_id,
            chapter_id,
            workflow_run_id,
            action_id,
            actor_user_id=actor_id,
            decision="request_revision",
        )
        updated = await service.execute_review_revision(
            project_id,
            chapter_id,
            workflow_run_id,
            actor_user_id=actor_id,
            report_ids=payload.report_ids,
            target_segment_ids=payload.target_segment_ids,
        )
    else:
        raise ChapterProductionV2ValidationError()

    return ChapterProductionV2UpdatedResponse(
        workflow_run_id=updated.workflow_run_id,
        draft_document_id=updated.draft_document_id,
        draft_version_id=updated.draft_version_id,
        action_request_id=updated.action_request_id,
    )


@router.post(
    "/{workflow_run_id}/review",
    response_model=ChapterProductionV2UpdatedResponse,
    responses=_ERROR_RESPONSES,
)
async def trigger_chapter_review(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    payload: TriggerChapterReviewRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionV2UpdatedResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    await _require_run(session, project_id, chapter_id, workflow_run_id)
    updated = await service.execute_current_review(
        project_id, chapter_id, workflow_run_id, actor_user_id=actor_id
    )
    return ChapterProductionV2UpdatedResponse(
        workflow_run_id=updated.workflow_run_id,
        draft_document_id=updated.draft_document_id,
        draft_version_id=updated.draft_version_id,
        action_request_id=updated.action_request_id,
    )


@router.post(
    "/{workflow_run_id}/finalize",
    response_model=ChapterProductionV2FinalizedResponse,
    responses=_ERROR_RESPONSES,
)
async def finalize_chapter_production(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    payload: FinalizeChapterProductionRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionV2FinalizedResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    await _require_run(session, project_id, chapter_id, workflow_run_id)
    finalized = await service.finalize_without_reader_panel(
        project_id, chapter_id, workflow_run_id, actor_user_id=actor_id
    )
    return ChapterProductionV2FinalizedResponse(
        workflow_run_id=finalized.workflow_run_id,
        final_document_id=finalized.final_document_id,
        final_version_id=finalized.final_version_id,
    )


@router.post(
    "/{workflow_run_id}/reconcile",
    response_model=ChapterProductionStateResponse,
    responses=_ERROR_RESPONSES,
)
async def reconcile_chapter_production(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    payload: ReconcileChapterProductionRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    service: ChapterProductionV2Service = Depends(get_chapter_production_v2_service),
    default_actor_id: UUID = Depends(get_default_actor_user_id),
) -> ChapterProductionStateResponse:
    _, _, actor_id = await _resolve_project_and_actor(
        session, project_id, chapter_id, default_actor_id
    )
    await _require_run(session, project_id, chapter_id, workflow_run_id)
    state = await service.reconcile_indeterminate(
        project_id, chapter_id, workflow_run_id, actor_user_id=actor_id
    )
    return ChapterProductionStateResponse(
        chapter_workflow_run_id=UUID(state.chapter_workflow_run_id),
        chapter_id=UUID(state.chapter_id),
        status=state.status.value,
        current_node=state.current_node,
        awaiting_user=state.awaiting_user,
        review_policy_version=state.review_policy_version,
        chief_editor_required=state.chief_editor_required,
        document_id=UUID(state.document_id) if state.document_id else None,
        document_version_id=UUID(state.document_version_id) if state.document_version_id else None,
        content_hash=state.content_hash,
        editor_report_id=UUID(state.editor_report_id) if state.editor_report_id else None,
        chief_editor_report_id=UUID(state.chief_editor_report_id)
        if state.chief_editor_report_id
        else None,
        lore_report_id=UUID(state.lore_report_id) if state.lore_report_id else None,
        action_request_id=UUID(state.action_request_id) if state.action_request_id else None,
        action_kind=state.action_kind.value if state.action_kind else None,
        failed_from_status=state.failed_from_status.value if state.failed_from_status else None,
        failure_code=state.failure_code.value if state.failure_code else None,
    )
