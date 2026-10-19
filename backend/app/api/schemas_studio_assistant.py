"""Schemas for Studio Assistant conversations and messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AssistantConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: UUID | None = None
    title: str | None = Field(default=None, max_length=256)


class AssistantSendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=16384)
    chapter_id: UUID | None = None
    current_view: str | None = Field(default=None, max_length=64)
    client_message_id: str | None = Field(default=None, max_length=128)


class SettingAssistantSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16384)
    client_message_id: str = Field(min_length=1, max_length=128)
    document_id: UUID | None = None
    document_version_id: UUID | None = None


class AssistantMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_results: list[dict[str, Any]] | None = None
    created_at: datetime


class AssistantConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    project_id: UUID | None
    setting_collection_id: UUID | None = None
    chapter_id: UUID | None = None
    title: str | None = None
    messages: list[AssistantMessageResponse] = []
    created_at: datetime
    updated_at: datetime
