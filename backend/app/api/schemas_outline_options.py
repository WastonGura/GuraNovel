from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.outline_options import OutlineOption


class OutlineGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_idea_revision: int = Field(ge=0, strict=True)

    @field_validator("request_id")
    @classmethod
    def nonzero(cls, value):
        if not value.int:
            raise ValueError("Nil request identity")
        return value


class OutlineCandidate(OutlineOption):
    id: UUID


class OutlineGenerationResponse(BaseModel):
    id: UUID
    project_id: UUID
    chapter_id: UUID
    status: Literal["running", "completed", "failed", "uncertain", "cancelled"]
    idea_text: str
    idea_revision: int
    context_hash: str
    candidates: list[OutlineCandidate]
    started_at: datetime
    failure_code: str | None
    provenance: dict


class OutlineSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    candidate_id: UUID
    expected_current_version_id: UUID | None

    @field_validator("request_id", "candidate_id", "expected_current_version_id")
    @classmethod
    def nonzero(cls, value):
        if value is not None and not value.int:
            raise ValueError("Nil identity")
        return value


class OutlineSelectionResponse(BaseModel):
    chapter_id: UUID
    document_id: UUID
    version_id: UUID
