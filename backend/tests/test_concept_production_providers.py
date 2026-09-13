"""Unit tests for OpenAI-compatible concept generation and review providers."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.agents.concept_agent import ConceptAgent
from app.agents.concept_providers import (
    OpenAICompatibleConceptChiefEditorProvider,
    OpenAICompatibleConceptProvider,
)
from app.agents.contracts import (
    ChiefEditorReviewOutput,
    ConceptAgentRequest,
    ConceptGenerationOutput,
)
from app.agents.profiles import ProfileRegistry
from app.api.deps import get_project_creation_composition
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
            "id": "chatcmpl-mock-concept",
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
                "prompt_tokens": 120,
                "completion_tokens": 85,
                "total_tokens": 205,
            },
        }
        resp_headers = headers or {"content-type": "application/json", "content-encoding": "identity"}
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
        "reader_panel_mode": "off",
        "openai_compatible_base_url": None,
        "openai_compatible_api_key": None,
        "openai_compatible_model": None,
        "openai_compatible_timeout_seconds": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _make_concept_request() -> ConceptAgentRequest:
    return ConceptAgentRequest(
        project_id=uuid4(),
        user_seed="A clockwork detective solves locked room mysteries in gaslamp London.",
        target_platform="Webnovel",
        preferred_genres=["mystery", "steampunk"],
        disliked_elements=["harem"],
        style_preference="Victorian gothic tone",
    )


def _make_sample_concept_payload() -> dict[str, Any]:
    return {
        "options": [
            {
                "id": "brass-clockwork-detective",
                "title": "The Brass Key of Whitechapel",
                "logline": "An automaton investigator uncovers a high-society conspiracy behind a locked-room murder.",
                "premise": "In an alternate 1888 London, clockwork enforcers police the gaslit streets until a noble is slain in an airtight vault.",
                "genres": ["mystery", "steampunk"],
            }
        ]
    }


def _make_sample_review_payload(*, passed: bool = True) -> dict[str, Any]:
    if passed:
        return {
            "passed": True,
            "blocking_issues": [],
            "warnings": [],
            "notes": [{"code": "commercial_appeal", "message": "Commercial premise has strong appeal."}],
            "summary": "Concept is viable and well-defined.",
            "suggested_actions": [{"code": "develop_backstory", "message": "Develop the lead detective personal backstory."}],
        }
    return {
        "passed": False,
        "blocking_issues": [
            {
                "code": "fatal_conflict",
                "message": "The premise lacks a clear central antagonist motivation.",
            }
        ],
        "warnings": [],
        "notes": [],
        "summary": "Concept requires revision before proceeding.",
        "suggested_actions": [{"code": "rework_antagonist", "message": "Rework antagonist structure."}],
    }


class TestOpenAICompatibleConceptProvider:
    @pytest.mark.anyio
    async def test_generate_concepts_success(self) -> None:
        raw_concept_data = _make_sample_concept_payload()
        client, recorded = _make_mock_client(raw_concept_data)

        provider = OpenAICompatibleConceptProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            model="test-concept-model",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("concept_agent", mode=None)

        try:
            req = _make_concept_request()
            output = await provider.generate_concepts(req, profile)

            assert isinstance(output, ConceptGenerationOutput)
            assert len(output.options) == 1
            assert output.options[0].id == "brass-clockwork-detective"
            assert output.options[0].title == "The Brass Key of Whitechapel"
            assert output.options[0].genres == ["mystery", "steampunk"]
            assert provider.last_provenance is not None
            assert provider.last_input_tokens == 120
            assert provider.last_output_tokens == 85
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_generate_concepts_invalid_output(self) -> None:
        invalid_body = {"options": [{"id": "INVALID-ID", "title": ""}]}
        client, _ = _make_mock_client(invalid_body)

        provider = OpenAICompatibleConceptProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()

        try:
            agent = ConceptAgent(provider, registry=registry)
            with pytest.raises(ProviderInvalidOutputError):
                await agent.generate(_make_concept_request())
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_generate_concepts_rate_limited(self) -> None:
        client, _ = _make_mock_client({"error": "rate limit"}, status_code=429)
        provider = OpenAICompatibleConceptProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("concept_agent", mode=None)

        try:
            with pytest.raises(ProviderRateLimitedError):
                await provider.generate_concepts(_make_concept_request(), profile)
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_generate_concepts_server_unavailable(self) -> None:
        client, _ = _make_mock_client({"error": "service down"}, status_code=503)
        provider = OpenAICompatibleConceptProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("concept_agent", mode=None)

        try:
            with pytest.raises(ProviderUnavailableError):
                await provider.generate_concepts(_make_concept_request(), profile)
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_generate_concepts_timeout(self) -> None:
        client, _ = _make_mock_client(httpx.ReadTimeout("Request timed out"))
        provider = OpenAICompatibleConceptProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("concept_agent", mode=None)

        try:
            with pytest.raises(ProviderTimeoutError):
                await provider.generate_concepts(_make_concept_request(), profile)
        finally:
            await provider.aclose()


class TestOpenAICompatibleConceptChiefEditorProvider:
    @pytest.mark.anyio
    async def test_review_concepts_passed_success(self) -> None:
        raw_review_data = _make_sample_review_payload(passed=True)
        client, recorded = _make_mock_client(raw_review_data)

        provider = OpenAICompatibleConceptChiefEditorProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("chief_editor", mode=None)

        try:
            concepts = ConceptGenerationOutput.model_validate(_make_sample_concept_payload())
            output = await provider.review_concepts(concepts, profile)

            assert isinstance(output, ChiefEditorReviewOutput)
            assert output.passed is True
            assert len(output.blocking_issues) == 0
            assert "Commercial premise" in output.notes[0].message
            assert provider.last_provenance is not None
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_review_concepts_blocking_issues(self) -> None:
        raw_review_data = _make_sample_review_payload(passed=False)
        client, _ = _make_mock_client(raw_review_data)

        provider = OpenAICompatibleConceptChiefEditorProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("chief_editor", mode=None)

        try:
            concepts = ConceptGenerationOutput.model_validate(_make_sample_concept_payload())
            output = await provider.review_concepts(concepts, profile)

            assert isinstance(output, ChiefEditorReviewOutput)
            assert output.passed is False
            assert len(output.blocking_issues) == 1
            assert output.blocking_issues[0].code == "fatal_conflict"
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_review_concepts_invalid_output(self) -> None:
        # passed=True but blocking_issues is non-empty -> violates contract
        contradictory = {
            "passed": True,
            "blocking_issues": [{"code": "bad", "message": "should fail", "affected_concept_ids": []}],
            "warnings": [],
            "notes": [],
        }
        client, _ = _make_mock_client(contradictory)

        provider = OpenAICompatibleConceptChiefEditorProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        profile = registry.load("chief_editor", mode=None)

        try:
            concepts = ConceptGenerationOutput.model_validate(_make_sample_concept_payload())
            with pytest.raises(ProviderInvalidOutputError):
                await provider.review_concepts(concepts, profile)
        finally:
            await provider.aclose()


class TestProjectCreationCompositionDependencyInjection:
    def test_default_fake_provider(self) -> None:
        config = make_settings(project_creation_provider="fake")
        comp = get_project_creation_composition(configured_settings=config)
        assert comp is not None
        assert comp.concept_agent is not None
        assert comp.chief_editor is not None

    def test_configured_openai_compatible_provider(self) -> None:
        config = make_settings(
            project_creation_provider="openai_compatible",
            openai_compatible_base_url="http://localhost:8000/v1",
            openai_compatible_api_key=SecretStr("test-key"),
            openai_compatible_model="test-model",
            openai_compatible_timeout_seconds=30.0,
        )
        comp = get_project_creation_composition(configured_settings=config)
        assert comp is not None
        assert isinstance(comp.concept_agent._provider, OpenAICompatibleConceptProvider)
        assert isinstance(comp.chief_editor._provider, OpenAICompatibleConceptChiefEditorProvider)

    @pytest.mark.parametrize(
        "missing_kwargs",
        [
            {"openai_compatible_base_url": None},
            {"openai_compatible_api_key": None},
            {"openai_compatible_model": None},
            {"openai_compatible_timeout_seconds": None},
        ],
    )
    def test_openai_compatible_incomplete_config_raises(self, missing_kwargs: dict[str, Any]) -> None:
        base = {
            "project_creation_provider": "openai_compatible",
            "openai_compatible_base_url": "http://localhost:8000/v1",
            "openai_compatible_api_key": SecretStr("test-key"),
            "openai_compatible_model": "test-model",
            "openai_compatible_timeout_seconds": 30.0,
        }
        base.update(missing_kwargs)
        config = make_settings(**base)
        with pytest.raises(ProviderConfigurationError):
            get_project_creation_composition(configured_settings=config)
