"""Injected provider boundaries for project-maintenance analysis and planning."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agents.errors import ProfileRegistryError
from app.agents.maintenance_contracts import (
    ApplyChangeOutput,
    ApplyChangeRequest,
    ChiefEditorMaintenanceImpactOutput,
    ConsistencyReviewOutput,
    LoreImpactOutput,
    MaintenanceImpactRequest,
    PostChangeRequest,
    RevisionPlanOutput,
    RevisionPlanRequest,
    validate_apply_change_output,
    validate_chief_editor_impact_output,
    validate_consistency_review_output,
    validate_lore_impact_output,
    validate_revision_plan_output,
)
from app.agents.profiles import AgentProfile, ProfileRegistry
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


_SAFE_PROVIDER_ERRORS = (
    ProfileRegistryError,
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _safe_error_type(error: Exception) -> type[Exception]:
    for error_type in _SAFE_PROVIDER_ERRORS:
        if isinstance(error, error_type):
            return error_type
    return ProviderUnavailableError


@runtime_checkable
class MaintenanceImpactProvider(Protocol):
    async def analyze_maintenance_impact(
        self, request: MaintenanceImpactRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class RevisionPlanProvider(Protocol):
    async def plan_revision(
        self, request: RevisionPlanRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class ApplyChangeProvider(Protocol):
    async def propose_changes(
        self, request: ApplyChangeRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class PostChangeProvider(Protocol):
    async def review_consistency(
        self, request: PostChangeRequest, profile: AgentProfile
    ) -> object: ...


class LoreAgent:
    def __init__(
        self,
        provider: MaintenanceImpactProvider | PostChangeProvider,
        registry: ProfileRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def validate_output(
        raw_output: object, *, request: MaintenanceImpactRequest | None = None
    ) -> LoreImpactOutput:
        return validate_lore_impact_output(raw_output, request=request)

    async def analyze(self, request: MaintenanceImpactRequest) -> LoreImpactOutput:
        failure: type[Exception] | None = None
        try:
            if not isinstance(self._provider, MaintenanceImpactProvider):
                raise ProviderConfigurationError()
            profile = self._registry.load("lore_agent", mode="maintenance_impact")
            raw_output = await self._provider.analyze_maintenance_impact(request, profile)
            result = self.validate_output(raw_output, request=request)
        except _SAFE_PROVIDER_ERRORS as error:
            failure = _safe_error_type(error)
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        return result

    async def maintenance_impact(self, request: MaintenanceImpactRequest) -> LoreImpactOutput:
        return await self.analyze(request)

    @staticmethod
    def validate_post_change_output(
        raw_output: object, *, request: PostChangeRequest
    ) -> ConsistencyReviewOutput:
        return validate_consistency_review_output(raw_output, request=request)

    async def post_change(self, request: PostChangeRequest) -> ConsistencyReviewOutput:
        failure: type[Exception] | None = None
        try:
            if not isinstance(self._provider, PostChangeProvider):
                raise ProviderConfigurationError()
            profile = self._registry.load("lore_agent", mode="post_change")
            raw_output = await self._provider.review_consistency(request, profile)
            result = self.validate_post_change_output(raw_output, request=request)
        except _SAFE_PROVIDER_ERRORS as error:
            failure = _safe_error_type(error)
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        return result


class ArchivistAgent:
    """Produce version proposals without receiving persistence or filesystem services."""

    def __init__(
        self, provider: ApplyChangeProvider, registry: ProfileRegistry | None = None
    ) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def validate_output(
        raw_output: object, *, request: ApplyChangeRequest
    ) -> ApplyChangeOutput:
        return validate_apply_change_output(raw_output, request=request)

    async def apply_change(self, request: ApplyChangeRequest) -> ApplyChangeOutput:
        failure: type[Exception] | None = None
        try:
            if not isinstance(self._provider, ApplyChangeProvider):
                raise ProviderConfigurationError()
            profile = self._registry.load("archivist_agent", mode="apply_change")
            raw_output = await self._provider.propose_changes(request, profile)
            result = self.validate_output(raw_output, request=request)
        except _SAFE_PROVIDER_ERRORS as error:
            failure = _safe_error_type(error)
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        return result


class ChiefEditorImpactAgent:
    def __init__(
        self, provider: MaintenanceImpactProvider, registry: ProfileRegistry | None = None
    ) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def validate_output(
        raw_output: object, *, request: MaintenanceImpactRequest | None = None
    ) -> ChiefEditorMaintenanceImpactOutput:
        return validate_chief_editor_impact_output(raw_output, request=request)

    async def analyze(
        self, request: MaintenanceImpactRequest
    ) -> ChiefEditorMaintenanceImpactOutput:
        failure: type[Exception] | None = None
        try:
            profile = self._registry.load("chief_editor", mode="maintenance_impact")
            raw_output = await self._provider.analyze_maintenance_impact(request, profile)
            result = self.validate_output(raw_output, request=request)
        except _SAFE_PROVIDER_ERRORS as error:
            failure = _safe_error_type(error)
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        return result

    async def maintenance_impact(
        self, request: MaintenanceImpactRequest
    ) -> ChiefEditorMaintenanceImpactOutput:
        return await self.analyze(request)


class _RevisionAgent:
    profile_name: str

    def __init__(
        self, provider: RevisionPlanProvider, registry: ProfileRegistry | None = None
    ) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def validate_output(
        raw_output: object, *, request: RevisionPlanRequest | None = None
    ) -> RevisionPlanOutput:
        return validate_revision_plan_output(raw_output, request=request)

    async def plan(self, request: RevisionPlanRequest) -> RevisionPlanOutput:
        failure: type[Exception] | None = None
        try:
            profile = self._registry.load(self.profile_name, mode="revision_plan")
            raw_output = await self._provider.plan_revision(request, profile)
            result = self.validate_output(raw_output, request=request)
        except _SAFE_PROVIDER_ERRORS as error:
            failure = _safe_error_type(error)
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        return result

    async def revision_plan(self, request: RevisionPlanRequest) -> RevisionPlanOutput:
        return await self.plan(request)


class PlotArchitectAgent(_RevisionAgent):
    profile_name = "plot_architect_agent"


class WorldbuildingAgent(_RevisionAgent):
    profile_name = "worldbuilding_agent"


# Explicit maintenance names avoid ambiguity with the existing concept-review
# ``ChiefEditor`` while retaining the design-document terminology.
ChiefEditorAgent = ChiefEditorImpactAgent
ChiefEditorMaintenanceAgent = ChiefEditorImpactAgent


__all__ = [
    "ApplyChangeProvider",
    "ArchivistAgent",
    "ChiefEditorImpactAgent",
    "ChiefEditorAgent",
    "ChiefEditorMaintenanceAgent",
    "LoreAgent",
    "MaintenanceImpactProvider",
    "PostChangeProvider",
    "PlotArchitectAgent",
    "RevisionPlanProvider",
    "WorldbuildingAgent",
]
