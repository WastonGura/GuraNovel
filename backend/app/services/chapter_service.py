"""Transactional project-scoped chapter persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Chapter, Project


class ChapterService:
    """Application boundary for chapter creation and project-scoped retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_chapter(
        self, *, project_id: UUID, title: str | None = None, metadata: dict | None = None
    ) -> Chapter:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")

        await self._lock_project(project_id)
        chapter = Chapter(
            project_id=project_id,
            chapter_number=await self._next_chapter_number(project_id),
            title=title,
            metadata_=metadata or {},
        )
        self.session.add(chapter)
        try:
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return chapter

    async def get_chapter(self, *, project_id: UUID, chapter_id: UUID) -> Chapter:
        chapter = await self.session.scalar(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        )
        if chapter is None:
            raise NotFoundError("Chapter not found.")
        return chapter

    async def list_chapters(self, *, project_id: UUID) -> list[Chapter]:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        return list(
            await self.session.scalars(
                select(Chapter)
                .where(Chapter.project_id == project_id)
                .order_by(Chapter.chapter_number, Chapter.id)
            )
        )

    async def _next_chapter_number(self, project_id: UUID) -> int:
        latest = await self.session.scalar(
            select(func.max(Chapter.chapter_number)).where(Chapter.project_id == project_id)
        )
        return (latest or 0) + 1

    async def _lock_project(self, project_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"chapter:{project_id}"},
        )
