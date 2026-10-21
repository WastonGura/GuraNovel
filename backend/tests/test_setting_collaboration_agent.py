"""Real structured transport boundaries and exact version context for setting authoring."""
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.agents import setting_collaboration
from app.api.schemas_setting_collaboration import SettingCollaborationSendRequest
from app.llm.errors import ProviderConfigurationError, ProviderInvalidOutputError
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_setting_agent_uses_distinct_prompt_complete_proposal_and_shared_transport(monkeypatch):
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_base_url", "https://model.example.test/v1")
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_api_key", SecretStr("synthetic-test-key"))
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_model", "test-model")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        content = {"reply": "Here is a suggestion", "suggestion": {"proposed_content": "Old paragraph. New paragraph.", "reason": "Adds detail"}}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps({"choices": [{"message": {"content": json.dumps(content)}}], "usage": {"prompt_tokens": 40, "completion_tokens": 20}}).encode()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(setting_collaboration, "OpenAICompatibleStructuredOutputTransport", lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        result, provenance = await setting_collaboration.generate_setting_collaboration("Expand this", {"body": "Old paragraph.", "agent_role": "worldbuilding_agent", "base_version_id": "immutable-version"}, [])
    assert result.suggestion.proposed_content == "Old paragraph. New paragraph."
    assert "你不是全局助手 Gura" in requests[0]["messages"][0]["content"]
    assert "作者比较并明确采纳" in requests[0]["messages"][0]["content"]
    assert "tools" not in requests[0]
    assert json.loads(requests[0]["messages"][-1]["content"])["context"]["base_version_id"] == "immutable-version"
    assert provenance == {"provider_kind": "openai_compatible", "model_identifier": "test-model", "prompt_template_version": "setting-collaboration-v1", "input_tokens": 40, "output_tokens": 20}


@pytest.mark.anyio
async def test_missing_configuration_and_invalid_provider_output_never_fabricate_a_suggestion(monkeypatch):
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_api_key", None)
    with pytest.raises(ProviderConfigurationError):
        await setting_collaboration.generate_setting_collaboration("Hello", {}, [])
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_base_url", "https://model.example.test/v1")
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_api_key", SecretStr("synthetic-test-key"))
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_model", "test-model")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(b'{"choices":[{"message":{"content":"{\\"reply\\":\\"saved\\",\\"suggestion\\":null,\\"execute_write\\":true}"}}]}')))) as client:
        monkeypatch.setattr(setting_collaboration, "OpenAICompatibleStructuredOutputTransport", lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        with pytest.raises(ProviderInvalidOutputError):
            await setting_collaboration.generate_setting_collaboration("Hello", {"agent_role": "lore_agent"}, [])


def test_selection_is_bound_to_utf16_draft_without_splitting_emoji():
    from uuid import uuid4
    base = {"client_message_id": "selection", "content": "Explain", "base_version_id": uuid4(), "draft_content": "A🌊B"}
    assert SettingCollaborationSendRequest(**base, selection={"start": 1, "end": 3, "text": "🌊"}).selection.text == "🌊"
    with pytest.raises(ValidationError):
        SettingCollaborationSendRequest(**base, selection={"start": 1, "end": 2, "text": "🌊"})
    with pytest.raises(ValidationError):
        SettingCollaborationSendRequest(**base, selection={"start": 0, "end": 1, "text": "B"})


@pytest.mark.anyio
async def test_collection_agent_uses_the_whole_collection_contract_and_can_suggest_the_first_entry(monkeypatch):
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_base_url", "https://model.example.test/v1")
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_api_key", SecretStr("synthetic-test-key"))
    monkeypatch.setattr(setting_collaboration.settings, "openai_compatible_model", "test-model")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        content = {"reply": "先建立世界规则。", "suggestion": {"target_document_id": None, "title": "潮汐规则", "category": "world", "proposed_content": "每次潮汐逆转会消耗一段记忆。\n", "reason": "统一世界规则。"}}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(setting_collaboration, "OpenAICompatibleStructuredOutputTransport", lambda **kwargs: OpenAICompatibleStructuredOutputTransport(**kwargs, client=client))
        reply, provenance = await setting_collaboration.generate_setting_collection_collaboration("从零构建设定集", {"scope": "collection", "agent_role": "lore_agent", "documents": []}, [])
    assert reply.suggestion.target_document_id is None
    assert reply.suggestion.title == "潮汐规则"
    assert reply.suggestion.proposed_content.endswith("\n")
    assert "整个设定集" in requests[0]["messages"][0]["content"]
    assert "空设定集" in requests[0]["messages"][0]["content"]
    assert "不是全局助手 Gura" in requests[0]["messages"][0]["content"]
    assert json.loads(requests[0]["messages"][-1]["content"])["context"]["documents"] == []
    assert provenance["prompt_template_version"] == "setting-collection-collaboration-v1"
