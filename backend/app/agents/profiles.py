"""Strict, local-only registry for bundled agent profiles."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agents.errors import ProfileRegistryError
from app.llm.contracts import validate_model_identifier


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PATH = re.compile(r"[a-z][a-z0-9_/-]{0,127}(?:\.md)?")
_SAFE_TEXT = re.compile(r"[^\x00]{1,4000}")


class _StrictProfileModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ModelProfile(_StrictProfileModel):
    provider: Literal["openai_compatible", "fake"]
    model: str = Field(min_length=1, max_length=128)
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)
    max_tokens: int = Field(ge=1, le=16384)
    response_format: Literal["json_schema"]

    @field_validator("temperature", "top_p", mode="before")
    @classmethod
    def float_only(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, float):
            raise ValueError("must be a float")
        return value

    @field_validator("model")
    @classmethod
    def safe_model_identifier(cls, value: str) -> str:
        return validate_model_identifier(value)


class PermissionsProfile(_StrictProfileModel):
    can_read: list[str] = Field(max_length=16)
    can_write: list[str] = Field(max_length=16)
    cannot: list[str] = Field(max_length=16)

    @field_validator("can_read", "can_write", "cannot")
    @classmethod
    def safe_capabilities(cls, value: list[str]) -> list[str]:
        if any(_PATH.fullmatch(item) is None for item in value):
            raise ValueError("invalid capability")
        return value


class ContextPolicyProfile(_StrictProfileModel):
    required: list[str] = Field(max_length=16)
    optional: list[str] = Field(max_length=16)
    max_context_tokens: int = Field(ge=1, le=32768)

    @field_validator("required", "optional")
    @classmethod
    def safe_context_names(cls, value: list[str]) -> list[str]:
        if any(_IDENTIFIER.fullmatch(item) is None for item in value):
            raise ValueError("invalid context name")
        return value


class AgentProfile(_StrictProfileModel):
    name: Literal["concept_agent", "chief_editor"]
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    agent_role: Literal["concept_agent", "chief_editor_agent"]
    model: ModelProfile
    permissions: PermissionsProfile
    context_policy: ContextPolicyProfile
    system_prompt: str = Field(min_length=1, max_length=4000)
    output_schema: Literal["concept_generation_output", "chief_editor_review_output"]

    @field_validator("version")
    @classmethod
    def safe_version(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("invalid version")
        return value

    @field_validator("description", "system_prompt")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if _SAFE_TEXT.fullmatch(value) is None:
            raise ValueError("invalid text")
        return value


class ProfileRegistry:
    """Load the one bundled profile known to this application version.

    Profile selection is deliberately not a general filesystem API.  The caller can
    request only ``concept_agent`` and cannot influence a provider endpoint or any
    credential-bearing configuration.
    """

    def __init__(self, profiles_directory: Path | None = None) -> None:
        self._profiles_directory = profiles_directory or Path(__file__).with_name("profiles")

    def load(self, name: str) -> AgentProfile:
        if name not in {"concept_agent", "chief_editor"}:
            raise ProfileRegistryError()
        try:
            filename = "concept.yaml" if name == "concept_agent" else "chief_editor.yaml"
            raw = yaml.safe_load((self._profiles_directory / filename).read_text())
            return AgentProfile.model_validate(raw)
        except (OSError, yaml.YAMLError, TypeError, ValueError, ValidationError):
            raise ProfileRegistryError() from None
