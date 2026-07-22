"""Strict, injected review boundary for the ConceptAgent artifact."""

from typing import Protocol, runtime_checkable
from app.agents.contracts import (
    ChiefEditorReviewOutput,
    ConceptGenerationOutput,
    validate_chief_editor_review_output,
)
from app.agents.profiles import AgentProfile, ProfileRegistry
from app.llm.errors import ProviderInvalidOutputError, ProviderUnavailableError


@runtime_checkable
class ChiefEditorProvider(Protocol):
    async def review_concepts(
        self, concepts: ConceptGenerationOutput, profile: AgentProfile
    ) -> object: ...


class ChiefEditor:
    def __init__(
        self, provider: ChiefEditorProvider, registry: ProfileRegistry | None = None
    ) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    async def review(self, concepts: ConceptGenerationOutput) -> ChiefEditorReviewOutput:
        try:
            return validate_chief_editor_review_output(
                await self._provider.review_concepts(concepts, self._registry.load("chief_editor"))
            )
        except (ProviderInvalidOutputError, ProviderUnavailableError):
            raise
        except Exception:
            raise ProviderUnavailableError() from None
