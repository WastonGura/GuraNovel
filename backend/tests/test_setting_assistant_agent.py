"""The collection assistant uses real transport contracts, never a fake-success fallback."""
import json

import httpx
import pytest
from pydantic import SecretStr

from app.agents import setting_assistant
from app.llm.errors import ProviderConfigurationError, ProviderUnavailableError
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


def configured(monkeypatch):
    monkeypatch.setattr(setting_assistant.settings, "openai_compatible_base_url", "https://model.example.test/v1")
    monkeypatch.setattr(setting_assistant.settings, "openai_compatible_api_key", SecretStr("synthetic-test-key"))
    monkeypatch.setattr(setting_assistant.settings, "openai_compatible_model", "test-model")


@pytest.mark.anyio
async def test_context_is_read_only_data_and_uses_shared_transport(monkeypatch):
    configured(monkeypatch)
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        envelope = {"choices": [{"message": {"content": '{"reply":"这是只读说明。"}'}}]}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(envelope).encode()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(setting_assistant, "OpenAICompatibleStructuredOutputTransport", lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        context = {"collection_id": "collection-A", "document_version_id": "version-1", "body": "Synthetic saved text"}
        result = await setting_assistant.generate_setting_reply("如何返回主页", context, [])
    assert result == "这是只读说明。"
    assert json.loads(sent[0]["messages"][-1]["content"])["context"] == context
    assert "tools" not in sent[0]
    assert "已保存" in sent[0]["messages"][0]["content"]
    assert "侧栏底部" in sent[0]["messages"][0]["content"]
    assert "回到书架" in sent[0]["messages"][0]["content"]
    assert "左上角房屋图标" not in sent[0]["messages"][0]["content"]


@pytest.mark.anyio
async def test_missing_configuration_does_not_fabricate_a_reply(monkeypatch):
    monkeypatch.setattr(setting_assistant.settings, "openai_compatible_api_key", None)
    with pytest.raises(ProviderConfigurationError):
        await setting_assistant.generate_setting_reply("Hello", {}, [])


@pytest.mark.anyio
async def test_model_failure_remains_a_retryable_error(monkeypatch):
    configured(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        monkeypatch.setattr(setting_assistant, "OpenAICompatibleStructuredOutputTransport", lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        with pytest.raises(ProviderUnavailableError):
            await setting_assistant.generate_setting_reply("Hello", {}, [])
