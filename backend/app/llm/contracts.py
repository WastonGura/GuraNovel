"""Provider-neutral chapter generation value objects and boundary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.errors import ProviderInvalidOutputError


# One billion is well above a chapter-generation request while preventing a provider
# from storing unbounded accounting values in the public workflow event stream.
MAX_PROVENANCE_TOKEN_COUNT = 1_000_000_000
_MACHINE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MODEL_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_MODEL_SENSITIVE_PREFIXES = ("sk-", "sk_")
_MODEL_SENSITIVE_MATERIAL_PATTERN = re.compile(
    r"(?:^|[-_:/])(?:api[-_]?key|apikey|authorization|bearer|token|secret|redacted)(?=$|[-_:])",
    re.IGNORECASE,
)


def validate_model_identifier(value: object) -> str:
    """Return a safe, bounded model identifier suitable for persisted provenance."""
    has_sensitive_prefix = isinstance(value, str) and value.lower().startswith(
        _MODEL_SENSITIVE_PREFIXES
    )
    if (
        not isinstance(value, str)
        or _MODEL_IDENTIFIER_PATTERN.fullmatch(value) is None
        or "://" in value
        or has_sensitive_prefix
        or _MODEL_SENSITIVE_MATERIAL_PATTERN.search(value) is not None
    ):
        raise ValueError("model identifier must be a safe bounded identifier")
    return value


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


@dataclass(frozen=True)
class ChapterGenerationProvenance:
    """Trusted, server-owned metadata retained for a generation run.

    This value is deliberately not part of ``ChapterGenerationResponse``.  Server
    composition selects it when constructing ``ChapterProductionService``; provider
    responses are untrusted and can supply only artifacts and integer accounting.
    """

    provider_kind: str
    model_identifier: str
    prompt_template_version: str

    def __post_init__(self) -> None:
        for value in (self.provider_kind, self.prompt_template_version):
            if (
                not isinstance(value, str)
                or _MACHINE_IDENTIFIER_PATTERN.fullmatch(value) is None
                or value.lower().startswith("sk-")
            ):
                raise ValueError("provenance text values must be bounded machine identifiers")
        validate_model_identifier(self.model_identifier)
    def to_payload(
        self, *, input_tokens: int | None = None, output_tokens: int | None = None
    ) -> dict[str, str | int]:
        """Return precisely the persistence-safe provenance fields."""
        payload: dict[str, str | int] = {
            "provider_kind": self.provider_kind,
            "model_identifier": self.model_identifier,
            "prompt_template_version": self.prompt_template_version,
        }
        if input_tokens is not None:
            payload["input_tokens"] = input_tokens
        if output_tokens is not None:
            payload["output_tokens"] = output_tokens
        return payload


@dataclass(frozen=True)
class ChapterGenerationResponse:
    """Untrusted provider response containing artifacts and bounded accounting only."""

    result: ChapterGenerationResult
    input_tokens: int | None = None
    output_tokens: int | None = None


def validate_chapter_generation_output(raw_payload: object) -> ChapterGenerationResult:
    """Convert arbitrary provider output into typed artifacts without exposing details."""
    try:
        output = RawChapterGenerationOutput.model_validate(raw_payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise ProviderInvalidOutputError() from error
    return ChapterGenerationResult(
        outline=output.outline,
        draft=output.draft,
        summary=output.summary,
    )


def validate_chapter_generation_response(raw_response: object) -> ChapterGenerationResponse:
    """Defend the persistence boundary against malformed injected providers."""
    try:
        if not isinstance(raw_response, ChapterGenerationResponse):
            raise TypeError("provider response must use the chapter response envelope")
        if not isinstance(raw_response.result, ChapterGenerationResult):
            raise TypeError("provider result must use the chapter result value object")
        result = validate_chapter_generation_output(
            {
                "outline": raw_response.result.outline,
                "draft": raw_response.result.draft,
                "summary": raw_response.result.summary,
            }
        )
        for value in (raw_response.input_tokens, raw_response.output_tokens):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_PROVENANCE_TOKEN_COUNT
            ):
                raise ValueError("provider token counters must be bounded integers")
    except ProviderInvalidOutputError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise ProviderInvalidOutputError() from error
    return ChapterGenerationResponse(
        result=result,
        input_tokens=raw_response.input_tokens,
        output_tokens=raw_response.output_tokens,
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
