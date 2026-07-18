"""Provider-neutral chapter generation value objects and boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResult:
        """Return generated chapter artifacts or raise a documented provider failure."""
