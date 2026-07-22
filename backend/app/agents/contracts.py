"""Strict transient request and untrusted-output contracts for ConceptAgent."""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.llm.errors import ProviderInvalidOutputError


_OPTION_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")


class _StrictConceptModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ConceptAgentRequest(_StrictConceptModel):
    """Project-creation prompt context.  This is intentionally never persisted."""

    project_id: UUID
    workflow_run_id: UUID | None = None
    user_seed: str = Field(min_length=1, max_length=4000)
    target_platform: str | None = Field(default=None, min_length=1, max_length=500)
    preferred_genres: list[str] = Field(default_factory=list, max_length=10)
    disliked_elements: list[str] = Field(default_factory=list, max_length=10)
    style_preference: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("user_seed")
    @classmethod
    def valid_user_seed(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("invalid text")
        return value

    @field_validator("preferred_genres", "disliked_elements")
    @classmethod
    def valid_preference_list(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("invalid text")
        return value

    @field_validator("target_platform", "style_preference")
    @classmethod
    def optional_bounded_platform(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("invalid text")
        return value


class ConceptOption(_StrictConceptModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=160)
    logline: str = Field(min_length=1, max_length=600)
    premise: str = Field(min_length=1, max_length=2000)
    genres: list[str] = Field(min_length=1, max_length=6)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if _OPTION_ID.fullmatch(value) is None:
            raise ValueError("invalid option id")
        return value

    @field_validator("title", "logline", "premise")
    @classmethod
    def nonempty_text(cls, value: object) -> object:
        values = value if isinstance(value, list) else [value]
        if any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 256
            or "\n" in item
            or "\r" in item
            for item in values
        ):
            raise ValueError("invalid text")
        return value

    @field_validator("genres")
    @classmethod
    def canonical_genres(cls, value: list[str]) -> list[str]:
        if any(
            not item
            or item != item.strip()
            or len(item) > 256
            or "," in item
            or "\n" in item
            or "\r" in item
            for item in value
        ):
            raise ValueError("invalid genre")
        return value


class ConceptGenerationOutput(_StrictConceptModel):
    options: list[ConceptOption] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_option_ids(self) -> "ConceptGenerationOutput":
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("duplicate option id")
        return self


class ChiefEditorIssue(_StrictConceptModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"[a-z][a-z0-9_-]{0,63}")
    message: str = Field(min_length=1, max_length=500)


class ChiefEditorReviewOutput(_StrictConceptModel):
    passed: bool
    summary: str = Field(default="Concept review completed.", min_length=1, max_length=1000)
    blocking_issues: list[ChiefEditorIssue] = Field(default_factory=list, max_length=12)
    warnings: list[ChiefEditorIssue] = Field(default_factory=list, max_length=12)
    notes: list[ChiefEditorIssue] = Field(default_factory=list, max_length=12)
    suggested_actions: list[ChiefEditorIssue] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def consistent(self) -> "ChiefEditorReviewOutput":
        if self.passed and self.blocking_issues:
            raise ValueError("passed review cannot have blocking issues")
        if not self.passed and not self.blocking_issues:
            raise ValueError("failed review needs a blocking issue")
        return self


def validate_concept_generation_output(raw_output: object) -> ConceptGenerationOutput:
    """Normalize every untrusted provider-schema failure to one safe error."""
    try:
        if isinstance(raw_output, ConceptGenerationOutput):
            raw_output = raw_output.model_dump()
        return ConceptGenerationOutput.model_validate(raw_output)
    except (TypeError, ValueError, ValidationError):
        raise ProviderInvalidOutputError() from None


def validate_chief_editor_review_output(raw_output: object) -> ChiefEditorReviewOutput:
    try:
        if isinstance(raw_output, ChiefEditorReviewOutput):
            raw_output = raw_output.model_dump()
        return ChiefEditorReviewOutput.model_validate(raw_output)
    except (TypeError, ValueError, ValidationError):
        raise ProviderInvalidOutputError() from None
