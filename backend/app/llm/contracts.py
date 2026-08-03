"""Provider-neutral chapter generation value objects and boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.llm.errors import ProviderInvalidOutputError
from app.llm.gateway import (
    MAX_PROVENANCE_TOKEN_COUNT,
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputProvenance,
    StructuredOutputRequest,
    validate_model_identifier as _validate_model_identifier,
)


# One billion is well above a chapter-generation request while preventing a provider
# from storing unbounded accounting values in the public workflow event stream.
def validate_model_identifier(value: object) -> str:
    """Compatibility export for server-owned model identifier validation."""
    return _validate_model_identifier(value)


@dataclass(frozen=True)
class ChapterGenerationRequest:
    """The chapter context supplied to a generation provider."""

    project_title: str
    chapter_number: int
    title: str | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.chapter_number, bool)
            or not isinstance(self.chapter_number, int)
            or self.chapter_number < 1
        ):
            raise ValueError("chapter_number must be a positive integer")


@dataclass(frozen=True)
class ChapterGenerationResult:
    """The immutable chapter artifacts produced by a generation provider."""

    outline: str
    draft: str
    summary: str


class RawChapterGenerationOutput(BaseModel):
    """Strict schema for an untrusted provider's chapter artifact payload."""

    model_config = ConfigDict(strict=True, extra="forbid")

    outline: str = Field(min_length=1)
    draft: str = Field(min_length=1)
    summary: str = Field(min_length=1)


ChapterGenerationProvenance = StructuredOutputProvenance

CHAPTER_GENERATION_SYSTEM_PROMPT = (
    "Generate chapter artifacts. Return a JSON object exactly with the string keys "
    "outline, draft, and summary. Do not include any other keys or prose."
)


def chapter_generation_profile(
    provider_kind: str,
    model_identifier: str,
    *,
    timeout_seconds: float = 30.0,
) -> StructuredOutputProfile[RawChapterGenerationOutput]:
    """Build the server-owned chapter-generation structured-output profile."""
    return StructuredOutputProfile(
        profile_id="chapter_generation_v1",
        provider_kind=provider_kind,
        model_identifier=model_identifier,
        prompt_template_version="chapter-production-v1",
        system_prompt=CHAPTER_GENERATION_SYSTEM_PROMPT,
        output_schema_name="chapter_generation_output",
        output_schema=RawChapterGenerationOutput,
        timeout_seconds=timeout_seconds,
        max_input_chars=131_072,
        max_output_bytes=8_000_000,
    )


@dataclass(frozen=True)
class ChapterGenerationResponse:
    """Untrusted provider response containing artifacts and bounded accounting only."""

    result: ChapterGenerationResult = field(repr=False)
    input_tokens: int | None = field(default=None, repr=False)
    output_tokens: int | None = field(default=None, repr=False)


def validate_chapter_generation_output(raw_payload: object) -> ChapterGenerationResult:
    """Convert arbitrary provider output into typed artifacts without exposing details."""
    try:
        output = RawChapterGenerationOutput.model_validate(raw_payload)
    except Exception:
        output = None
    if output is None:
        raise ProviderInvalidOutputError() from None
    return ChapterGenerationResult(
        outline=output.outline,
        draft=output.draft,
        summary=output.summary,
    )


def validate_chapter_generation_response(raw_response: object) -> ChapterGenerationResponse:
    """Defend the persistence boundary against malformed injected providers."""
    validated: tuple[ChapterGenerationResult, int | None, int | None] | None = None
    try:
        if type(raw_response) is not ChapterGenerationResponse:
            raise TypeError("provider response must use the chapter response envelope")
        raw_result = raw_response.result
        if type(raw_result) is not ChapterGenerationResult:
            raise TypeError("provider result must use the chapter result value object")
        result = validate_chapter_generation_output(
            {
                "outline": raw_result.outline,
                "draft": raw_result.draft,
                "summary": raw_result.summary,
            }
        )
        input_tokens = raw_response.input_tokens
        output_tokens = raw_response.output_tokens
        for value in (input_tokens, output_tokens):
            if value is not None and (
                type(value) is not int
                or not 0 <= value <= MAX_PROVENANCE_TOKEN_COUNT
            ):
                raise ValueError("provider token counters must be bounded integers")
        validated = (result, input_tokens, output_tokens)
    except Exception:
        pass
    if validated is None:
        raise ProviderInvalidOutputError() from None
    result, input_tokens, output_tokens = validated
    return ChapterGenerationResponse(
        result=result,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class GatewayChapterGenerationProvider:
    """Capability-specific adapter over the reusable structured-output gateway."""

    __slots__ = ("__gateway",)

    def __init__(
        self, gateway: StructuredOutputGateway[RawChapterGenerationOutput]
    ) -> None:
        self.__gateway = gateway

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
        response = await self.__gateway.call(
            StructuredOutputRequest(
                profile_id=self.__gateway.profile_id,
                user_prompt=(
                    f"Project: {request.project_title}\nChapter: {request.chapter_number}\n"
                    f"Title: {request.title or 'Untitled Chapter'}"
                ),
            )
        )
        return ChapterGenerationResponse(
            result=ChapterGenerationResult(
                outline=response.result.outline,
                draft=response.result.draft,
                summary=response.result.summary,
            ),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )


@runtime_checkable
class ChapterGenerationProvider(Protocol):
    """Generate chapter artifacts from the supplied typed request.

    Implementations raise only the safe provider failures exported by ``app.llm``:
    ``ProviderUnavailableError`` for temporary service failures,
    ``ProviderTimeoutError`` for deadline expiry, ``ProviderRateLimitedError`` for
    provider throttling, and ``ProviderInvalidOutputError`` when returned artifacts
    cannot satisfy this contract. These exceptions never include upstream response
    text or details.
    """

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
        """Return generated chapter artifacts or raise a documented provider failure."""
