import dataclasses

import pytest

from app.llm import (
    ChapterGenerationProvider,
    ChapterGenerationRequest,
    ChapterGenerationResult,
    FakeChapterGenerationProvider,
)
from app.production.fake_generator import FakeChapterGenerator


@pytest.mark.anyio
async def test_fake_provider_implements_contract_with_fixture_equivalent_artifacts() -> None:
    request = ChapterGenerationRequest(
        project_title="The Glass Archive", chapter_number=7, title="The Locked Door"
    )

    provider = FakeChapterGenerationProvider()
    result = await provider.generate(request)
    expected = FakeChapterGenerator().generate("The Glass Archive", 7, "The Locked Door")

    assert isinstance(provider, ChapterGenerationProvider)
    assert isinstance(result, ChapterGenerationResult)
    assert result.outline.encode("utf-8") == expected.outline.encode("utf-8")
    assert result.draft.encode("utf-8") == expected.draft.encode("utf-8")
    assert result.summary.encode("utf-8") == expected.summary.encode("utf-8")


@pytest.mark.parametrize("chapter_number", [0, -1, 1.5, True, "1", None])
def test_chapter_generation_request_rejects_non_positive_integer_chapter_numbers(
    chapter_number: object,
) -> None:
    with pytest.raises(ValueError, match="chapter_number must be a positive integer"):
        ChapterGenerationRequest("The Glass Archive", chapter_number, "A Title")  # type: ignore[arg-type]


def test_chapter_generation_request_and_result_are_immutable() -> None:
    request = ChapterGenerationRequest("The Glass Archive", 7, "The Locked Door")
    result = ChapterGenerationResult(outline="outline", draft="draft", summary="summary")

    assert dataclasses.is_dataclass(request)
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.title = "Changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.draft = "Changed"  # type: ignore[misc]
