"""Safe adapter for OpenAI-compatible chat-completions services."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from app.llm.contracts import (
    MAX_PROVENANCE_TOKEN_COUNT,
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    validate_chapter_generation_output,
)
from app.llm.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@dataclass
class OpenAICompatibleChapterGenerationProvider:
    """Generate chapters without retaining prompts, responses, or credentials."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 30.0
    client: httpx.AsyncClient | None = field(default=None, repr=False)

    SYSTEM_PROMPT = (
        "Generate chapter artifacts. Return a JSON object exactly with the string keys "
        "outline, draft, and summary. Do not include any other keys or prose."
    )

    def __post_init__(self) -> None:
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=f"{self.base_url.rstrip('/')}/", timeout=self.timeout_seconds
            )

    async def aclose(self) -> None:
        """Close only the client this provider created itself."""
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Project: {request.project_title}\nChapter: {request.chapter_number}\n"
                        f"Title: {request.title or 'Untitled Chapter'}"
                    ),
                },
            ],
        }
        try:
            response = await self.client.post(  # type: ignore[union-attr]
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError() from error
        except httpx.RequestError as error:
            raise ProviderUnavailableError() from error
        if response.status_code == 429:
            raise ProviderRateLimitedError()
        if not response.is_success:
            raise ProviderUnavailableError()
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content must be text")
            result = validate_chapter_generation_output(json.loads(content))
        except ProviderInvalidOutputError:
            raise
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderInvalidOutputError() from error
        usage = body.get("usage") if isinstance(body, dict) else None
        input_tokens = self._bounded_usage(usage, "prompt_tokens")
        output_tokens = self._bounded_usage(usage, "completion_tokens")
        return ChapterGenerationResponse(result, input_tokens, output_tokens)

    @staticmethod
    def _bounded_usage(usage: object, name: str) -> int | None:
        value = usage.get(name) if isinstance(usage, dict) else None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if 0 <= value <= MAX_PROVENANCE_TOKEN_COUNT else None
