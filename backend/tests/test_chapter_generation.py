import dataclasses

import pytest

from app.llm import (
    ChapterGenerationProvider,
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    ChapterGenerationResult,
    ChapterGenerationProvenance,
    FakeChapterGenerationProvider,
    ProviderInvalidOutputError,
    validate_chapter_generation_response,
    validate_chapter_generation_output,
)
from app.production.fake_generator import FakeChapterGenerator


@pytest.mark.anyio
async def test_fake_provider_implements_contract_with_fixture_equivalent_artifacts() -> None:
    request = ChapterGenerationRequest(
        project_title="The Glass Archive", chapter_number=7, title="The Locked Door"
    )

    provider = FakeChapterGenerationProvider()
    response = await provider.generate(request)
    expected = FakeChapterGenerator().generate("The Glass Archive", 7, "The Locked Door")

    assert isinstance(provider, ChapterGenerationProvider)
    assert isinstance(response, ChapterGenerationResponse)
    assert isinstance(response.result, ChapterGenerationResult)
    assert response.result.outline.encode("utf-8") == expected.outline.encode("utf-8")
    assert response.result.draft.encode("utf-8") == expected.draft.encode("utf-8")
    assert response.result.summary.encode("utf-8") == expected.summary.encode("utf-8")
    assert response.input_tokens is None
    assert response.output_tokens is None


@pytest.mark.parametrize("chapter_number", [0, -1, 1.5, True, "1", None])
def test_chapter_generation_request_rejects_non_positive_integer_chapter_numbers(
    chapter_number: object,
) -> None:
    with pytest.raises(ValueError, match="chapter_number must be a positive integer"):
        ChapterGenerationRequest("The Glass Archive", chapter_number, "A Title")  # type: ignore[arg-type]


def test_chapter_generation_request_and_result_are_immutable() -> None:
    request = ChapterGenerationRequest("The Glass Archive", 7, "The Locked Door")
    result = ChapterGenerationResult(outline="outline", draft="draft", summary="summary")
    provenance = ChapterGenerationProvenance("fake", "deterministic-fake-v1", "v1")

    assert dataclasses.is_dataclass(request)
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.title = "Changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.draft = "Changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        provenance.model_identifier = "Changed"  # type: ignore[misc]


def test_raw_chapter_output_conversion_returns_typed_artifacts() -> None:
    result = validate_chapter_generation_output(
        {"outline": "outline", "draft": "draft", "summary": "summary"}
    )

    assert result == ChapterGenerationResult("outline", "draft", "summary")


@pytest.mark.parametrize(
    "raw_output",
    [
        None,
        [],
        {"outline": "outline", "draft": "draft"},
        {"outline": "outline", "draft": "draft", "summary": "summary", "extra": "no"},
        {"outline": 1, "draft": "draft", "summary": "summary"},
        {"outline": "", "draft": "draft", "summary": "summary"},
        {"outline": "outline", "draft": "", "summary": "summary"},
        {"outline": "outline", "draft": "draft", "summary": ""},
    ],
)
def test_raw_chapter_output_conversion_normalizes_all_invalid_payloads(
    raw_output: object,
) -> None:
    with pytest.raises(ProviderInvalidOutputError) as error:
        validate_chapter_generation_output(raw_output)

    assert error.value.code == "provider_invalid_output"
    assert error.value.message == "The generation provider returned invalid output."


@pytest.mark.parametrize("args", [
    ("", "model", "v1"), ("fake", "", "v1"), ("fake", "model", ""),
    ("fake", "https://model.example", "v1"),
    ("fake prompt: write a chapter", "model", "v1"),
    ("fake", "Authorization: Bearer secret", "v1"),
    ("fake", "api_key=super-secret", "v1"),
    ("fake", "model.example/path", "v1"), ("fake", "a" * 129, "v1"),
])
def test_server_owned_provenance_rejects_invalid_identifiers(args: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        ChapterGenerationProvenance(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize("counter", [-1, True, 1_000_000_001, "12"])
def test_response_validation_rejects_unbounded_or_non_integer_counters(counter: object) -> None:
    response = ChapterGenerationResponse(
        result=ChapterGenerationResult("outline", "draft", "summary"), output_tokens=0
    )
    object.__setattr__(response, "input_tokens", counter)

    with pytest.raises(ProviderInvalidOutputError):
        validate_chapter_generation_response(response)


def test_response_has_no_provenance_field_and_ignores_injected_one() -> None:
    response = ChapterGenerationResponse(ChapterGenerationResult("outline", "draft", "summary"))
    object.__setattr__(response, "provenance", "sk-proj-opaque-provider-secret")

    validated = validate_chapter_generation_response(response)

    assert not hasattr(validated, "provenance")
