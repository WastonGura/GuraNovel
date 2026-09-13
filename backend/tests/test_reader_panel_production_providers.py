"""Unit tests for OpenAI-compatible Reader Panel provider and moderation gateway."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.agents.profiles import ProfileRegistry
from app.agents.reader_panel_agents import (
    build_blind_ballot_request,
    build_cold_read_request,
)
from app.agents.reader_panel_contracts import (
    Confidence,
    ContinueReadingVote,
    DiscussionNovelty,
    DiscussionStance,
    EvidenceRef,
    ExtractedIssueItem,
    ModeratorDiscussionSummaryOutput,
    ModeratorDiscussionSummaryRequest,
    ModeratorIssueExtractionOutput,
    ModeratorIssueExtractionRequest,
    ModeratorReportSynthesisOutput,
    ModeratorReportSynthesisRequest,
    ReaderBallotOutput,
    ReaderDiscussionTurnOutput,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotOutput,
    ReaderFinalBallotRequest,
    ReaderInitialReadingOutput,
    Severity,
    SuggestedAction,
)
from app.agents.reader_panel_fakes import DeterministicReaderPanelProvider
from app.agents.reader_panel_providers import OpenAICompatibleReaderPanelProvider
from app.api.deps import get_reader_panel_provider
from app.core.config import Settings
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


def _make_mock_client(
    handler_or_payload: Any,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    recorded_requests: list[dict[str, Any]] = []

    async def handle_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        recorded_requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": body,
            }
        )
        if callable(handler_or_payload):
            return handler_or_payload(request, body)

        if isinstance(handler_or_payload, Exception):
            raise handler_or_payload

        response_body = {
            "id": "chatcmpl-mock-reader-panel",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            json.dumps(handler_or_payload)
                            if isinstance(handler_or_payload, dict)
                            else str(handler_or_payload)
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 100,
                "total_tokens": 250,
            },
        }
        resp_headers = headers or {
            "content-type": "application/json",
            "content-encoding": "identity",
        }
        payload_bytes = json.dumps(response_body).encode("utf-8")
        return httpx.Response(
            status_code=status_code,
            headers=resp_headers,
            stream=_CountingStream([payload_bytes]),
        )

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport, base_url="https://mock-provider.test/v1/")
    return client, recorded_requests


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "project_creation_provider": "fake",
        "project_maintenance_provider": "fake",
        "chapter_generation_provider": "fake",
        "chapter_production_provider": "fake",
        "reader_panel_provider": "fake",
        "reader_panel_mode": "off",
        "openai_compatible_base_url": None,
        "openai_compatible_api_key": None,
        "openai_compatible_model": None,
        "openai_compatible_timeout_seconds": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _sample_initial_reading_payload() -> dict[str, Any]:
    return {
        "overall_reaction": "Compelling atmosphere and solid character introduction in opening scene.",
        "continue_reading": "yes",
        "confidence": "high",
        "strengths": [
            {
                "summary": "Effective sensory details and mood.",
                "evidence": [{"segment_ids": ["S001"], "note": "Opening hook"}],
            }
        ],
        "reactions": [
            {
                "segment_ids": ["S001"],
                "reaction": "Immediately immersed in the scene.",
                "emotion": "engaged",
                "confusion": None,
            }
        ],
        "concerns": [
            {
                "category": "pacing",
                "symptom": "Dialogue repeats exposition slightly.",
                "severity": "minor",
                "evidence": [{"segment_ids": ["S002"], "note": "Repetition"}],
                "suggested_action": "compress",
            }
        ],
    }


def _sample_issue_extraction_payload() -> dict[str, Any]:
    return {
        "issues": [
            {
                "issue_number": 1,
                "title": "Repetitive dialogue exposition in middle section",
                "category": "pacing",
                "symptom": "Exposition in dialogue slows scene tempo",
                "root_cause_hypotheses": ["Lore inserted mid-conversation"],
                "evidence": [{"segment_ids": ["S002"], "note": "Dialogue lore"}],
                "source_reader_ids": ["general_immersive"],
                "target_audience_relevance": "medium",
                "minority_risk": False,
                "discussion_status": "queued",
            }
        ]
    }


def _sample_ballot_payload() -> dict[str, Any]:
    return {
        "issue_number": 1,
        "severity": "minor",
        "suggested_action": "compress",
        "confidence": "high",
        "evidence": [{"segment_ids": ["S002"], "note": "Pacing evidence"}],
        "reason": "Dialogue can easily be tightened by 20% without losing tone.",
    }


def _sample_discussion_turn_payload() -> dict[str, Any]:
    return {
        "stance": "support",
        "claim": "Trimming redundant dialogue tags will restore action momentum.",
        "evidence": [{"segment_ids": ["S002"], "note": "Discussion evidence"}],
        "concession": "The lore background is still necessary.",
        "proposed_action": "Compress middle dialogue lines.",
        "novelty": "new_interpretation",
    }


def _sample_discussion_summary_payload() -> dict[str, Any]:
    return {
        "round_summary": "Readers agree on targeted dialogue compression while preserving lore points.",
        "remaining_disagreements": [],
        "suggested_focus": "Focus on tightening middle paragraph.",
        "is_consensus_reached": True,
    }


def _sample_final_ballot_payload() -> dict[str, Any]:
    return {
        "issue_number": 1,
        "severity": "minor",
        "suggested_action": "compress",
        "confidence": "high",
        "evidence": [{"segment_ids": ["S002"], "note": "Final evidence"}],
        "position_changed": False,
        "change_reason": None,
        "remaining_disagreement": None,
    }


def _sample_report_synthesis_payload() -> dict[str, Any]:
    return {
        "executive_summary": "Reader panel evaluated the chapter draft with high continuation willingness.",
        "target_audience_appeal": "Strong fit for genre readers seeking atmospheric mystery.",
        "key_findings": [
            {
                "issue_number": 1,
                "title": "Repetitive dialogue exposition in middle section",
                "consensus_class": "strong_consensus",
                "recommended_priority": "must_fix",
                "summary": "Exposition in dialogue slows scene tempo",
                "evidence": [{"segment_ids": ["S002"], "note": "Dialogue lore"}],
            }
        ],
        "actionable_recommendations": [
            {
                "priority": "must_fix",
                "target_segment_ids": ["S002"],
                "suggested_action": "compress",
                "instruction": "Trim repetitive dialogue exposition in S002.",
            }
        ],
    }


# =============================================================================
# Provider Unit Tests (Isolated HTTP Mock)
# =============================================================================

class TestOpenAICompatibleReaderPanelProvider:
    @pytest.fixture
    def registry(self) -> ProfileRegistry:
        return ProfileRegistry()

    @pytest.mark.anyio
    async def test_generate_initial_reading_success_and_input_isolation(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_initial_reading_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-reader-v1",
            client=client,
            registry=registry,
        )

        request = build_cold_read_request(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="urban fantasy",
            target_audience=["young adult fantasy"],
            manuscript_segments={"S001": "The fog crept into the alley.", "S002": "He spoke at length."},
            test_goals=["check pacing"],
        )

        result = await provider.generate_initial_reading(request)

        assert isinstance(result, ReaderInitialReadingOutput)
        assert result.continue_reading == ContinueReadingVote.YES
        assert result.confidence == Confidence.HIGH
        assert len(result.strengths) == 1
        assert len(result.concerns) == 1
        assert result.concerns[0].severity == Severity.MINOR

        assert provider.last_input_tokens == 150
        assert provider.last_output_tokens == 100
        assert provider.last_provenance is not None

        # Verify request structure and input isolation
        assert len(recorded_requests) == 1
        sent = recorded_requests[0]
        assert sent["method"] == "POST"
        assert sent["url"] == "https://mock-provider.test/v1/chat/completions"

        messages = sent["json"]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "[S001]: The fog crept into the alley." in user_content
        assert "[S002]: He spoke at length." in user_content

        # CRITICAL ISOLATION CHECK: Cold read request must NOT leak any peer reports or other reader IDs
        assert "genre_experienced" not in user_content
        assert "low_patience" not in user_content
        assert "peer" not in user_content.lower()

    @pytest.mark.anyio
    async def test_extract_issues_success_and_moderator_boundaries(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_issue_extraction_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-moderator-v1",
            client=client,
            registry=registry,
        )

        request = ModeratorIssueExtractionRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_initial_reports={
                "general_immersive": {
                    "overall_reaction": "Good read",
                    "continue_reading": "yes",
                    "concerns": [
                        {
                            "category": "pacing",
                            "symptom": "Dialogue exposition slows scene tempo",
                            "severity": "minor",
                            "evidence": [{"segment_ids": ["S002"], "note": "Dialogue"}],
                        }
                    ],
                }
            },
            manuscript_segments={"S001": "Intro text.", "S002": "Dialogue text."},
            max_ballot_issues=4,
        )

        result = await provider.extract_issues(request)

        assert isinstance(result, ModeratorIssueExtractionOutput)
        assert len(result.issues) == 1
        assert result.issues[0].issue_number == 1
        assert result.issues[0].category == "pacing"
        assert result.issues[0].evidence[0].segment_ids == ["S002"]

        sent = recorded_requests[0]
        messages = sent["json"]["messages"]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "vote" in system_content.lower()  # zero voting authority reminder

    @pytest.mark.anyio
    async def test_generate_blind_ballot_masks_source_and_peer_votes(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_ballot_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-reader-v1",
            client=client,
            registry=registry,
        )

        sample_issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue exposition load",
            category="pacing",
            symptom="Exposition slows scene tempo",
            root_cause_hypotheses=["Lore inserted mid-conversation"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Dialogue")],
            source_reader_ids=["genre_experienced", "low_patience"],  # Originators
        )

        request = build_blind_ballot_request(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            issue=sample_issue,
            manuscript_segments={"S002": "Dialogue text."},
        )

        result = await provider.generate_blind_ballot(request)

        assert isinstance(result, ReaderBallotOutput)
        assert result.issue_number == 1
        assert result.severity == Severity.MINOR
        assert result.suggested_action == SuggestedAction.COMPRESS

        # CRITICAL ISOLATION CHECK: Blind ballot MUST NOT reveal source reader IDs or vote counts
        sent = recorded_requests[0]
        user_content = next(m["content"] for m in sent["json"]["messages"] if m["role"] == "user")
        assert "genre_experienced" not in user_content
        assert "low_patience" not in user_content
        assert "vote count" not in user_content.lower()
        assert "tally" not in user_content.lower()

    @pytest.mark.anyio
    async def test_generate_discussion_turn_success(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_discussion_turn_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-reader-v1",
            client=client,
            registry=registry,
        )

        sample_issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue exposition load",
            category="pacing",
            symptom="Exposition slows scene tempo",
            root_cause_hypotheses=["Lore inserted mid-conversation"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Dialogue")],
        )

        request = ReaderDiscussionTurnRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            issue=sample_issue,
            round_number=1,
            turn_number=1,
            prior_messages=[{"sender": "style_sensitive", "claim": "Pacing seems a bit slow."}],
            prior_ballot={"severity": "minor", "suggested_action": "compress", "reason": "Trim tags"},
            manuscript_segments={"S002": "Dialogue text."},
        )

        result = await provider.generate_discussion_turn(request)

        assert isinstance(result, ReaderDiscussionTurnOutput)
        assert result.stance == DiscussionStance.SUPPORT
        assert result.novelty == DiscussionNovelty.NEW_INTERPRETATION
        assert result.concession is not None

        # Verify turn context includes prior ballot and prior messages
        sent = recorded_requests[0]
        user_content = next(m["content"] for m in sent["json"]["messages"] if m["role"] == "user")
        assert "Pacing seems a bit slow." in user_content
        assert "Your Prior Ballot" in user_content

    @pytest.mark.anyio
    async def test_summarize_discussion_success(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_discussion_summary_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-moderator-v1",
            client=client,
            registry=registry,
        )

        sample_issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue exposition load",
            category="pacing",
            symptom="Exposition slows scene tempo",
            root_cause_hypotheses=["Lore inserted mid-conversation"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Dialogue")],
        )

        request = ModeratorDiscussionSummaryRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            issue=sample_issue,
            round_number=1,
            round_messages=[{"sender": "general_immersive", "claim": "Trim tags."}],
        )

        result = await provider.summarize_discussion(request)

        assert isinstance(result, ModeratorDiscussionSummaryOutput)
        assert result.is_consensus_reached is True
        assert "compression" in result.round_summary

    @pytest.mark.anyio
    async def test_generate_final_ballot_success(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_final_ballot_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-reader-v1",
            client=client,
            registry=registry,
        )

        sample_issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue exposition load",
            category="pacing",
            symptom="Exposition slows scene tempo",
            root_cause_hypotheses=["Lore inserted mid-conversation"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Dialogue")],
        )

        request = ReaderFinalBallotRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            issue=sample_issue,
            round_summaries=["Readers agreed to compress."],
            initial_ballot={"severity": "minor", "suggested_action": "compress"},
            manuscript_segments={"S002": "Dialogue text."},
        )

        result = await provider.generate_final_ballot(request)

        assert isinstance(result, ReaderFinalBallotOutput)
        assert result.issue_number == 1
        assert result.position_changed is False

    @pytest.mark.anyio
    async def test_synthesize_report_success_and_server_consensus(
        self, registry: ProfileRegistry
    ) -> None:
        client, recorded_requests = _make_mock_client(_sample_report_synthesis_payload())
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            model="reader-panel-moderator-v1",
            client=client,
            registry=registry,
        )

        sample_issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue exposition load",
            category="pacing",
            symptom="Exposition slows scene tempo",
            root_cause_hypotheses=["Lore inserted mid-conversation"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Dialogue")],
        )

        request = ModeratorReportSynthesisRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            initial_reports={"general_immersive": {"overall_reaction": "Good", "continue_reading": "yes"}},
            extracted_issues=[sample_issue],
            final_consensus_results={
                1: {
                    "consensus_class": "strong_consensus",
                    "recommended_priority": "must_fix",
                    "suggested_action": "compress",
                }
            },
            minority_risk_issues=[],
        )

        result = await provider.synthesize_report(request)

        assert isinstance(result, ModeratorReportSynthesisOutput)
        assert len(result.key_findings) == 1
        assert len(result.actionable_recommendations) == 1
        assert result.actionable_recommendations[0].suggested_action == SuggestedAction.COMPRESS

        # Check server consensus was provided in prompt
        sent = recorded_requests[0]
        user_content = next(m["content"] for m in sent["json"]["messages"] if m["role"] == "user")
        assert "Server Consensus Class: strong_consensus" in user_content

    @pytest.mark.anyio
    async def test_provider_handles_rate_limiting(self, registry: ProfileRegistry) -> None:
        client, _ = _make_mock_client(
            {"error": "Too Many Requests"},
            status_code=429,
        )
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            client=client,
            registry=registry,
        )
        request = build_cold_read_request(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="fantasy",
            target_audience=["general"],
            manuscript_segments={"S001": "Text."},
        )
        with pytest.raises(ProviderRateLimitedError):
            await provider.generate_initial_reading(request)

    @pytest.mark.anyio
    async def test_provider_handles_timeout(self, registry: ProfileRegistry) -> None:
        client, _ = _make_mock_client(httpx.ReadTimeout("Read timed out"))
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            client=client,
            registry=registry,
        )
        request = build_cold_read_request(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="fantasy",
            target_audience=["general"],
            manuscript_segments={"S001": "Text."},
        )
        with pytest.raises(ProviderTimeoutError):
            await provider.generate_initial_reading(request)

    @pytest.mark.anyio
    async def test_provider_handles_unavailable(self, registry: ProfileRegistry) -> None:
        client, _ = _make_mock_client(
            {"error": "Bad Gateway"},
            status_code=502,
        )
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            client=client,
            registry=registry,
        )
        request = build_cold_read_request(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="fantasy",
            target_audience=["general"],
            manuscript_segments={"S001": "Text."},
        )
        with pytest.raises(ProviderUnavailableError):
            await provider.generate_initial_reading(request)

    @pytest.mark.anyio
    async def test_provider_handles_malformed_json_output(self, registry: ProfileRegistry) -> None:
        client, _ = _make_mock_client("not-valid-json-string")
        provider = OpenAICompatibleReaderPanelProvider(
            base_url="https://mock-provider.test/v1",
            api_key=SecretStr("sk-test-secret-key-12345"),
            client=client,
            registry=registry,
        )
        request = build_cold_read_request(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="fantasy",
            target_audience=["general"],
            manuscript_segments={"S001": "Text."},
        )
        with pytest.raises(ProviderInvalidOutputError):
            await provider.generate_initial_reading(request)


# =============================================================================
# Configuration & Dependency Injection Tests
# =============================================================================

class TestReaderPanelConfigAndDeps:
    def test_deps_fake_provider_selected_by_default(self) -> None:
        settings_fake = make_settings(reader_panel_provider="fake")
        provider = get_reader_panel_provider(settings_fake)
        assert isinstance(provider, DeterministicReaderPanelProvider)

    def test_deps_openai_compatible_fails_closed_when_missing_credentials(self) -> None:
        settings_missing = make_settings(
            reader_panel_provider="openai_compatible",
            openai_compatible_base_url=None,
            openai_compatible_api_key=None,
        )
        with pytest.raises(ProviderConfigurationError):
            get_reader_panel_provider(settings_missing)

    def test_deps_openai_compatible_fails_closed_when_missing_base_url(self) -> None:
        settings_missing_url = make_settings(
            reader_panel_provider="openai_compatible",
            openai_compatible_base_url=None,
            openai_compatible_api_key=SecretStr("sk-test"),
            openai_compatible_model="reader-v1",
            openai_compatible_timeout_seconds=30.0,
        )
        with pytest.raises(ProviderConfigurationError):
            get_reader_panel_provider(settings_missing_url)

    def test_deps_openai_compatible_returns_real_provider_when_configured(self) -> None:
        settings_valid = make_settings(
            reader_panel_provider="openai_compatible",
            openai_compatible_base_url="https://api.openai.com/v1",
            openai_compatible_api_key=SecretStr("sk-test-123"),
            openai_compatible_model="gpt-4o-mini",
            openai_compatible_timeout_seconds=45.0,
        )
        provider = get_reader_panel_provider(settings_valid)
        assert isinstance(provider, OpenAICompatibleReaderPanelProvider)
