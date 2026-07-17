import dataclasses

import pytest

from app.production.fake_generator import FakeChapterGenerator, FakeChapterResult


def test_generate_returns_frozen_typed_chapter_artifacts() -> None:
    result = FakeChapterGenerator().generate("The Glass Archive", 7, "The Locked Door")

    assert isinstance(result, FakeChapterResult)
    assert dataclasses.is_dataclass(result)
    assert result.outline == (
        "# Chapter 7 Outline: The Locked Door\n\n"
        "Project: The Glass Archive\n\n"
        "- Mira follows the archive's new clue to a locked door.\n"
        "- The door forces a choice between preserving the record and protecting a friend.\n"
        "- Mira leaves with a lead that raises the stakes for the next chapter.\n"
    )
    assert result.draft == (
        "# Chapter 7: The Locked Door\n\n"
        "Project: The Glass Archive\n\n"
        "Mira stopped at the locked door beneath the archive, holding the clue she had "
        "promised not to follow. The brass plate offered no name, only a keyhole shaped "
        "like a closed eye.\n\n"
        "When the alarm bell sounded upstairs, she had one decision: preserve the record "
        "inside or leave in time to protect her friend. Mira chose the friend, but copied "
        "the door's inscription before she ran.\n\n"
        "The copied words pointed to a second archive across the river—and to someone who "
        "already knew she was looking.\n"
    )
    assert result.summary == (
        "In Chapter 7, The Locked Door, Mira follows an archive clue to a locked door. "
        "She chooses to protect a friend over opening it, but keeps an inscription that "
        "points to a second archive and a new threat."
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outline = "changed"  # type: ignore[misc]


def test_generate_is_byte_identical_for_identical_values() -> None:
    generator = FakeChapterGenerator()

    first = generator.generate("The Glass Archive", 7, "The Locked Door")
    second = generator.generate("The Glass Archive", 7, "The Locked Door")

    assert first == second
    assert first.outline.encode("utf-8") == second.outline.encode("utf-8")
    assert first.draft.encode("utf-8") == second.draft.encode("utf-8")
    assert first.summary.encode("utf-8") == second.summary.encode("utf-8")


def test_generate_uses_a_stable_default_title_when_omitted() -> None:
    result = FakeChapterGenerator().generate("The Glass Archive", 8)

    assert result.outline == (
        "# Chapter 8 Outline: Untitled Chapter\n\n"
        "Project: The Glass Archive\n\n"
        "- Mira follows the archive's new clue to a locked door.\n"
        "- The door forces a choice between preserving the record and protecting a friend.\n"
        "- Mira leaves with a lead that raises the stakes for the next chapter.\n"
    )
    assert result.draft == (
        "# Chapter 8: Untitled Chapter\n\n"
        "Project: The Glass Archive\n\n"
        "Mira stopped at the locked door beneath the archive, holding the clue she had "
        "promised not to follow. The brass plate offered no name, only a keyhole shaped "
        "like a closed eye.\n\n"
        "When the alarm bell sounded upstairs, she had one decision: preserve the record "
        "inside or leave in time to protect her friend. Mira chose the friend, but copied "
        "the door's inscription before she ran.\n\n"
        "The copied words pointed to a second archive across the river—and to someone who "
        "already knew she was looking.\n"
    )
    assert result.summary == (
        "In Chapter 8, Untitled Chapter, Mira follows an archive clue to a locked door. "
        "She chooses to protect a friend over opening it, but keeps an inscription that "
        "points to a second archive and a new threat."
    )


@pytest.mark.parametrize("chapter_number", [0, -1, 1.5, True, "1", None])
def test_generate_rejects_non_positive_integer_chapter_numbers(
    chapter_number: object,
) -> None:
    with pytest.raises(ValueError, match="chapter_number must be a positive integer"):
        FakeChapterGenerator().generate("The Glass Archive", chapter_number, "A Title")  # type: ignore[arg-type]
