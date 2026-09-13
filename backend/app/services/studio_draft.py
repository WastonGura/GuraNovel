"""Autosave prose without resolving the author's review decision."""

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Document, DocumentSource, DocumentType, Project, WorkflowRun, WorkflowType
from app.services.document_service import DocumentService
from app.services.studio_scope import ensure_studio_writable, studio_chapter


async def save_studio_draft(session: AsyncSession, project_id: UUID, chapter_id: UUID,
                            content: str, expected_current_version_id: UUID):
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                          {"key": f"chapter-production-v2:{chapter_id}"})
    chapter = await studio_chapter(session, project_id, chapter_id)
    await ensure_studio_writable(session, chapter)
    runs = await session.scalars(select(WorkflowRun).where(
        WorkflowRun.chapter_id == chapter_id, WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
        func.lower(WorkflowRun.status).not_in(("completed", "cancelled")),
    ))
    if any(run.status != "AUTHOR_REVISION" or not run.awaiting_user for run in runs):
        raise ConflictError("Draft editing is not available at this workflow stage.")
    document = await session.scalar(select(Document).where(
        Document.id == chapter.current_draft_document_id, Document.project_id == project_id,
        Document.chapter_id == chapter_id, Document.type == DocumentType.CHAPTER_DRAFT.value,
    ).with_for_update())
    if document is None:
        raise NotFoundError("Draft document not found.")
    project = await session.get(Project, project_id)
    return await DocumentService(session).write_document(
        document_id=UUID(str(document.id)), content=content,
        expected_current_version_id=expected_current_version_id,
        source=DocumentSource.USER, actor_user_id=project.owner_id,
    )
