"""Confirm one selected outline without generating or approving its draft."""

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Document, DocumentType, WorkflowRun
from app.services.document_service import DocumentService
from app.services.studio_scope import studio_chapter


async def approve_studio_outline(
    session: AsyncSession, project_id: UUID, chapter_id: UUID,
    document_id: UUID, expected_current_version_id: UUID,
) -> dict:
    # Match V2's chapter lock order so confirmation cannot race a new run.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                          {"key": f"chapter-production-v2:{chapter_id}"})
    chapter = await studio_chapter(session, project_id, chapter_id)
    if chapter.current_outline_document_id != document_id:
        raise ConflictError("The selected outline has changed.")
    document = await session.scalar(select(Document).where(
        Document.id == document_id, Document.project_id == project_id,
        Document.chapter_id == chapter_id, Document.type == DocumentType.CHAPTER_SELECTED_OUTLINE.value,
    ).with_for_update().execution_options(populate_existing=True))
    if document is None:
        raise NotFoundError("Selected outline not found.")
    if document.current_version_id != expected_current_version_id:
        raise ConflictError("The outline version has changed. Review it before confirming.")
    result = dict(chapter_id=chapter_id, document_id=document_id, version_id=expected_current_version_id)
    # An uncertain response can be retried even after drafting has started.
    if chapter.approved_outline_version_id == expected_current_version_id:
        return result
    active = await session.scalar(select(WorkflowRun.id).where(
        WorkflowRun.chapter_id == chapter_id,
        func.lower(WorkflowRun.status).not_in(("completed", "cancelled", "failed", "rejected")),
    ).limit(1))
    if (active or chapter.current_draft_document_id or chapter.final_document_id
            or chapter.status not in {"OUTLINE_DISCUSSION", "OUTLINE_APPROVED"}):
        raise ConflictError("Outline approval is not available in the current chapter state.")
    content = await DocumentService(session).read_version_content(document_id, expected_current_version_id)
    if not content.strip():
        raise ConflictError("An empty outline cannot be approved.")
    chapter.approved_outline_version_id = expected_current_version_id
    chapter.status = "OUTLINE_APPROVED"
    await session.commit()
    return result
