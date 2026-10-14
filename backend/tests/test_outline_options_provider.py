from types import SimpleNamespace
import json

from pydantic import SecretStr
import pytest

from app.agents import outline_options
from app.llm.gateway import StructuredTransportResponse


@pytest.mark.anyio
async def test_outline_real_adapter_uses_the_strict_gateway_and_records_usage(monkeypatch):
    calls = []

    class Transport:
        def __init__(self, **kwargs):
            pass

        async def call(self, request):
            calls.append(request)
            return StructuredTransportResponse(payload=json.dumps({"candidates": [
                {"title": f"Direction {i}", "summary": f"Choice {i}", "content": f"Opening {i}. Conflict, author choice and visible consequence."}
                for i in range(3)]}), input_tokens=123, output_tokens=456)

        async def aclose(self):
            pass

    monkeypatch.setattr(outline_options, "OpenAICompatibleStructuredOutputTransport", Transport)
    monkeypatch.setattr(outline_options, "settings", SimpleNamespace(chapter_generation_provider="openai_compatible",
        openai_compatible_base_url="https://model.invalid/v1", openai_compatible_api_key=SecretStr("fixture-key"),
        openai_compatible_model="fixture-model", openai_compatible_timeout_seconds=30))
    options, provenance = await outline_options.generate_outline_options({"idea_text": "Keep the evidence"})
    assert len(options.candidates) == 3
    assert len(calls) == 1
    assert calls[0].output_schema["additionalProperties"] is False
    assert provenance["provider_kind"] == "openai_compatible"
    assert provenance["input_tokens"] == 123 and provenance["output_tokens"] == 456
