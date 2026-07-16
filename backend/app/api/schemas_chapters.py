"""Pydantic v2 schemas for project-scoped chapter endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateChapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    metadata_: dict = Field(default_factory=dict, validation_alias="metadata")


class ChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    project_id: UUID
    chapter_number: int
    title: str | None
    status: str
    current_outline_document_id: UUID | None
    current_draft_document_id: UUID | None
    final_document_id: UUID | None
    summary_document_id: UUID | None
    word_count: int
    metadata_: dict = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime
