"""Schemas for Studio Assistant conversations and messages."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class HomeAssistantSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=16384)
    client_message_id: str = Field(min_length=1, max_length=128)
    current_view: Literal["home"] = "home"

    @field_validator("content", "client_message_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A non-blank value is required.")
        return value


class HomeAssistantConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_conversation: bool = False
    client_conversation_id: UUID | None = None


class GlobalHomeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["home"]
    current_view: Literal["home"] | None = None


class GlobalProjectContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["project"]
    project_id: UUID
    chapter_id: UUID | None = None
    current_view: str | None = Field(default=None, max_length=64)


class GlobalCollectionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["collection"]
    collection_id: UUID
    document_id: UUID | None = None
    document_version_id: UUID | None = None


class GlobalAssistantSendRequest(BaseModel):
    # Keep the original home-only body as a separate compatibility contract.
    # Global requests always carry a complete, discriminated per-message context.
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16384)
    client_message_id: str = Field(min_length=1, max_length=128)
    context: Annotated[
        GlobalHomeContext | GlobalProjectContext | GlobalCollectionContext,
        Field(discriminator="kind"),
    ]

    @field_validator("content", "client_message_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A non-blank value is required.")
        return value


class AssistantConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    preview: str


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
