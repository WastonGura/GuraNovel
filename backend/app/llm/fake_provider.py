"""Deterministic provider adapter for local chapter generation."""

from app.llm.contracts import (
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    ChapterGenerationResult,
)
from app.production.fake_generator import FakeChapterGenerator


class FakeChapterGenerationProvider:
    """Adapt the existing synchronous fake generator to the async provider contract."""

    def __init__(self, generator: FakeChapterGenerator | None = None) -> None:
        self._generator = generator or FakeChapterGenerator()

    async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
        """Return byte-identical artifacts from the existing fake generator."""
        generated = self._generator.generate(
            request.project_title, request.chapter_number, request.title
        )
        return ChapterGenerationResponse(
            result=ChapterGenerationResult(
                outline=generated.outline,
                draft=generated.draft,
                summary=generated.summary,
            ),
        )
