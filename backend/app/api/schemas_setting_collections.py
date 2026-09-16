"""Pydantic v2 schemas for setting collection endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.models import DocumentSource, DocumentType
from app.workspace import ProjectWorkspace, UnsafeProjectWorkspaceError


class CreateSettingCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    slug: str | None = None
    description: str | None = None
    owner_id: UUID | None = None
    metadata_: dict = Field(default_factory=dict, validation_alias="metadata")

    @field_validator("slug")
    @classmethod
    def validate_workspace_slug(cls, slug: str | None) -> str | None:
        if slug is None:
            return None
        try:
            ProjectWorkspace.validate_slug(slug)
        except UnsafeProjectWorkspaceError as error:
            raise PydanticCustomError("unsafe_project_slug", "{message}", {"message": str(error)}) from error
        return slug


class UpdateSettingCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    metadata_: dict | None = Field(default=None, validation_alias="metadata")


class SettingCollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    owner_id: UUID | None
    slug: str
    title: str
    description: str | None
    status: str
    workspace_root: str
    revision: int
    metadata_: dict = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class CreateSettingDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DocumentType
    title: str | None = None
    path: str
    content: str
    source: DocumentSource = DocumentSource.USER
    actor_user_id: UUID | None = None
    agent_role: str | None = None
    workflow_run_id: UUID | None = None
    change_summary: str | None = None
    metadata_: dict = Field(default_factory=dict, validation_alias="metadata")
