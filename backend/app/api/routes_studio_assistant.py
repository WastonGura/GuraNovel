"""HTTP routes for Studio Assistant."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.schemas_studio_assistant import (
    AssistantConversationCreateRequest,
    AssistantConversationResponse,
    AssistantSendMessageRequest,
)
from app.services.studio_assistant_service import StudioAssistantService
from app.api.schemas_studio_assistant import SettingAssistantSendRequest
from app.api.routes_setting_collections import get_actor_user_id

router = APIRouter(prefix="/projects/{project_id}/assistant")
collection_router = APIRouter(prefix="/setting-collections/{collection_id}/assistant")


@collection_router.post("/conversations", response_model=AssistantConversationResponse)
async def get_or_create_collection_conversation(collection_id: UUID, actor_id: UUID | None = Depends(get_actor_user_id), session: AsyncSession = Depends(get_db_session)):
    return await StudioAssistantService(session, setting_collection_id=collection_id, actor_id=actor_id).get_or_create_conversation(None)


@collection_router.get("/conversations/{conversation_id}", response_model=AssistantConversationResponse)
async def get_collection_conversation(collection_id: UUID, conversation_id: UUID, actor_id: UUID | None = Depends(get_actor_user_id), session: AsyncSession = Depends(get_db_session)):
    return await StudioAssistantService(session, setting_collection_id=collection_id, actor_id=actor_id).get_conversation(None, conversation_id)


@collection_router.post("/conversations/{conversation_id}/messages", response_model=AssistantConversationResponse)
async def send_collection_message(collection_id: UUID, conversation_id: UUID, payload: SettingAssistantSendRequest, actor_id: UUID | None = Depends(get_actor_user_id), session: AsyncSession = Depends(get_db_session)):
    return await StudioAssistantService(session, setting_collection_id=collection_id, actor_id=actor_id).send_message(None, conversation_id, payload)


@router.post("/conversations", response_model=AssistantConversationResponse)
async def get_or_create_conversation(
    project_id: UUID,
    payload: AssistantConversationCreateRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> AssistantConversationResponse:
    chapter_id = payload.chapter_id if payload else None
    title = payload.title if payload else None
    service = StudioAssistantService(session)
    conv = await service.get_or_create_conversation(project_id, chapter_id=chapter_id, title=title)
    return AssistantConversationResponse.model_validate(conv)


@router.get("/conversations", response_model=list[AssistantConversationResponse])
async def list_conversations(
    project_id: UUID,
    chapter_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> list[AssistantConversationResponse]:
    service = StudioAssistantService(session)
    conversations = await service.list_conversations(project_id, chapter_id=chapter_id)
    return [AssistantConversationResponse.model_validate(c) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationResponse)
async def get_conversation(
    project_id: UUID,
    conversation_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AssistantConversationResponse:
    service = StudioAssistantService(session)
    conv = await service.get_conversation(project_id, conversation_id)
    return AssistantConversationResponse.model_validate(conv)


@router.post("/conversations/{conversation_id}/messages", response_model=AssistantConversationResponse)
async def send_message(
    project_id: UUID,
    conversation_id: UUID,
    payload: AssistantSendMessageRequest,
    session: AsyncSession = Depends(get_db_session),
) -> AssistantConversationResponse:
    service = StudioAssistantService(session)
    conv = await service.send_message(project_id, conversation_id, payload)
    return AssistantConversationResponse.model_validate(conv)
