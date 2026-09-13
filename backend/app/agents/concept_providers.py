"""OpenAI-compatible providers for project concept generation and review."""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from app.agents.chief_editor import ChiefEditorProvider
from app.agents.concept_agent import ConceptProvider
from app.agents.contracts import (
    ChiefEditorReviewOutput,
    ConceptAgentRequest,
    ConceptGenerationOutput,
)
from app.agents.profiles import AgentProfile
from app.llm.gateway import (
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputRequest,
)
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport


class OpenAICompatibleConceptProvider(ConceptProvider):
    """Real OpenAI-compatible model provider for concept option generation."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | str,
        *,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = OpenAICompatibleStructuredOutputTransport(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._gateways: dict[str, StructuredOutputGateway[ConceptGenerationOutput]] = {}
        self.last_provenance: str | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    def _get_gateway(self, profile: AgentProfile) -> StructuredOutputGateway[ConceptGenerationOutput]:
        key = f"{profile.name}_{profile.version}"
        if key not in self._gateways:
            model_identifier = self._model if self._model is not None else profile.model.model
            gateway_profile = StructuredOutputProfile(
                profile_id=f"{profile.name}_{profile.version}",
                provider_kind="openai_compatible",
                model_identifier=model_identifier,
                prompt_template_version=profile.version,
                system_prompt=profile.system_prompt,
                output_schema_name="concept_generation_output",
                output_schema=ConceptGenerationOutput,
                timeout_seconds=self._timeout_seconds,
                max_input_chars=100_000,
                max_output_bytes=200_000,
                temperature=profile.model.temperature,
                top_p=profile.model.top_p,
                max_tokens=profile.model.max_tokens,
            )
            self._gateways[key] = StructuredOutputGateway(gateway_profile, self._transport)
        return self._gateways[key]

    @staticmethod
    def _build_user_prompt(request: ConceptAgentRequest) -> str:
        lines: list[str] = [
            "--- Project Creation Seed Context ---",
            f"User Seed Prompt: {request.user_seed}",
            f"Target Platform: {request.target_platform or 'None specified'}",
            f"Preferred Genres: {', '.join(request.preferred_genres) if request.preferred_genres else 'None specified'}",
            f"Disliked Elements: {', '.join(request.disliked_elements) if request.disliked_elements else 'None specified'}",
            f"Style Preference: {request.style_preference or 'None specified'}",
            "",
            "--- Output Requirements ---",
            "Generate between 1 and 5 distinct, high-concept novel options conforming strictly to the declared JSON schema.",
            "Each option must have:",
            "- id: lowercase alphanumeric identifier matching regex '^[a-z][a-z0-9-]{0,63}$' (e.g. 'cyberpunk-heist', 'mist-detective')",
            "- title: single-line book title (no newlines, max 160 characters)",
            "- logline: single-line high-stakes pitch hook (no newlines, max 600 characters)",
            "- premise: single-line core dramatic premise (no newlines, max 2000 characters)",
            "- genres: list of 1 to 6 genre strings (no commas or newlines, e.g. ['fantasy', 'mystery'])",
            "Ensure all option IDs are unique.",
        ]
        return "\n".join(lines)

    async def generate_concepts(
        self, request: ConceptAgentRequest, profile: AgentProfile
    ) -> ConceptGenerationOutput:
        gateway = self._get_gateway(profile)
        user_prompt = self._build_user_prompt(request)
        response = await gateway.call(
            StructuredOutputRequest(
                profile_id=gateway.profile_id,
                user_prompt=user_prompt,
            )
        )
        self.last_provenance = response.provenance
        self.last_input_tokens = response.input_tokens
        self.last_output_tokens = response.output_tokens
        return response.result

    async def aclose(self) -> None:
        await self._transport.aclose()


class OpenAICompatibleConceptChiefEditorProvider(ChiefEditorProvider):
    """Real OpenAI-compatible model provider for Chief Editor concept review."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | str,
        *,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = OpenAICompatibleStructuredOutputTransport(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._gateways: dict[str, StructuredOutputGateway[ChiefEditorReviewOutput]] = {}
        self.last_provenance: str | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    def _get_gateway(self, profile: AgentProfile) -> StructuredOutputGateway[ChiefEditorReviewOutput]:
        key = f"{profile.name}_{profile.version}"
        if key not in self._gateways:
            model_identifier = self._model if self._model is not None else profile.model.model
            gateway_profile = StructuredOutputProfile(
                profile_id=f"{profile.name}_{profile.version}",
                provider_kind="openai_compatible",
                model_identifier=model_identifier,
                prompt_template_version=profile.version,
                system_prompt=profile.system_prompt,
                output_schema_name="chief_editor_review_output",
                output_schema=ChiefEditorReviewOutput,
                timeout_seconds=self._timeout_seconds,
                max_input_chars=100_000,
                max_output_bytes=200_000,
                temperature=profile.model.temperature,
                top_p=profile.model.top_p,
                max_tokens=profile.model.max_tokens,
            )
            self._gateways[key] = StructuredOutputGateway(gateway_profile, self._transport)
        return self._gateways[key]

    @staticmethod
    def _build_user_prompt(concepts: ConceptGenerationOutput) -> str:
        lines: list[str] = [
            "--- Concept Options Under Review ---",
        ]
        for opt in concepts.options:
            lines.append(f"Option ID: {opt.id}")
            lines.append(f"Title: {opt.title}")
            lines.append(f"Logline: {opt.logline}")
            lines.append(f"Premise: {opt.premise}")
            lines.append(f"Genres: {', '.join(opt.genres)}")
            lines.append("")

        lines.extend([
            "--- Review Criteria ---",
            "Evaluate commercial market viability, premise clarity, narrative promise, and genre coherence.",
            "Produce a JSON object conforming strictly to chief_editor_review_output.",
            "- If all concepts are acceptable or have only non-fatal advisories, set passed=true and leave blocking_issues empty.",
            "- If there are fatal flaws in the pitch that render development impossible, set passed=false and supply at least one blocking issue.",
            "- Every issue code must match regex '^[a-z][a-z0-9_-]{0,63}$'.",
            "- Provide summary, warnings, notes, and suggested_actions as appropriate.",
        ])
        return "\n".join(lines)

    async def review_concepts(
        self, concepts: ConceptGenerationOutput, profile: AgentProfile
    ) -> ChiefEditorReviewOutput:
        gateway = self._get_gateway(profile)
        user_prompt = self._build_user_prompt(concepts)
        response = await gateway.call(
            StructuredOutputRequest(
                profile_id=gateway.profile_id,
                user_prompt=user_prompt,
            )
        )
        self.last_provenance = response.provenance
        self.last_input_tokens = response.input_tokens
        self.last_output_tokens = response.output_tokens
        return response.result

    async def aclose(self) -> None:
        await self._transport.aclose()


__all__ = [
    "OpenAICompatibleConceptChiefEditorProvider",
    "OpenAICompatibleConceptProvider",
]
