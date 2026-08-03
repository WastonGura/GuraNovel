"""Deterministic transports and adapters for local structured generation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Literal

from app.llm.contracts import (
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
)
from app.production.fake_generator import FakeChapterGenerator


@dataclass
class FakeStructuredOutputTransport:
    """Deterministic structured transport with explicit test-only failure injection."""

    payload: object = field(repr=False)
    input_tokens: object = field(default=None, repr=False)
    output_tokens: object = field(default=None, repr=False)
    failure: Literal["timeout", "rate_limit", "unavailable", "malformed"] | None = None
    requests: list[StructuredTransportRequest] = field(default_factory=list, init=False)

    async def call(self, request: StructuredTransportRequest) -> StructuredTransportResponse:
        self.requests.append(request)
        if self.failure == "timeout":
            raise ProviderTimeoutError()
        if self.failure == "rate_limit":
            raise ProviderRateLimitedError()
        if self.failure == "unavailable":
            raise ProviderUnavailableError()
        if self.failure == "malformed":
            raise ProviderInvalidOutputError()
        try:
            raw_payload = (
                self.payload
                if type(self.payload) in (str, bytes)
                else json.dumps(
                    self.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except Exception:
            raise ProviderInvalidOutputError() from None
        return StructuredTransportResponse(
            payload=raw_payload,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class FakeChapterGenerationProvider:
    """Adapt the existing synchronous fake generator to the async provider contract."""

    def __init__(self, generator: FakeChapterGenerator | None = None) -> None:
        self._generator = generator or FakeChapterGenerator()
        profile = chapter_generation_profile("fake", "deterministic-fake-v1")
        self.provenance = profile.provenance

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
        """Return byte-identical artifacts from the existing fake generator."""
        generated = self._generator.generate(
            request.project_title, request.chapter_number, request.title
        )
        profile = chapter_generation_profile("fake", "deterministic-fake-v1")
        transport = FakeStructuredOutputTransport(
            payload={
                "outline": generated.outline,
                "draft": generated.draft,
                "summary": generated.summary,
            }
        )
        adapter = GatewayChapterGenerationProvider(
            StructuredOutputGateway(profile, transport)
        )
        return await adapter.generate(request)
