"""Minimal injected-provider boundary for concept generation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agents.contracts import (
    ConceptAgentRequest,
    ConceptGenerationOutput,
    validate_concept_generation_output,
)
from app.agents.profiles import AgentProfile, ProfileRegistry
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@runtime_checkable
class ConceptProvider(Protocol):
    """Injected adapter; server composition owns provider URLs and credentials."""

    async def generate_concepts(
        self, request: ConceptAgentRequest, profile: AgentProfile
    ) -> object: ...


class ConceptAgent:
    def __init__(self, provider: ConceptProvider, registry: ProfileRegistry | None = None) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def validate_output(raw_output: object) -> ConceptGenerationOutput:
        return validate_concept_generation_output(raw_output)

    async def generate(self, request: ConceptAgentRequest) -> ConceptGenerationOutput:
        profile = self._registry.load("concept_agent")
        try:
            raw_output = await self._provider.generate_concepts(request, profile)
            return self.validate_output(raw_output)
        except (
            ProviderInvalidOutputError,
            ProviderConfigurationError,
            ProviderUnavailableError,
            ProviderTimeoutError,
            ProviderRateLimitedError,
        ):
            raise
        except Exception:
            raise ProviderUnavailableError() from None
