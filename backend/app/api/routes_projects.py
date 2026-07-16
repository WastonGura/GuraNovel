"""Thin HTTP routes for project operations."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_project_workspace
from app.api.schemas_projects import CreateProjectRequest, ProjectResponse
from app.models import Project
from app.services import ProjectService
from app.workspace import ProjectWorkspace

router = APIRouter(prefix="/projects")


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    session: AsyncSession = Depends(get_db_session),
    workspace: ProjectWorkspace = Depends(get_project_workspace),
) -> Project:
    data = payload.model_dump()
    data["metadata"] = data.pop("metadata_")
    return await ProjectService(session, workspace).create_project(**data)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(session: AsyncSession = Depends(get_db_session)) -> list[Project]:
    return await ProjectService(session).list_projects()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: UUID, session: AsyncSession = Depends(get_db_session)) -> Project:
    return await ProjectService(session).get_project(project_id)
