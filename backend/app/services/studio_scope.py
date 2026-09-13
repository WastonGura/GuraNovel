"""Shared project/chapter authority for Studio persistence."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Chapter, WorkflowRun


async def studio_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter:
    chapter = await session.scalar(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if chapter is None:
        raise NotFoundError("Chapter not found.")
    return chapter


async def ensure_studio_writable(session: AsyncSession, chapter: Chapter) -> None:
    active = await session.scalar(select(WorkflowRun.id).where(
        WorkflowRun.chapter_id == chapter.id, WorkflowRun.awaiting_user.is_(False),
        func.lower(WorkflowRun.status).not_in(("completed", "cancelled", "failed")),
    ).limit(1))
    if chapter.final_document_id or active:
        raise ConflictError("The chapter is currently read-only.")
