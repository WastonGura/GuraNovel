"""Pydantic request and response schemas for versioned documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas_studio import StudioComment

from app.models import DocumentSource, DocumentType


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    version_number: int
    parent_version_id: UUID | None
    restored_from_version_id: UUID | None = None
    restored_from_restore_point_id: UUID | None = None
    source: DocumentSource
    actor_user_id: UUID | None
    agent_role: str | None
    workflow_run_id: UUID | None
    content_hash: str
    byte_size: int
    word_count: int
    file_path: str
    change_summary: str | None
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    project_id: UUID | None = None
    setting_collection_id: UUID | None = None
    chapter_id: UUID | None = None
    type: DocumentType
    title: str | None
    path: str
    current_version_id: UUID | None
    current_version: DocumentVersionResponse | None
    metadata_: dict = Field(default_factory=dict, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class DocumentContentResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    content: str


class CreateDocumentRequest(BaseModel):
    project_id: UUID | None = None
    setting_collection_id: UUID | None = None
    type: DocumentType
    title: str | None = None
    path: str
    content: str
    source: DocumentSource = DocumentSource.USER
    chapter_id: UUID | None = None
    actor_user_id: UUID | None = None
    agent_role: str | None = None
    workflow_run_id: UUID | None = None
    change_summary: str | None = None


class WriteDocumentRequest(BaseModel):
    content: str
    expected_current_version_id: UUID
    source: DocumentSource = DocumentSource.USER
    actor_user_id: UUID | None = None
    agent_role: str | None = None
    workflow_run_id: UUID | None = None
    change_summary: str | None = None
    setting_comments: list["StudioComment"] | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def valid_setting_anchors(self):
        if self.setting_comments is not None:
            if len({comment.id for comment in self.setting_comments}) != len(self.setting_comments):
                raise ValueError("Duplicate comment identity")
            encoded = self.content.encode("utf-16-le")
            for comment in self.setting_comments:
                if comment.orphaned:
                    continue
                try:
                    quote = encoded[comment.start * 2:comment.end * 2].decode("utf-16-le")
                except UnicodeDecodeError:
                    raise ValueError("Invalid comment anchor") from None
                if comment.end * 2 > len(encoded) or quote != comment.quote:
                    raise ValueError("Comment anchor does not match content")
        return self


class RestoreDocumentRequest(BaseModel):
    expected_current_version_id: UUID
    source: DocumentSource = DocumentSource.USER
    actor_user_id: UUID | None = None
    agent_role: str | None = None
    workflow_run_id: UUID | None = None
    change_summary: str | None = None


class UpdateDocumentRequest(BaseModel):
    title: str | None = None
    metadata_: dict | None = Field(default=None, validation_alias="metadata")

    @model_validator(mode="after")
    def preserve_server_metadata(self):
        if self.metadata_ and any(key.startswith("studio_") or key in {"comments", "comments_source_version_id", "generation_id", "candidate_id"} for key in self.metadata_):
            raise ValueError("Use the versioned editor for server-owned feedback and outline metadata")
        return self
