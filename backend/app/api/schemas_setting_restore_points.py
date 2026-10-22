"""Manual archives never accept a client-provided body, feedback or marker."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.api.schemas_documents import DocumentVersionResponse


class CreateSettingDocumentRestorePointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_current_version_id: UUID

    @field_validator("request_id", "expected_current_version_id")
    @classmethod
    def nonzero_uuid(cls, value: UUID) -> UUID:
        if not value.int:
            raise ValueError("UUID must not be nil")
        return value


class SettingDocumentRestorePointResponse(BaseModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    point_number: int
    summary: str | None
    message: str | None = None
    created_at: datetime
    version: DocumentVersionResponse
    matches_current: bool


class UpdateSettingDocumentRestorePointMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None
    expected_message: str | None

    @field_validator("message", "expected_message")
    @classmethod
    def single_line_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 160:
            raise ValueError("Archive messages must not exceed 160 characters")
        if any(
            ord(character) < 32 or ord(character) == 127 or character in "\x85\u2028\u2029"
            for character in value
        ):
            raise ValueError("Archive messages must be a single line without control characters")
        return value or None
