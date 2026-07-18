import json

import httpx
import pytest

from app.llm import (
    ChapterGenerationRequest,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.openai_compatible_provider import OpenAICompatibleChapterGenerationProvider


def request() -> ChapterGenerationRequest:
    return ChapterGenerationRequest("Archive of Ash", 3, "The Locked Door")


@pytest.mark.anyio
async def test_posts_server_owned_prompt_and_returns_validated_artifacts_without_leaking_key() -> None:
    captured: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["url"] = str(http_request.url)
        captured["headers"] = dict(http_request.headers)
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"outline":"o","draft":"d","summary":"s"}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 11},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://provider.test/v1/")
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://provider.test/v1", api_key="test-secret-key", model="server-model-v1", client=client
    )
    try:
        response = await provider.generate(request())
    finally:
        await client.aclose()

    assert response.result.outline == "o"
    assert response.input_tokens == 7
    assert response.output_tokens == 11
    assert captured["url"] == "https://provider.test/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-secret-key"  # type: ignore[index]
    assert captured["body"] == {
        "model": "server-model-v1",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": OpenAICompatibleChapterGenerationProvider.SYSTEM_PROMPT},
            {"role": "user", "content": "Project: Archive of Ash\nChapter: 3\nTitle: The Locked Door"},
        ],
    }
    assert "test-secret-key" not in repr(provider)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(429, text="secret upstream detail"), ProviderRateLimitedError),
        (httpx.Response(500, text="secret upstream detail"), ProviderUnavailableError),
    ],
)
async def test_maps_safe_http_errors(response: httpx.Response, error_type: type[Exception]) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://local.test", api_key="secret", model="model", client=client
    )
    with pytest.raises(error_type) as error:
        await provider.generate(request())
    await client.aclose()
    assert "secret" not in str(error.value)


@pytest.mark.anyio
async def test_maps_timeout_and_malformed_upstream_payload_to_safe_errors() -> None:
    timeout_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("opaque")))
    )
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://local.test", api_key="secret", model="model", client=timeout_client
    )
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(request())
    await timeout_client.aclose()

    malformed_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": []}))
    )
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://local.test", api_key="secret", model="model", client=malformed_client
    )
    with pytest.raises(ProviderInvalidOutputError):
        await provider.generate(request())
    await malformed_client.aclose()
