"""Thin HTTP routes for setting collection operations."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db_session, get_project_workspace
from app.api.schemas_documents import DocumentResponse
from app.api.schemas_projects import ProjectResponse
from app.api.schemas_setting_collections import (
    CreateSettingCollectionRequest,
    CreateSettingDocumentRequest,
    SettingCollectionResponse,
    UpdateSettingCollectionRequest,
)
from app.agents.setting_proposal_contracts import SettingChangeProposal
from app.models import Document, Project, SettingCollection
from app.services import SettingCollectionService
from app.workspace import ProjectWorkspace

router = APIRouter(prefix="/setting-collections")


def get_actor_user_id(
    x_actor_user_id: UUID | None = Header(None, alias="x-actor-user-id"),
    actor_user_id: UUID | None = Query(None),
) -> UUID | None:
    return x_actor_user_id or actor_user_id


@router.post("", response_model=SettingCollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_setting_collection(
    payload: CreateSettingCollectionRequest,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
    workspace: ProjectWorkspace = Depends(get_project_workspace),
) -> SettingCollection:
    data = payload.model_dump()
    data["metadata"] = data.pop("metadata_")
    if actor_user_id is not None and data.get("owner_id") is None:
        data["owner_id"] = actor_user_id
    return await SettingCollectionService(session, workspace).create_setting_collection(**data)


@router.get("", response_model=list[SettingCollectionResponse])
async def list_setting_collections(
    status: str | None = None,
    owner_id: UUID | None = None,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> list[SettingCollection]:
    effective_owner_id = owner_id or actor_user_id
    return await SettingCollectionService(session).list_setting_collections(
        owner_id=effective_owner_id, status=status
    )


@router.get("/{collection_id}", response_model=SettingCollectionResponse)
async def get_setting_collection(
    collection_id: UUID,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> SettingCollection:
    return await SettingCollectionService(session).get_setting_collection(
        collection_id, actor_user_id=actor_user_id
    )


@router.patch("/{collection_id}", response_model=SettingCollectionResponse)
async def update_setting_collection(
    collection_id: UUID,
    payload: UpdateSettingCollectionRequest,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> SettingCollection:
    data = payload.model_dump(exclude_unset=True)
    if "metadata_" in data:
        data["metadata"] = data.pop("metadata_")
    return await SettingCollectionService(session).update_setting_collection(
        collection_id, actor_user_id=actor_user_id, **data
    )


@router.post("/{collection_id}/archive", response_model=SettingCollectionResponse)
async def archive_setting_collection(
    collection_id: UUID,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> SettingCollection:
    return await SettingCollectionService(session).archive_setting_collection(
        collection_id, actor_user_id=actor_user_id
    )


@router.get("/{collection_id}/projects", response_model=list[ProjectResponse])
async def list_collection_projects(
    collection_id: UUID,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> list[Project]:
    return await SettingCollectionService(session).list_projects_for_collection(
        collection_id, actor_user_id=actor_user_id
    )


@router.get("/{collection_id}/documents", response_model=list[DocumentResponse])
async def list_collection_documents(
    collection_id: UUID,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> list[Document]:
    return await SettingCollectionService(session).list_documents_for_collection(
        collection_id, actor_user_id=actor_user_id
    )


@router.post(
    "/{collection_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection_document(
    collection_id: UUID,
    payload: CreateSettingDocumentRequest,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> Document:
    data = payload.model_dump()
    data["metadata"] = data.pop("metadata_")
    if actor_user_id is not None and data.get("actor_user_id") is None:
        data["actor_user_id"] = actor_user_id
    document = await SettingCollectionService(session).create_document_for_collection(
        collection_id=collection_id,
        document_type=data.pop("type"),
        **data,
    )
    return await session.scalar(
        select(Document)
        .options(
            selectinload(Document.current_version),
            selectinload(Document.project),
            selectinload(Document.setting_collection),
        )
        .where(Document.id == document.id)
    )


@router.post(
    "/{collection_id}/proposals/apply",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
)
async def apply_setting_proposal(
    collection_id: UUID,
    proposal: SettingChangeProposal,
    actor_user_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
) -> Document:
    return await SettingCollectionService(session).apply_setting_change_proposal(
        collection_id=collection_id,
        proposal=proposal,
        actor_user_id=actor_user_id,
    )

