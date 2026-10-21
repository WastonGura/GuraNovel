"""Version-bound authoring requests; model authority never comes from the client."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.setting_proposal_contracts import SettingChangeProposal
from app.api.schemas_documents import DocumentResponse


class SettingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=65536)


class SettingCollaborationSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=16000)
    base_version_id: UUID
    draft_content: str = Field(max_length=65536)
    selection: SettingSelection | None = None

    @model_validator(mode="after")
    def selection_matches_exact_draft(self):
        if not self.content.strip() or "\x00" in self.content + self.draft_content:
            raise ValueError("Message and draft must be valid text")
        if self.selection is not None:
            selection = self.selection
            encoded = self.draft_content.encode("utf-16-le")
            try:
                selected = encoded[selection.start * 2:selection.end * 2].decode("utf-16-le")
            except UnicodeDecodeError:
                raise ValueError("Selection must use complete UTF-16 characters") from None
            if selection.start >= selection.end or selection.end * 2 > len(encoded) or selected != selection.text:
                raise ValueError("Selection does not match the draft")
        return self


class SettingProposalAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_current_version_id: UUID | None
    expected_draft_content: str = Field(max_length=65536)
    expected_title: str = Field(max_length=1000)


class SettingCollectionCollaborationSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("client_message_id", "content")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Message identity and content must be nonempty valid text")
        return value


class SettingCollaborationMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    client_message_id: str | None
    proposal: SettingChangeProposal | None
    proposal_status: Literal["pending", "accepted", "rejected"] | None
    base_content: str | None
    base_title: str | None
    accepted_version_id: UUID | None
    created_at: datetime


class SettingCollaborationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    setting_collection_id: UUID
    document_id: UUID | None
    agent_role: Literal["worldbuilding_agent", "lore_agent"]
    messages: list[SettingCollaborationMessageResponse]
    created_at: datetime
    updated_at: datetime


class SettingCollaborationAcceptanceResponse(BaseModel):
    conversation: SettingCollaborationResponse
    document: DocumentResponse
    content: str
