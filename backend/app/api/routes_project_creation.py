"""HTTP boundary for the deliberately content-free project-creation foundation."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_project_creation_composition
from app.api.schemas_project_creation import (
    ProjectCreationRunResponse,
    ResolveProjectCreationActionRequest,
    StartProjectCreationRequest,
)
from app.services.project_creation_service import ProjectCreationRunRead, ProjectCreationService
from app.agents import ConceptAgentRequest
from app.agents.composition import ProjectCreationComposition

router = APIRouter(prefix="/projects/{project_id}/creation")


@router.post(
    "/start", response_model=ProjectCreationRunResponse, status_code=status.HTTP_201_CREATED
)
async def start_project_creation(
    project_id: UUID,
    payload: StartProjectCreationRequest,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectCreationComposition = Depends(get_project_creation_composition),
) -> ProjectCreationRunRead:
    service = ProjectCreationService(session, composition)
    started = await service.start(
        project_id,
        ConceptAgentRequest(
            project_id=project_id,
            user_seed=payload.user_seed,
            target_platform=payload.target_platform,
            preferred_genres=payload.preferred_genres or [],
            disliked_elements=payload.disliked_elements or [],
            style_preference=payload.style_preference,
        ),
    )
    return await service.get_project_creation_run(project_id, started.workflow_run_id)


@router.get("/{workflow_run_id}", response_model=ProjectCreationRunResponse)
async def get_project_creation(
    project_id: UUID,
    workflow_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectCreationComposition = Depends(get_project_creation_composition),
) -> ProjectCreationRunRead:
    return await ProjectCreationService(session, composition).get_project_creation_run(
        project_id, workflow_run_id
    )


@router.post(
    "/{workflow_run_id}/actions/{action_id}/resolve", response_model=ProjectCreationRunResponse
)
async def resolve_project_creation_action(
    project_id: UUID,
    workflow_run_id: UUID,
    action_id: UUID,
    payload: ResolveProjectCreationActionRequest,
    session: AsyncSession = Depends(get_db_session),
    composition: ProjectCreationComposition = Depends(get_project_creation_composition),
) -> ProjectCreationRunRead:
    service = ProjectCreationService(session, composition)
    await service.resolve_action(
        project_id,
        workflow_run_id,
        action_id,
        decision=payload.decision,
        fused_concept=payload.fused_concept,
        option_id=payload.option_id,
        feedback=payload.feedback,
    )
    return await service.get_project_creation_run(project_id, workflow_run_id)
