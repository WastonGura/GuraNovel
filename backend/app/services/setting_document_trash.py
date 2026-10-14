"""Recoverable setting deletion never removes content, versions or references."""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.models import Document, SettingCollection


async def set_setting_document_deleted(
    session: AsyncSession, document_id: UUID, *, deleted: bool, actor_id: UUID | None,
) -> Document:
    document = await session.scalar(select(Document).where(
        Document.id == document_id, Document.setting_collection_id.is_not(None),
    ).with_for_update().execution_options(populate_existing=True))
    if document is None:
        raise NotFoundError("Setting document not found.")
    collection = await session.scalar(select(SettingCollection).where(
        SettingCollection.id == document.setting_collection_id,
    ).with_for_update().execution_options(populate_existing=True))
    if collection is None:
        raise NotFoundError("Setting collection not found.")
    if actor_id is not None and collection.owner_id is not None and actor_id != collection.owner_id:
        raise ForbiddenError("You do not have permission to modify this setting collection.")
    if collection.status == "archived":
        raise ConflictError("Archived setting collection cannot be modified.")
    if bool(document.deleted_at) != deleted:
        document.deleted_at = datetime.now(UTC) if deleted else None
        collection.revision += 1
    await session.commit()
    return document
