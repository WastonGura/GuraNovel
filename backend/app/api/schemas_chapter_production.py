"""Pydantic schemas for chapter-production workflow endpoints."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StartChapterProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveChapterProductionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]


class ChapterProductionActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    options: list[str]
    default_option: str | None
    user_decision: str | None


class ChapterProductionEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    node_name: str | None
    message: str | None
    payload: dict


class ChapterProductionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    current_node: str | None
    next_node: str | None
    awaiting_user: bool
    actions: list[ChapterProductionActionResponse]
    events: list[ChapterProductionEventResponse]
    outline_document_id: UUID | None
    draft_document_id: UUID | None
