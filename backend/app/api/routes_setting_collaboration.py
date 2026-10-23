"""Document authoring collaboration; deliberately separate from Gura routes."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.routes_setting_collections import get_actor_user_id
from app.api.schemas_documents import DocumentResponse
from app.api.schemas_setting_collaboration import (
    SettingCollaborationAcceptanceResponse, SettingCollaborationAttemptResponse, SettingCollaborationResponse,
    SettingCollaborationCreateRequest, SettingCollaborationHistoryResponse,
    SettingCollaborationSendRequest, SettingCollectionCollaborationSendRequest, SettingProposalAcceptanceRequest,
)
from app.services.setting_collaboration_service import SettingCollaborationService

router = APIRouter(prefix="/setting-collections/{collection_id}/documents/{document_id}/collaboration")
collection_router = APIRouter(prefix="/setting-collections/{collection_id}/collaboration")


def service(collection_id: UUID, document_id: UUID, actor_id: UUID | None = Depends(get_actor_user_id), session: AsyncSession = Depends(get_db_session)):
    return SettingCollaborationService(session, collection_id, document_id, actor_id)


def collection_service(collection_id: UUID, actor_id: UUID | None = Depends(get_actor_user_id), session: AsyncSession = Depends(get_db_session)):
    return SettingCollaborationService(session, collection_id, actor_id=actor_id)


def conversation_service(collection_id: UUID, conversation_id: UUID, actor_id: UUID | None = Depends(get_actor_user_id), session: AsyncSession = Depends(get_db_session)):
    return SettingCollaborationService(session, collection_id, actor_id=actor_id, conversation_id=conversation_id)


@router.post("", response_model=SettingCollaborationResponse)
async def open_collaboration(collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.get_or_create()


@router.get("", response_model=SettingCollaborationResponse)
async def get_collaboration(collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.get()


@router.post("/messages", response_model=SettingCollaborationResponse)
async def send_message(payload: SettingCollaborationSendRequest, collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.send(payload)


@router.post("/proposals/{proposal_id}/accept", response_model=SettingCollaborationAcceptanceResponse)
async def accept_proposal(proposal_id: UUID, payload: SettingProposalAcceptanceRequest, collaboration: SettingCollaborationService = Depends(service)):
    conversation, document, content = await collaboration.accept(proposal_id, payload)
    return SettingCollaborationAcceptanceResponse(
        conversation=SettingCollaborationResponse.model_validate(conversation),
        document=DocumentResponse.model_validate(document), content=content,
    )


@router.post("/proposals/{proposal_id}/discard", response_model=SettingCollaborationResponse)
async def discard_proposal(proposal_id: UUID, collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.discard(proposal_id)


@collection_router.post("", response_model=SettingCollaborationResponse)
async def open_collection_collaboration(collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.get_or_create()


@collection_router.get("", response_model=SettingCollaborationResponse)
async def get_collection_collaboration(collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.get()


@collection_router.post("/messages", response_model=SettingCollaborationResponse)
async def send_collection_message(payload: SettingCollectionCollaborationSendRequest, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.send(payload)


@router.get("/attempts/{attempt_id}", response_model=SettingCollaborationAttemptResponse)
async def inspect_attempt(attempt_id: UUID, collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.inspect_attempt(attempt_id)


@collection_router.get("/attempts/{attempt_id}", response_model=SettingCollaborationAttemptResponse)
async def inspect_collection_attempt(attempt_id: UUID, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.inspect_attempt(attempt_id)


@router.post("/attempts/{attempt_id}/resume", response_model=SettingCollaborationResponse)
async def resume_attempt(attempt_id: UUID, collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.resume_attempt(attempt_id)


@collection_router.post("/attempts/{attempt_id}/resume", response_model=SettingCollaborationResponse)
async def resume_collection_attempt(attempt_id: UUID, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.resume_attempt(attempt_id)


@router.post("/attempts/finish", response_model=SettingCollaborationResponse)
async def finish_attempt(payload: SettingCollaborationSendRequest, collaboration: SettingCollaborationService = Depends(service)):
    return await collaboration.finish_attempt(payload)


@collection_router.post("/attempts/finish", response_model=SettingCollaborationResponse)
async def finish_collection_attempt(payload: SettingCollectionCollaborationSendRequest, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.finish_attempt(payload)


@collection_router.post("/proposals/{proposal_id}/accept", response_model=SettingCollaborationAcceptanceResponse)
async def accept_collection_proposal(proposal_id: UUID, payload: SettingProposalAcceptanceRequest, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await accept_proposal(proposal_id, payload, collaboration)


@collection_router.post("/proposals/{proposal_id}/discard", response_model=SettingCollaborationResponse)
async def discard_collection_proposal(proposal_id: UUID, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.discard(proposal_id)


@collection_router.get("/conversations", response_model=SettingCollaborationHistoryResponse)
async def list_conversations(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.list_conversations(limit=limit, offset=offset)


@collection_router.post("/conversations", response_model=SettingCollaborationResponse)
async def create_conversation(payload: SettingCollaborationCreateRequest, collaboration: SettingCollaborationService = Depends(collection_service)):
    return await collaboration.create_conversation(payload.client_request_id)


@collection_router.get("/conversations/{conversation_id}", response_model=SettingCollaborationResponse)
async def get_conversation(collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await collaboration.get()


@collection_router.post("/conversations/{conversation_id}/messages", response_model=SettingCollaborationResponse)
async def send_conversation_message(payload: SettingCollectionCollaborationSendRequest, collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await collaboration.send(payload)


@collection_router.get("/conversations/{conversation_id}/attempts/{attempt_id}", response_model=SettingCollaborationAttemptResponse)
async def inspect_conversation_attempt(attempt_id: UUID, collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await collaboration.inspect_attempt(attempt_id)


@collection_router.post("/conversations/{conversation_id}/attempts/{attempt_id}/resume", response_model=SettingCollaborationResponse)
async def resume_conversation_attempt(attempt_id: UUID, collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await collaboration.resume_attempt(attempt_id)


@collection_router.post("/conversations/{conversation_id}/attempts/finish", response_model=SettingCollaborationResponse)
async def finish_conversation_attempt(payload: SettingCollectionCollaborationSendRequest, collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await collaboration.finish_attempt(payload)


@collection_router.post("/conversations/{conversation_id}/proposals/{proposal_id}/accept", response_model=SettingCollaborationAcceptanceResponse)
async def accept_conversation_proposal(proposal_id: UUID, payload: SettingProposalAcceptanceRequest, collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await accept_proposal(proposal_id, payload, collaboration)


@collection_router.post("/conversations/{conversation_id}/proposals/{proposal_id}/discard", response_model=SettingCollaborationResponse)
async def discard_conversation_proposal(proposal_id: UUID, collaboration: SettingCollaborationService = Depends(conversation_service)):
    return await collaboration.discard(proposal_id)
