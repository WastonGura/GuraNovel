"""HTTP boundary for the deliberately content-free project-creation foundation."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.schemas_project_creation import (
    ProjectCreationRunResponse,
    ResolveProjectCreationActionRequest,
    StartProjectCreationRequest,
)
from app.services.project_creation_service import ProjectCreationRunRead, ProjectCreationService

router = APIRouter(prefix="/projects/{project_id}/creation")


@router.post("/start", response_model=ProjectCreationRunResponse, status_code=status.HTTP_201_CREATED)
async def start_project_creation(
    project_id: UUID,
    payload: StartProjectCreationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ProjectCreationRunRead:
    # #65 owns safe generation and document persistence; this validated input is transient in #64.
    del payload
    service = ProjectCreationService(session)
    started = await service.start(project_id)
    return await service.get_project_creation_run(project_id, started.workflow_run_id)


@router.get("/{workflow_run_id}", response_model=ProjectCreationRunResponse)
async def get_project_creation(
    project_id: UUID,
    workflow_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ProjectCreationRunRead:
    return await ProjectCreationService(session).get_project_creation_run(project_id, workflow_run_id)


@router.post(
    "/{workflow_run_id}/actions/{action_id}/resolve", response_model=ProjectCreationRunResponse
)
async def resolve_project_creation_action(
    project_id: UUID,
    workflow_run_id: UUID,
    action_id: UUID,
    payload: ResolveProjectCreationActionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ProjectCreationRunRead:
    service = ProjectCreationService(session)
    await service.resolve_concept_review(project_id, workflow_run_id, action_id, payload.decision)
    return await service.get_project_creation_run(project_id, workflow_run_id)
