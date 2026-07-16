"""Pydantic v2 schemas for project endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.workspace import ProjectWorkspace, UnsafeProjectWorkspaceError


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    genre: str | None = None
    target_platform: str | None = None
    metadata_: dict = Field(default_factory=dict, validation_alias="metadata")

    @field_validator("slug")
    @classmethod
    def validate_workspace_slug(cls, slug: str) -> str:
        try:
            ProjectWorkspace.validate_slug(slug)
        except UnsafeProjectWorkspaceError as error:
            raise PydanticCustomError("unsafe_project_slug", "{message}", {"message": str(error)}) from error
        return slug


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
