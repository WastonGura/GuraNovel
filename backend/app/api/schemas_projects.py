"""Pydantic v2 schemas for project endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    genre: str | None = None
    target_platform: str | None = None
    metadata_: dict = Field(default_factory=dict, validation_alias="metadata")


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    slug: str
    title: str
    genre: str | None
    target_platform: str | None
    status: str
    workspace_root: str
    metadata_: dict = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime
