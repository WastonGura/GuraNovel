"""Thin HTTP routes for versioned document operations."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
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
from app.core.errors import NotFoundError
from app.models import Document, DocumentVersion
from app.services import DocumentService

router = APIRouter(prefix="/documents")


async def _document_metadata(session: AsyncSession, document_id: UUID) -> Document:
    document = await session.scalar(
        select(Document)
        .options(selectinload(Document.current_version))
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
    payload: CreateDocumentRequest, session: AsyncSession = Depends(get_db_session)
) -> Document:
    document = await DocumentService(session).create_document(
        document_type=payload.type,
        **payload.model_dump(exclude={"type"}),
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
async def write_document(
    document_id: UUID,
    payload: WriteDocumentRequest,
    session: AsyncSession = Depends(get_db_session),
) -> DocumentVersion:
    version = await DocumentService(session).write_document(
        document_id=document_id, **payload.model_dump()
    )
    return await _version_metadata(session, version.id)


@router.post("/{document_id}/versions/{version_id}/restore", response_model=DocumentVersionResponse)
async def restore_document(
    document_id: UUID,
    version_id: UUID,
    payload: RestoreDocumentRequest,
    session: AsyncSession = Depends(get_db_session),
) -> DocumentVersion:
    version = await DocumentService(session).restore_document(
        document_id=document_id, version_id=version_id, **payload.model_dump()
    )
    return await _version_metadata(session, version.id)
