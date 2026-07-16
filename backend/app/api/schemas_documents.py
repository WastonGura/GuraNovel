"""Pydantic request and response schemas for versioned documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import DocumentSource, DocumentType


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    version_number: int
    parent_version_id: UUID | None
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
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    chapter_id: UUID | None
    type: DocumentType
    title: str | None
    path: str
    current_version_id: UUID | None
    current_version: DocumentVersionResponse | None
    created_at: datetime
    updated_at: datetime


class DocumentContentResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    content: str


class CreateDocumentRequest(BaseModel):
    project_id: UUID
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


class RestoreDocumentRequest(BaseModel):
    expected_current_version_id: UUID
    source: DocumentSource = DocumentSource.USER
    actor_user_id: UUID | None = None
    agent_role: str | None = None
    workflow_run_id: UUID | None = None
    change_summary: str | None = None
