"""Strict, provider-neutral contracts for setting change proposals."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _canonical_uuid(value: object) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("invalid reference") from None
    if parsed.int == 0 or str(parsed) != str(value).lower():
        raise ValueError("invalid reference")
    return parsed


def _bounded_text(value: str, field_name: str, *, min_len: int = 1, max_len: int = 65_536) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    stripped = value.strip()
    if len(stripped) < min_len or len(stripped) > max_len or "\x00" in stripped:
        raise ValueError(f"invalid {field_name}")
    return stripped


class SettingProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SettingProposalSourceTask(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )

    workflow_run_id: UUID | None = None
    chapter_id: UUID | None = None
    agent_role: str | None = None
    novel_title: str | None = None

    @field_validator("workflow_run_id", "chapter_id", mode="before")
    @classmethod
    def canonical_optional_uuid(cls, value: object) -> UUID | None:
        if value is None or value == "":
            return None
        return _canonical_uuid(value)

    @field_validator("agent_role", "novel_title", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        raise ValueError("expected string")


class SettingChangeProposal(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )

    id: UUID
    setting_collection_id: UUID
    target_document_id: UUID | None = None
    base_version_id: UUID | None = None
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=50)
    proposed_content: str = Field(min_length=1, max_length=65_536)
    reason: str = Field(min_length=1, max_length=2_000)
    source_task: SettingProposalSourceTask = Field(default_factory=SettingProposalSourceTask)
    status: SettingProposalStatus = SettingProposalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("id", "setting_collection_id", "target_document_id", "base_version_id", mode="before")
    @classmethod
    def canonical_uuid(cls, value: object) -> UUID | None:
        if value is None or value == "":
            return None
        return _canonical_uuid(value)

    @field_validator("title", "reason", mode="before")
    @classmethod
    def validate_title_and_reason(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        max_len = 100 if field_name == "title" else 2_000
        return _bounded_text(str(value), field_name, min_len=1, max_len=max_len)

    @field_validator("proposed_content", mode="before")
    @classmethod
    def validate_proposed_content(cls, value: object) -> str:
        return _bounded_text(str(value), "proposed_content", min_len=1, max_len=65_536)

    @field_validator("status", mode="before")
    @classmethod
    def canonical_status(cls, value: object) -> SettingProposalStatus:
        if isinstance(value, SettingProposalStatus):
            return value
        if isinstance(value, str):
            return SettingProposalStatus(value.lower())
        raise ValueError("invalid status")

    @field_validator("created_at", mode="before")
    @classmethod
    def canonical_created_at(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError("invalid created_at")

    @model_validator(mode="after")
    def validate_version_binding(self) -> Self:
        if self.target_document_id is not None and self.base_version_id is None:
            raise ValueError("base_version_id is required when target_document_id is specified")
        if self.target_document_id is None and self.base_version_id is not None:
            raise ValueError("base_version_id must not be specified for a new document proposal")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "setting_collection_id": str(self.setting_collection_id),
            "target_document_id": str(self.target_document_id) if self.target_document_id else None,
            "base_version_id": str(self.base_version_id) if self.base_version_id else None,
            "title": self.title,
            "category": self.category,
            "proposed_content": self.proposed_content,
            "reason": self.reason,
            "source_task": {
                "workflow_run_id": str(self.source_task.workflow_run_id) if self.source_task.workflow_run_id else None,
                "chapter_id": str(self.source_task.chapter_id) if self.source_task.chapter_id else None,
                "agent_role": self.source_task.agent_role,
                "novel_title": self.source_task.novel_title,
            },
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SettingChangeProposal:
        return cls.model_validate(data)
