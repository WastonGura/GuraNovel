"""Owner-bound global Gura sessions with explicit per-message page context."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.routes_setting_collections import get_actor_user_id
from app.api.schemas_studio_assistant import (
    AssistantConversationResponse,
    AssistantConversationSummary,
    GlobalAssistantSendRequest,
    HomeAssistantConversationCreateRequest,
    HomeAssistantSendRequest,
)
from app.services.global_assistant_service import GlobalAssistantService

router = APIRouter(prefix="/assistant")


@router.post("/conversations", response_model=AssistantConversationResponse)
async def get_or_create_home_conversation(
    payload: HomeAssistantConversationCreateRequest | None = None,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
):
    return await GlobalAssistantService(session, actor_id=actor_id).get_or_create_conversation(
        None, new_conversation=payload.new_conversation if payload else False,
        client_conversation_id=payload.client_conversation_id if payload else None,
    )


@router.get("/conversations", response_model=list[AssistantConversationSummary])
async def list_home_conversations(
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
):
    return await GlobalAssistantService(session, actor_id=actor_id).list_summaries()


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationResponse)
async def get_home_conversation(
    conversation_id: UUID,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
):
    return await GlobalAssistantService(session, actor_id=actor_id).get_conversation(None, conversation_id)


@router.post("/conversations/{conversation_id}/messages", response_model=AssistantConversationResponse)
async def send_home_message(
    conversation_id: UUID,
    payload: GlobalAssistantSendRequest | HomeAssistantSendRequest,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
):
    return await GlobalAssistantService(session, actor_id=actor_id).send_message(None, conversation_id, payload)


@router.get("/legacy-conversations", response_model=list[AssistantConversationSummary])
async def list_legacy_conversations(
    project_id: UUID | None = None,
    collection_id: UUID | None = None,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
):
    return await GlobalAssistantService(session, actor_id=actor_id).list_legacy_summaries(project_id, collection_id)


@router.get("/legacy-conversations/{conversation_id}", response_model=AssistantConversationResponse)
async def get_legacy_conversation(
    conversation_id: UUID,
    project_id: UUID | None = None,
    collection_id: UUID | None = None,
    actor_id: UUID | None = Depends(get_actor_user_id),
    session: AsyncSession = Depends(get_db_session),
):
    return await GlobalAssistantService(session, actor_id=actor_id).get_legacy_conversation(conversation_id, project_id, collection_id)
