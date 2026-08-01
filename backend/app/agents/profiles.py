"""Strict, local-only registry for bundled agent profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    name: Literal[
        "concept_agent",
        "chief_editor",
        "archivist_agent",
        "lore_agent",
        "plot_architect_agent",
        "worldbuilding_agent",
    ]
    mode: Literal["maintenance_impact", "revision_plan", "apply_change", "post_change"] | None = Field(
        default=None, exclude=True
    )
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    agent_role: Literal[
        "concept_agent",
        "chief_editor_agent",
        "archivist_agent",
        "lore_agent",
        "plot_architect_agent",
        "worldbuilding_agent",
    ]
    model: ModelProfile
    permissions: PermissionsProfile
    context_policy: ContextPolicyProfile
    system_prompt: str = Field(min_length=1, max_length=4000)
    output_schema: Literal[
        "concept_generation_output",
        "chief_editor_review_output",
        "lore_maintenance_impact_output",
        "chief_editor_maintenance_impact_output",
        "revision_plan_output",
        "apply_change_output",
        "consistency_review_output",
    ]

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


@dataclass(frozen=True)
class _ProfileManifest:
    filename: str
    agent_role: str
    output_schema: str
    can_read: frozenset[str]
    can_write: frozenset[str]
    cannot: frozenset[str]
    context_required: frozenset[str] | None = None
    context_optional: frozenset[str] | None = None


class ProfileRegistry:
    """Load only exact bundled agent/mode pairs known to this application version.

    Profile selection is deliberately not a general filesystem API.  The caller can
    never influence a path, provider endpoint, model, or credential-bearing setting.
    """

    _MANIFESTS: dict[tuple[str, str | None], _ProfileManifest] = {
        ("concept_agent", None): _ProfileManifest(
            "concept.yaml",
            "concept_agent",
            "concept_generation_output",
            frozenset({"project_creation_context"}),
            frozenset({"pitch/concept_options.md"}),
            frozenset({"network", "credentials"}),
        ),
        ("chief_editor", None): _ProfileManifest(
            "chief_editor.yaml",
            "chief_editor_agent",
            "chief_editor_review_output",
            frozenset({"pitch/concept_options.md"}),
            frozenset(),
            frozenset({"pitch/selected_concept.md"}),
        ),
        ("lore_agent", "maintenance_impact"): _ProfileManifest(
            "lore_maintenance_impact.yaml",
            "lore_agent",
            "lore_maintenance_impact_output",
            frozenset({"maintenance_context", "document_refs"}),
            frozenset(),
            frozenset({"network", "credentials", "document_versions"}),
        ),
        ("chief_editor", "maintenance_impact"): _ProfileManifest(
            "chief_editor_maintenance_impact.yaml",
            "chief_editor_agent",
            "chief_editor_maintenance_impact_output",
            frozenset({"maintenance_context", "document_refs"}),
            frozenset(),
            frozenset({"network", "credentials", "document_versions"}),
        ),
        ("plot_architect_agent", "revision_plan"): _ProfileManifest(
            "plot_architect_revision_plan.yaml",
            "plot_architect_agent",
            "revision_plan_output",
            frozenset({"maintenance_context", "document_refs"}),
            frozenset({"revision_plan"}),
            frozenset({"network", "credentials", "document_versions"}),
        ),
        ("worldbuilding_agent", "revision_plan"): _ProfileManifest(
            "worldbuilding_revision_plan.yaml",
            "worldbuilding_agent",
            "revision_plan_output",
            frozenset({"maintenance_context", "document_refs"}),
            frozenset({"revision_plan"}),
            frozenset({"network", "credentials", "document_versions"}),
        ),
        ("archivist_agent", "apply_change"): _ProfileManifest(
            "archivist_apply_change.yaml",
            "archivist_agent",
            "apply_change_output",
            frozenset({"maintenance_context", "approved_revision_plan"}),
            frozenset({"proposed_changes"}),
            frozenset(
                {"network", "credentials", "filesystem", "database", "document_service", "document_versions"}
            ),
            frozenset(
                {
                    "project_id",
                    "workflow_run_id",
                    "change_request_id",
                    "approval_id",
                    "revision_plan_id",
                    "revision_plan_document_id",
                    "revision_plan_version_id",
                    "operations",
                }
            ),
            frozenset(),
        ),
        ("lore_agent", "post_change"): _ProfileManifest(
            "lore_post_change.yaml",
            "lore_agent",
            "consistency_review_output",
            frozenset({"maintenance_context", "applied_changes"}),
            frozenset(),
            frozenset(
                {"network", "credentials", "filesystem", "database", "document_service", "document_versions"}
            ),
            frozenset(
                {
                    "project_id",
                    "workflow_run_id",
                    "change_request_id",
                    "approval_id",
                    "revision_plan_id",
                    "revision_plan_document_id",
                    "revision_plan_version_id",
                    "change_set_id",
                    "applied_changes",
                }
            ),
            frozenset(),
        ),
    }

    def __init__(self, profiles_directory: Path | None = None) -> None:
        self._profiles_directory = profiles_directory or Path(__file__).with_name("profiles")

    def load(self, name: str, mode: str | None = None) -> AgentProfile:
        manifest = self._MANIFESTS.get((name, mode))
        if manifest is None:
            raise ProfileRegistryError()
        profile: AgentProfile | None = None
        try:
            raw = yaml.safe_load((self._profiles_directory / manifest.filename).read_text())
            profile = AgentProfile.model_validate(raw)
        except (OSError, yaml.YAMLError, TypeError, ValueError, ValidationError):
            pass
        if profile is None or not self._matches_manifest(profile, name, mode, manifest):
            raise ProfileRegistryError() from None
        return profile

    @staticmethod
    def _matches_manifest(
        profile: AgentProfile,
        name: str,
        mode: str | None,
        manifest: _ProfileManifest,
    ) -> bool:
        permissions = profile.permissions
        return (
            profile.name == name
            and profile.mode == mode
            and profile.agent_role == manifest.agent_role
            and profile.output_schema == manifest.output_schema
            and len(permissions.can_read) == len(manifest.can_read)
            and frozenset(permissions.can_read) == manifest.can_read
            and len(permissions.can_write) == len(manifest.can_write)
            and frozenset(permissions.can_write) == manifest.can_write
            and len(permissions.cannot) == len(manifest.cannot)
            and frozenset(permissions.cannot) == manifest.cannot
            and (
                manifest.context_required is None
                or (
                    len(profile.context_policy.required) == len(manifest.context_required)
                    and frozenset(profile.context_policy.required) == manifest.context_required
                )
            )
            and (
                manifest.context_optional is None
                or (
                    len(profile.context_policy.optional) == len(manifest.context_optional)
                    and frozenset(profile.context_policy.optional) == manifest.context_optional
                )
            )
        )
