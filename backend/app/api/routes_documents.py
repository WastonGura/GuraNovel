"""Thin HTTP routes for versioned document operations."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db_session
from app.api.schemas_documents import (
    CreateDocumentRequest,
    DocumentContentResponse,
    DocumentResponse,
    DocumentVersionResponse,
    RestoreDocumentRequest,
    WriteDocumentRequest,
)
from app.core.errors import ConflictError, NotFoundError
from app.models import Document, DocumentVersion
from app.services import DocumentService

router = APIRouter(prefix="/documents")


def get_actor_user_id(
    x_actor_user_id: UUID | None = Header(None, alias="x-actor-user-id"),
    actor_user_id: UUID | None = Query(None),
) -> UUID | None:
    return x_actor_user_id or actor_user_id


class RestoreBodyPayload(RestoreDocumentRequest):
    version_id: UUID | None = None


async def _document_metadata(session: AsyncSession, document_id: UUID) -> Document:
    document = await session.scalar(
        select(Document)
        .options(
            selectinload(Document.current_version),
            selectinload(Document.project),
            selectinload(Document.setting_collection),
        )
        .where(Document.id == document_id)
    )
    if document is None:
        raise NotFoundError("Document not found.")
    return document


async def _version_metadata(session: AsyncSession, version_id: UUID) -> DocumentVersion:
    version = await session.get(DocumentVersion, version_id)
    if version is None:
        raise NotFoundError("Document version not found.")
    return version


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: CreateDocumentRequest,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> Document:
    data = payload.model_dump(exclude={"type"})
    if actor_id is not None and data.get("actor_user_id") is None:
        data["actor_user_id"] = actor_id
    document = await DocumentService(session).create_document(
        document_type=payload.type,
        **data,
    )
    return await _document_metadata(session, document.id)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, session: AsyncSession = Depends(get_db_session)) -> Document:
    return await _document_metadata(session, document_id)


@router.get("/{document_id}/content", response_model=DocumentContentResponse)
async def read_current_content(
    document_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> DocumentContentResponse:
    current_content = await DocumentService(session).read_current_content(document_id)
    return DocumentContentResponse(
        document_id=document_id,
        version_id=current_content.version_id,
        content=current_content.content,
    )


@router.get("/{document_id}/versions", response_model=list[DocumentVersionResponse])
async def list_document_versions(
    document_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> list[DocumentVersion]:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFoundError("Document not found.")
    return list(
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number)
        )
    )


@router.get(
    "/{document_id}/versions/{version_id}/content", response_model=DocumentContentResponse
)
async def read_version_content(
    document_id: UUID, version_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> DocumentContentResponse:
    content = await DocumentService(session).read_version_content(document_id, version_id)
    return DocumentContentResponse(document_id=document_id, version_id=version_id, content=content)


@router.put("/{document_id}/content", response_model=DocumentVersionResponse)
@router.post("/{document_id}/content", response_model=DocumentVersionResponse)
async def write_document(
    document_id: UUID,
    payload: WriteDocumentRequest,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> DocumentVersion:
    data = payload.model_dump()
    if actor_id is not None and data.get("actor_user_id") is None:
        data["actor_user_id"] = actor_id
    version = await DocumentService(session).write_document(
        document_id=document_id, **data
    )
    return await _version_metadata(session, version.id)


@router.post("/{document_id}/versions/{version_id}/restore", response_model=DocumentVersionResponse)
@router.post("/{document_id}/restore", response_model=DocumentVersionResponse)
async def restore_document(
    document_id: UUID,
    payload: RestoreBodyPayload,
    version_id: UUID | None = None,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> DocumentVersion:
    target_version_id = version_id or payload.version_id
    if target_version_id is None:
        raise ConflictError("version_id must be provided in URL or request body")
    data = payload.model_dump(exclude={"version_id"})
    if actor_id is not None and data.get("actor_user_id") is None:
        data["actor_user_id"] = actor_id
    version = await DocumentService(session).restore_document(
        document_id=document_id, version_id=target_version_id, **data
    )
    return await _version_metadata(session, version.id)
