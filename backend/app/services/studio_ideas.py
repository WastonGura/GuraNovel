"""Scoped chapter ideas with optimistic revisions and retry identities."""
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import Chapter
from app.services.studio_scope import studio_chapter


def idea_value(chapter: Chapter) -> dict:
    idea = (chapter.metadata_ or {}).get("studio_idea", {})
    if not isinstance(idea, dict) or not isinstance(idea.get("text", ""), str) or type(idea.get("revision", 0)) is not int:
        raise ConflictError("Stored chapter idea is invalid.")
    return dict(project_id=chapter.project_id, chapter_id=chapter.id, text=idea.get("text", ""),
                revision=idea.get("revision", 0),
                read_only=bool(chapter.current_draft_document_id or chapter.final_document_id))


async def read_idea(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> dict:
    return idea_value(await studio_chapter(session, project_id, chapter_id))


async def write_idea(session: AsyncSession, project_id: UUID, chapter_id: UUID, *,
                     text: str, expected_revision: int, request_id: UUID) -> dict:
    chapter = await studio_chapter(session, project_id, chapter_id)
    current = idea_value(chapter)
    stored = (chapter.metadata_ or {}).get("studio_idea", {})
    digest = sha256(json.dumps([text, expected_revision], ensure_ascii=False).encode()).hexdigest()
    if stored.get("request_id") == str(request_id):
        if stored.get("request_hash") != digest:
            raise ConflictError("The request identity was reused with different content.")
        return current
    if current["read_only"] or current["revision"] != expected_revision:
        raise ConflictError("The chapter idea changed or is read-only. Keep your local draft.")
    chapter.metadata_ = {**(chapter.metadata_ or {}), "studio_idea": {
        "text": text, "revision": expected_revision + 1, "request_id": str(request_id), "request_hash": digest,
    }}
    await session.commit()
    return idea_value(chapter)
