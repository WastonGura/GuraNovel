"""Bounded public Studio persistence requests."""

from datetime import datetime
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.documents.chapter_segments import MAX_CHAPTER_CONTENT_BYTES


class OutlineApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    expected_current_version_id: UUID

    @field_validator("document_id", "expected_current_version_id")
    @classmethod
    def nonzero_uuid(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("UUID must not be nil")
        return value


class OutlineApprovalResponse(BaseModel):
    chapter_id: UUID
    document_id: UUID
    version_id: UUID


class DraftWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=MAX_CHAPTER_CONTENT_BYTES)
    expected_current_version_id: UUID

    @field_validator("content")
    @classmethod
    def bounded_content(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_CHAPTER_CONTENT_BYTES:
            raise ValueError("Chapter text is too large")
        return value

    @field_validator("expected_current_version_id")
    @classmethod
    def nonzero_uuid(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("UUID must not be nil")
        return value


class RestorePointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_current_version_id: UUID

    @field_validator("request_id", "expected_current_version_id")
    @classmethod
    def nonzero_uuid(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("UUID must not be nil")
        return value


class RestorePointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_id: UUID
    document_id: UUID
    version_id: UUID
    summary: str
    created_at: datetime


FeedbackRegion = Literal["outline", "draft"]
COMMENT_COLORS = (
    "#8d9bff", "#ff858d", "#ffbe69", "#8fcba9", "#be94df", "#70bdcf",
    "#447ac2", "#b96972", "#af8c31", "#458466", "#8256a9", "#377b86",
)


class StudioComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    start: int = Field(ge=0, le=10_000_000, strict=True)
    end: int = Field(ge=0, le=10_000_000, strict=True)
    quote: str = Field(min_length=1, max_length=8192)
    text: str = Field(max_length=4000)
    color: str
    submitted: bool = Field(default=False, strict=True)
    orphaned: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid_comment(self):
        if not self.id.int or self.end < self.start or self.color not in COMMENT_COLORS:
            raise ValueError("Invalid comment identity, range or color")
        self.quote.encode("utf-8")
        self.text.encode("utf-8")
        if "\x00" in self.quote or "\x00" in self.text:
            raise ValueError("Text must not contain null characters")
        return self


class FeedbackWriteRequest(RestorePointRequest):
    expected_revision: int = Field(ge=0, strict=True)
    comments: list[StudioComment] = Field(max_length=1024)
    requirements: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def valid_feedback(self):
        self.requirements.encode("utf-8")
        if "\x00" in self.requirements:
            raise ValueError("Text must not contain null characters")
        if len({comment.id for comment in self.comments}) != len(self.comments):
            raise ValueError("Comment IDs must be unique")
        if len(self.model_dump_json().encode("utf-8")) > 1_048_576:
            raise ValueError("Feedback is too large")
        return self


class FeedbackSubmitRequest(RestorePointRequest):
    expected_revision: int = Field(ge=0, strict=True)
    comment_ids: list[UUID] = Field(max_length=1024)

    @field_validator("comment_ids")
    @classmethod
    def unique_comment_ids(cls, value: list[UUID]):
        if len(set(value)) != len(value) or any(not item.int for item in value):
            raise ValueError("Comment IDs must be unique and non-nil")
        return value


class FeedbackResponse(BaseModel):
    chapter_id: UUID
    region: FeedbackRegion
    document_id: UUID | None
    source_version_id: UUID | None
    revision: int
    comments: list[StudioComment]
    requirements: str
    read_only: bool


class RestorePointFeedbackResponse(BaseModel):
    point_id: UUID
    document_id: UUID
    source_version_id: UUID
    available: bool
    comments: list[StudioComment]
    requirements: str


class FeedbackSubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_id: UUID
    region: FeedbackRegion
    document_id: UUID
    source_version_id: UUID
    feedback_revision: int
    comments: list[StudioComment]
    requirements: str
    created_at: datetime
