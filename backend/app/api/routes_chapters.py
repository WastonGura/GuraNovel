"""Thin HTTP routes for project-scoped chapter operations."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.schemas_chapters import ChapterResponse, CreateChapterRequest
from app.models import Chapter
from app.services import ChapterService

router = APIRouter(prefix="/projects/{project_id}/chapters")


@router.post("", response_model=ChapterResponse, status_code=status.HTTP_201_CREATED)
async def create_chapter(
    project_id: UUID,
    payload: CreateChapterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Chapter:
    data = payload.model_dump()
    data["metadata"] = data.pop("metadata_")
    return await ChapterService(session).create_chapter(project_id=project_id, **data)


@router.get("", response_model=list[ChapterResponse])
async def list_chapters(
    project_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> list[Chapter]:
    return await ChapterService(session).list_chapters(project_id=project_id)


@router.get("/{chapter_id}", response_model=ChapterResponse)
async def get_chapter(
    project_id: UUID, chapter_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> Chapter:
    return await ChapterService(session).get_chapter(project_id=project_id, chapter_id=chapter_id)
