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
    target_platform: str | None = Field(default=None, min_length=1, max_length=128)
    preferred_genres: list[str] = Field(default_factory=list, max_length=12)
    disliked_elements: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("user_seed", "preferred_genres", "disliked_elements")
    @classmethod
    def bounded_text(cls, value: object) -> object:
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(item, str) or not item.strip() or len(item) > 256 for item in values):
            raise ValueError("invalid text")
        return value

    @field_validator("target_platform")
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

    @field_validator("title", "logline", "premise", "genres")
    @classmethod
    def nonempty_text(cls, value: object) -> object:
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(item, str) or not item.strip() or len(item) > 256 for item in values):
            raise ValueError("invalid text")
        return value


class ConceptGenerationOutput(_StrictConceptModel):
    options: list[ConceptOption] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_option_ids(self) -> "ConceptGenerationOutput":
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("duplicate option id")
        return self


def validate_concept_generation_output(raw_output: object) -> ConceptGenerationOutput:
    """Normalize every untrusted provider-schema failure to one safe error."""
    try:
        if isinstance(raw_output, ConceptGenerationOutput):
            raw_output = raw_output.model_dump()
        return ConceptGenerationOutput.model_validate(raw_output)
    except (TypeError, ValueError, ValidationError):
        raise ProviderInvalidOutputError() from None
