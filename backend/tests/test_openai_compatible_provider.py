import gzip
import json
import logging

import httpx
import pytest

from app.llm import (
    ChapterGenerationRequest,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.gateway import StructuredTransportRequest
from app.llm.openai_compatible_provider import (
    OpenAICompatibleChapterGenerationProvider,
    OpenAICompatibleStructuredOutputTransport,
)


def request() -> ChapterGenerationRequest:
    return ChapterGenerationRequest("Archive of Ash", 3, "The Locked Door")


def transport_request(*, max_output_bytes: int = 1) -> StructuredTransportRequest:
    return StructuredTransportRequest(
        profile_id="test_v1",
        model_identifier="server-model-v1",
        output_schema_name="test_output",
        output_schema={"type": "object", "additionalProperties": False},
        max_output_bytes=max_output_bytes,
        timeout_seconds=30,
        system_prompt="private system prompt",
        user_prompt="private user prompt",
    )


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def raw_response(
    payload: bytes,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers=headers,
        stream=_CountingStream([payload]),
    )


@pytest.mark.anyio
async def test_owned_client_ignores_ambient_socks_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_url = "socks5://proxy-user:proxy-password@proxy.test:1080"
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALL_PROXY", proxy_url)

    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def allocate(**kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr("app.llm.openai_compatible_provider.httpx.AsyncClient", allocate)
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://provider.test/v1", api_key="test-secret-key", model="server-model-v1"
    )

    try:
        assert captured["trust_env"] is False
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("model", "timeout"),
    [("https://unsafe.example/model", 30), ("safe-model", 0), ("safe-model", 121)],
)
def test_invalid_profile_is_rejected_before_allocating_owned_client(
    monkeypatch: pytest.MonkeyPatch, model: str, timeout: float
) -> None:
    allocations = 0

    def allocate(**_: object) -> object:
        nonlocal allocations
        allocations += 1
        return object()

    monkeypatch.setattr("app.llm.openai_compatible_provider.httpx.AsyncClient", allocate)
    with pytest.raises(ValueError):
        OpenAICompatibleChapterGenerationProvider(
            base_url="https://provider.test/v1",
            api_key="private-key",
            model=model,
            timeout_seconds=timeout,
        )
    assert allocations == 0


@pytest.mark.anyio
async def test_provider_does_not_close_an_external_client() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://provider.test/v1",
        api_key="private-key",
        model="safe-model",
        client=client,
    )

    await provider.aclose()

    assert client.is_closed is False
    await client.aclose()


@pytest.mark.anyio
async def test_posts_server_owned_prompt_and_returns_validated_artifacts_without_leaking_key() -> None:
    captured: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["url"] = str(http_request.url)
        captured["headers"] = dict(http_request.headers)
        captured["body"] = json.loads(http_request.content)
        return raw_response(
            json.dumps(
                {
                "choices": [{"message": {"content": '{"outline":"o","draft":"d","summary":"s"}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 11},
                }
            ).encode(),
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
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "chapter_generation_output",
                "strict": True,
                "schema": {
                    "additionalProperties": False,
                    "description": (
                        "Strict schema for an untrusted provider's chapter artifact payload."
                    ),
                    "properties": {
                        "outline": {"minLength": 1, "title": "Outline", "type": "string"},
                        "draft": {"minLength": 1, "title": "Draft", "type": "string"},
                        "summary": {"minLength": 1, "title": "Summary", "type": "string"},
                    },
                    "required": ["outline", "draft", "summary"],
                    "title": "RawChapterGenerationOutput",
                    "type": "object",
                },
            },
        },
        "messages": [
            {"role": "system", "content": OpenAICompatibleChapterGenerationProvider.SYSTEM_PROMPT},
            {"role": "user", "content": "Project: Archive of Ash\nChapter: 3\nTitle: The Locked Door"},
        ],
    }
    assert "test-secret-key" not in repr(provider)
    assert "provider.test" not in repr(provider)
    for attribute in ("base_url", "api_key", "client", "profile", "transport"):
        assert not hasattr(provider, attribute)


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
        transport=httpx.MockTransport(
            lambda _: raw_response(json.dumps({"choices": []}).encode())
        )
    )
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://local.test", api_key="secret", model="model", client=malformed_client
    )
    with pytest.raises(ProviderInvalidOutputError):
        await provider.generate(request())
    await malformed_client.aclose()


@pytest.mark.anyio
async def test_transport_stops_raw_streaming_when_identity_envelope_exceeds_bound() -> None:
    stream = _CountingStream([b"x" * 40_000, b"y" * 30_000, b"must-not-be-read"])
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    )
    transport = OpenAICompatibleStructuredOutputTransport(
        base_url="https://provider.test/v1", api_key="secret", client=client
    )

    with pytest.raises(ProviderInvalidOutputError):
        await transport.call(transport_request())

    assert stream.yielded == 2
    assert stream.closed is True
    await client.aclose()


@pytest.mark.anyio
async def test_transport_allows_near_limit_escape_heavy_inner_raw_json() -> None:
    inner_payload = {"answer": ("\\\"\u2603" * 30_000)}
    inner_raw_json = json.dumps(
        inner_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    envelope = json.dumps(
        {"choices": [{"message": {"content": inner_raw_json}}]},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    assert len(envelope) > len(inner_raw_json.encode()) + 65_536
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: raw_response(envelope))
    )
    transport = OpenAICompatibleStructuredOutputTransport(
        base_url="https://provider.test/v1", api_key="secret", client=client
    )

    response = await transport.call(
        transport_request(max_output_bytes=len(inner_raw_json.encode()))
    )

    assert response.payload == inner_raw_json
    await client.aclose()


@pytest.mark.anyio
async def test_transport_rejects_non_identity_encoding_before_reading_body() -> None:
    compressed = gzip.compress(b"x" * 70_000)
    stream = _CountingStream([compressed])
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=stream
            )
        )
    )
    transport = OpenAICompatibleStructuredOutputTransport(
        base_url="https://provider.test/v1", api_key="secret", client=client
    )

    with pytest.raises(ProviderInvalidOutputError):
        await transport.call(transport_request())

    assert stream.yielded == 0
    assert stream.closed is True
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "envelope",
    [
        b'{"choices":[],"choices":[{"message":{"content":"{}"}}]}',
        b'{"choices":[{"message":{"content":"{}"}}],"usage":{"prompt_tokens":NaN}}',
        b'{"choices":[{"message":{"content":"{}"}}],"usage":{"prompt_tokens":Infinity}}',
        b'{"choices":[{"message":{"content":"{}"}}],"usage":{"prompt_tokens":1e10000}}',
    ],
)
async def test_transport_strictly_rejects_duplicate_and_nonfinite_envelope_json(
    envelope: bytes,
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: raw_response(envelope))
    )
    transport = OpenAICompatibleStructuredOutputTransport(
        base_url="https://provider.test/v1", api_key="secret", client=client
    )

    with pytest.raises(ProviderInvalidOutputError):
        await transport.call(transport_request())

    await client.aclose()


@pytest.mark.anyio
async def test_transport_hides_authority_and_httpx_info_logs_do_not_disclose_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    endpoint = "https://private-endpoint-marker.test/v1"
    api_key = "private-key-marker"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: raw_response(
                json.dumps(
                    {"choices": [{"message": {"content": "{}"}}]}
                ).encode()
            )
        )
    )
    transport = OpenAICompatibleStructuredOutputTransport(
        base_url=endpoint,
        api_key=api_key,
        client=client,
    )

    with caplog.at_level(logging.INFO, logger="httpx"):
        await transport.call(transport_request())

    assert endpoint not in caplog.text
    assert "private-endpoint-marker" not in caplog.text
    assert api_key not in caplog.text
    assert endpoint not in repr(transport)
    assert api_key not in repr(transport)
    for attribute in ("base_url", "api_key", "client"):
        assert not hasattr(transport, attribute)
    await client.aclose()


@pytest.mark.anyio
async def test_transport_never_inherits_external_client_redirect_authority() -> None:
    observed: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((str(request.url), request.content))
        if request.url.host == "first-provider.test":
            return httpx.Response(
                307,
                headers={"Location": "https://second-provider.test/collect"},
            )
        return raw_response(
            b'{"choices":[{"message":{"content":"{}"}}]}'
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    transport = OpenAICompatibleStructuredOutputTransport(
        base_url="https://first-provider.test/v1",
        api_key="private-key",
        client=client,
    )

    with pytest.raises((ProviderUnavailableError, ProviderInvalidOutputError)):
        await transport.call(transport_request())

    assert len(observed) == 1
    assert observed[0][0] == "https://first-provider.test/v1/chat/completions"
    assert b"private user prompt" in observed[0][1]
    assert all("second-provider.test" not in url for url, _ in observed)
    await client.aclose()


def test_transport_internals_are_not_exported_from_public_llm_package() -> None:
    import app.llm as llm

    for name in (
        "FakeStructuredOutputTransport",
        "OpenAICompatibleStructuredOutputTransport",
        "StructuredOutputTransport",
        "StructuredTransportRequest",
        "StructuredTransportResponse",
    ):
        assert not hasattr(llm, name)
