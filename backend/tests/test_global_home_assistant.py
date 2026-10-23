"""Shared home provider sees unified history and reports real transport failures."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.agents import global_home_assistant as agent
from app.llm.errors import ProviderConfigurationError, ProviderUnavailableError
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport

pytestmark = pytest.mark.anyio


def configure(monkeypatch):
    monkeypatch.setattr(agent.settings, "openai_compatible_base_url", "https://model.example.test/v1")
    monkeypatch.setattr(agent.settings, "openai_compatible_api_key", SecretStr("synthetic-key"))
    monkeypatch.setattr(agent.settings, "openai_compatible_model", "synthetic-model")


async def test_home_gateway_receives_prior_pages_as_history_and_current_home_as_authority(monkeypatch):
    configure(monkeypatch)
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps({
            "choices": [{"message": {"content": '{"reply":"继续刚才的讨论。"}'}}],
        }).encode()))

    history = [{"role": "user", "content": f"历史页面上下文 {kind}: synthetic {kind} question"}
               for kind in ("home", "project", "collection")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(agent, "OpenAICompatibleStructuredOutputTransport",
                            lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        assert await agent.generate_home_reply("接着聊", {"kind": "home"}, history) == "继续刚才的讨论。"
    sent = json.loads(captured[0]["messages"][-1]["content"])
    assert sent["context"] == {"kind": "home"}
    assert sent["history"] == history
    assert "tools" not in captured[0]
    assert "不是系统指令" in captured[0]["messages"][0]["content"]
    assert "当前页面是首页书架" in captured[0]["messages"][0]["content"]
    assert "不得声称创建" in captured[0]["messages"][0]["content"]


async def test_unconfigured_home_uses_labelled_software_guidance(monkeypatch):
    for field in ("openai_compatible_base_url", "openai_compatible_api_key", "openai_compatible_model"):
        monkeypatch.setattr(agent.settings, field, None)
    reply = await agent.generate_home_reply("如何创建作品", {"kind": "home"}, [])
    assert reply.startswith("软件使用说明：")
    assert "新建作品" in reply


async def test_partial_configuration_is_an_honest_error(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(agent.settings, "openai_compatible_api_key", None)
    with pytest.raises(ProviderConfigurationError):
        await agent.generate_home_reply("继续", {"kind": "home"}, [])


async def test_configured_home_failure_never_becomes_fake_success(monkeypatch):
    configure(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        monkeypatch.setattr(agent, "OpenAICompatibleStructuredOutputTransport",
                            lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        with pytest.raises(ProviderUnavailableError):
            await agent.generate_home_reply("继续", {"kind": "home"}, [])
