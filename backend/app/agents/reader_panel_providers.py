"""OpenAI-compatible real model provider for Reader Panel reader personas and moderator stages."""

from __future__ import annotations

from typing import Any
import httpx
from pydantic import SecretStr

from app.agents.profiles import AgentProfile, ProfileRegistry
from app.agents.reader_panel_agents import (
    READER_BLIND_BALLOT_SYSTEM_PROMPT,
    READER_DISCUSSION_TURN_SYSTEM_PROMPT,
    READER_FINAL_BALLOT_SYSTEM_PROMPT,
    build_blind_ballot_user_prompt,
    build_discussion_summary_user_prompt,
    build_discussion_turn_user_prompt,
    build_final_ballot_user_prompt,
    build_initial_reading_user_prompt,
    build_issue_extraction_user_prompt,
    build_report_synthesis_user_prompt,
)
from app.agents.reader_panel_contracts import (
    ModeratorDiscussionSummaryOutput,
    ModeratorDiscussionSummaryRequest,
    ModeratorIssueExtractionOutput,
    ModeratorIssueExtractionRequest,
    ModeratorReportSynthesisOutput,
    ModeratorReportSynthesisRequest,
    ReaderBallotOutput,
    ReaderBlindBallotRequest,
    ReaderDiscussionTurnOutput,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotOutput,
    ReaderFinalBallotRequest,
    ReaderInitialReadingOutput,
    ReaderInitialReadingRequest,
)
from app.llm.gateway import (
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputRequest,
)
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport


class OpenAICompatibleReaderPanelProvider:
    """Real model provider for all 7 stages of Reader Panel evaluation and moderation."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | str,
        *,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
        registry: ProfileRegistry | None = None,
    ) -> None:
        self._transport = OpenAICompatibleStructuredOutputTransport(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._registry = registry or ProfileRegistry()
        self._gateways: dict[str, StructuredOutputGateway[Any]] = {}
        self.last_provenance: str | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    async def aclose(self) -> None:
        """Close underlying HTTP transport resources."""
        await self._transport.aclose()

    def _get_gateway(
        self,
        profile: AgentProfile,
        schema_name: str,
        schema_cls: type[Any],
        *,
        override_system_prompt: str | None = None,
        stage_key: str | None = None,
    ) -> StructuredOutputGateway[Any]:
        key = f"{profile.name}_{profile.mode or ''}_{stage_key or schema_name}_{profile.version}"
        if key not in self._gateways:
            model_identifier = self._model if self._model is not None else profile.model.model
            system_prompt = override_system_prompt or profile.system_prompt
            gateway_profile = StructuredOutputProfile(
                profile_id=key,
                provider_kind="openai_compatible",
                model_identifier=model_identifier,
                prompt_template_version=profile.version,
                system_prompt=system_prompt,
                output_schema_name=schema_name,
                output_schema=schema_cls,
                timeout_seconds=self._timeout_seconds,
                max_input_chars=120_000,
                max_output_bytes=240_000,
                temperature=profile.model.temperature,
                top_p=profile.model.top_p,
                max_tokens=profile.model.max_tokens,
            )
            self._gateways[key] = StructuredOutputGateway(gateway_profile, self._transport)
        return self._gateways[key]

    async def generate_initial_reading(
        self, request: ReaderInitialReadingRequest
    ) -> ReaderInitialReadingOutput:
        """Isolated cold-reading evaluation by reader persona."""
        profile = self._registry.load(request.reader_profile_id)
        gateway = self._get_gateway(
            profile,
            "reader_initial_reading_output",
            ReaderInitialReadingOutput,
            stage_key="initial_reading",
        )
        user_prompt = build_initial_reading_user_prompt(
            request, persona_description=profile.description
        )
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

    async def extract_issues(
        self, request: ModeratorIssueExtractionRequest
    ) -> ModeratorIssueExtractionOutput:
        """Neutral issue extraction and deduplication across reader reports."""
        profile = self._registry.load("moderator_agent", "issue_extraction")
        gateway = self._get_gateway(
            profile,
            "moderator_issue_extraction_output",
            ModeratorIssueExtractionOutput,
            stage_key="issue_extraction",
        )
        user_prompt = build_issue_extraction_user_prompt(request)
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

    async def generate_blind_ballot(
        self, request: ReaderBlindBallotRequest
    ) -> ReaderBallotOutput:
        """Blind independent balloting on a single extracted issue."""
        profile = self._registry.load(request.reader_profile_id)
        gateway = self._get_gateway(
            profile,
            "reader_ballot_output",
            ReaderBallotOutput,
            override_system_prompt=READER_BLIND_BALLOT_SYSTEM_PROMPT,
            stage_key="blind_ballot",
        )
        user_prompt = build_blind_ballot_user_prompt(request)
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

    async def generate_discussion_turn(
        self, request: ReaderDiscussionTurnRequest
    ) -> ReaderDiscussionTurnOutput:
        """Individual reader discussion turn with concession/action suggestions."""
        profile = self._registry.load(request.reader_profile_id)
        gateway = self._get_gateway(
            profile,
            "reader_discussion_turn_output",
            ReaderDiscussionTurnOutput,
            override_system_prompt=READER_DISCUSSION_TURN_SYSTEM_PROMPT,
            stage_key="discussion_turn",
        )
        user_prompt = build_discussion_turn_user_prompt(request)
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

    async def summarize_discussion(
        self, request: ModeratorDiscussionSummaryRequest
    ) -> ModeratorDiscussionSummaryOutput:
        """Neutral moderator summary of a discussion round without voting."""
        profile = self._registry.load("moderator_agent", "discussion_summary")
        gateway = self._get_gateway(
            profile,
            "moderator_discussion_summary_output",
            ModeratorDiscussionSummaryOutput,
            stage_key="discussion_summary",
        )
        user_prompt = build_discussion_summary_user_prompt(request)
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

    async def generate_final_ballot(
        self, request: ReaderFinalBallotRequest
    ) -> ReaderFinalBallotOutput:
        """Independent final ballot post-discussion indicating position changes."""
        profile = self._registry.load(request.reader_profile_id)
        gateway = self._get_gateway(
            profile,
            "reader_final_ballot_output",
            ReaderFinalBallotOutput,
            override_system_prompt=READER_FINAL_BALLOT_SYSTEM_PROMPT,
            stage_key="final_ballot",
        )
        user_prompt = build_final_ballot_user_prompt(request)
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

    async def synthesize_report(
        self, request: ModeratorReportSynthesisRequest
    ) -> ModeratorReportSynthesisOutput:
        """Moderator synthesis of the final diagnostic reader panel report."""
        profile = self._registry.load("moderator_agent", "report_synthesis")
        gateway = self._get_gateway(
            profile,
            "moderator_report_synthesis_output",
            ModeratorReportSynthesisOutput,
            stage_key="report_synthesis",
        )
        user_prompt = build_report_synthesis_user_prompt(request)
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
