"""Safe OpenAI-compatible transport and chapter adapter."""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.llm.contracts import (
    CHAPTER_GENERATION_SYSTEM_PROMPT,
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    GatewayChapterGenerationProvider,
    chapter_generation_profile,
)
from app.llm.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.gateway import (
    StructuredOutputGateway,
    StructuredTransportRequest,
    StructuredTransportResponse,
    _decode_strict_json,
)


_MAX_STRUCTURED_ENVELOPE_OVERHEAD_BYTES = 65_536
_MAX_JSON_STRING_ESCAPE_EXPANSION = 6


class _MinimumWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


for _logger_name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_logger_name)
    if _logger.level < logging.WARNING:
        _logger.setLevel(logging.WARNING)
    if not any(isinstance(item, _MinimumWarningFilter) for item in _logger.filters):
        _logger.addFilter(_MinimumWarningFilter())


class OpenAICompatibleStructuredOutputTransport:
    """Perform only HTTP transport; profile authority remains in the gateway."""

    __slots__ = (
        "__api_key",
        "__base_url",
        "__client",
        "__owns_client",
        "__supports_json_schema",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.__base_url = base_url.rstrip("/")
        self.__api_key = api_key
        self.__owns_client = client is None
        self.__client = client
        self.__supports_json_schema: bool | None = None
        if self.__client is None:
            self.__client = httpx.AsyncClient(
                base_url=f"{self.__base_url}/",
                timeout=timeout_seconds,
                trust_env=False,
            )

    async def aclose(self) -> None:
        if self.__owns_client:
            await self.__client.aclose()

    async def call(self, request: StructuredTransportRequest) -> StructuredTransportResponse:
        envelope: bytes | None = None
        use_json_schema = self.__supports_json_schema is not False

        for attempt in range(2):
            if use_json_schema:
                payload = {
                    "model": request.model_identifier,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": request.output_schema_name,
                            "strict": True,
                            "schema": request.output_schema,
                        },
                    },
                    "messages": [
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                }
            else:
                schema_str = json.dumps(request.output_schema, ensure_ascii=False)
                sys_prompt = (
                    f"{request.system_prompt}\n\n"
                    f"You MUST format your entire response as a valid JSON object strictly matching this schema:\n"
                    f"```json\n{schema_str}\n```\n"
                    f"Do not include markdown code block markers or any commentary outside the JSON."
                )
                payload = {
                    "model": request.model_identifier,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                }
            if request.temperature is not None:
                payload["temperature"] = request.temperature
            if request.top_p is not None:
                payload["top_p"] = request.top_p
            if request.max_tokens is not None:
                payload["max_tokens"] = request.max_tokens

            transport_error: Exception | None = None
            retry_fallback = False
            try:
                async with self.__client.stream(
                    "POST",
                    f"{self.__base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.__api_key}"},
                    json=payload,
                    timeout=request.timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code == 429:
                        transport_error = ProviderRateLimitedError()
                    elif response.status_code == 400 and use_json_schema and attempt == 0:
                        retry_fallback = True
                    elif not response.is_success:
                        transport_error = ProviderUnavailableError()
                    elif response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                        transport_error = ProviderInvalidOutputError()
                    else:
                        limit = (
                            _MAX_JSON_STRING_ESCAPE_EXPANSION * request.max_output_bytes
                            + _MAX_STRUCTURED_ENVELOPE_OVERHEAD_BYTES
                        )
                        collected = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(collected) + len(chunk) > limit:
                                transport_error = ProviderInvalidOutputError()
                                break
                            collected.extend(chunk)
                        if transport_error is None:
                            envelope = bytes(collected)
            except httpx.TimeoutException:
                transport_error = ProviderTimeoutError()
            except httpx.RequestError:
                transport_error = ProviderUnavailableError()
            except Exception:
                transport_error = ProviderUnavailableError()

            if retry_fallback:
                self.__supports_json_schema = False
                use_json_schema = False
                continue

            if transport_error is not None:
                raise transport_error from None

            if use_json_schema and self.__supports_json_schema is None:
                self.__supports_json_schema = True
            break

        invalid_response = False
        try:
            if envelope is None:
                raise TypeError("provider response envelope is missing")
            body = _decode_strict_json(envelope)
            if not isinstance(body, dict):
                raise TypeError("provider response envelope must be an object")
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content must be text")
            # If the model wrapped JSON in markdown code blocks like ```json ... ```, strip it
            content = content.strip()
            if "<think>" in content and "</think>" in content:
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.rfind("```")
                if end > start:
                    content = content[start:end].strip()
            elif content.startswith("```"):
                lines = content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
            usage = body.get("usage") if isinstance(body, dict) else None
        except Exception:
            invalid_response = True
        if invalid_response:
            raise ProviderInvalidOutputError() from None
        return StructuredTransportResponse(
            payload=content,
            input_tokens=usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            output_tokens=(
                usage.get("completion_tokens") if isinstance(usage, dict) else None
            ),
        )


class OpenAICompatibleChapterGenerationProvider:
    """Backward-compatible chapter capability implemented through the shared gateway."""

    SYSTEM_PROMPT = CHAPTER_GENERATION_SYSTEM_PROMPT
    __slots__ = ("__adapter", "__transport", "model", "provenance")

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        profile = chapter_generation_profile(
            "openai_compatible", model, timeout_seconds=timeout_seconds
        )
        self.provenance = profile.provenance
        self.__transport = OpenAICompatibleStructuredOutputTransport(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self.__adapter = GatewayChapterGenerationProvider(
            StructuredOutputGateway(profile, self.__transport)
        )

    async def aclose(self) -> None:
        await self.__transport.aclose()

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
        return await self.__adapter.generate(request)
