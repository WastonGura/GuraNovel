"""Thin project-scoped HTTP routes for project-maintenance workflows."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_project_maintenance_composition
from app.api.schemas_project_maintenance import (
    ProjectMaintenanceRunResponse,
    ResolveProjectMaintenanceActionRequest,
    StartProjectMaintenanceRequest,
)
from app.services.project_maintenance_service import (
    ProjectMaintenanceComposition,
    ProjectMaintenanceRunRead,
    ProjectMaintenanceService,
)
from app.workflows.project_maintenance import MaintenanceDecision


router = APIRouter(prefix="/projects/{project_id}/maintenance")
_ERROR_RESPONSES = {
    404: {"description": "The scoped project, run, or action was not found."},
    409: {"description": "The maintenance workflow state conflicts with the request."},
    422: {"description": "The request failed strict validation."},
}


@router.post(
    "/start",
    response_model=ProjectMaintenanceRunResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def start_project_maintenance(
    project_id: UUID,
    payload: StartProjectMaintenanceRequest,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectMaintenanceComposition = Depends(
        get_project_maintenance_composition
    ),
) -> ProjectMaintenanceRunRead:
    service = ProjectMaintenanceService(session, composition)
    started = await service.start(
        project_id,
        title=payload.title,
        change_request=payload.change_request,
        scope_hints=tuple(payload.scope_hints),
    )
    return await service.get_run(project_id, started.workflow_run_id)


@router.get(
    "", response_model=list[ProjectMaintenanceRunResponse], responses=_ERROR_RESPONSES
)
async def list_project_maintenance(
    project_id: UUID,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectMaintenanceComposition = Depends(
        get_project_maintenance_composition
    ),
) -> tuple[ProjectMaintenanceRunRead, ...]:
    return await ProjectMaintenanceService(session, composition).list_runs(
        project_id, offset=offset, limit=limit
    )


@router.get(
    "/{workflow_run_id}",
    response_model=ProjectMaintenanceRunResponse,
    responses=_ERROR_RESPONSES,
)
async def get_project_maintenance(
    project_id: UUID,
    workflow_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectMaintenanceComposition = Depends(
        get_project_maintenance_composition
    ),
) -> ProjectMaintenanceRunRead:
    return await ProjectMaintenanceService(session, composition).get_run(
        project_id, workflow_run_id
    )


@router.post(
    "/{workflow_run_id}/actions/{action_id}/resolve",
    response_model=ProjectMaintenanceRunResponse,
    responses=_ERROR_RESPONSES,
)
async def resolve_project_maintenance_action(
    project_id: UUID,
    workflow_run_id: UUID,
    action_id: UUID,
    payload: ResolveProjectMaintenanceActionRequest,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectMaintenanceComposition = Depends(
        get_project_maintenance_composition
    ),
) -> ProjectMaintenanceRunRead:
    service = ProjectMaintenanceService(session, composition)
    await service.resolve_action(
        project_id,
        workflow_run_id,
        action_id,
        decision=MaintenanceDecision(payload.decision),
    )
    return await service.get_run(project_id, workflow_run_id)
