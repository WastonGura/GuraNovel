"""Deterministic chapter fixtures for local production flows."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeChapterResult:
    """Generated chapter artifacts."""

    outline: str
    draft: str
    summary: str


class FakeChapterGenerator:
    """Create useful, repeatable chapter fixture content without external dependencies."""

    def generate(
        self, project_title: str, chapter_number: int, title: str | None = None
    ) -> FakeChapterResult:
        """Return the outline, draft, and summary for one chapter."""
        if isinstance(chapter_number, bool) or not isinstance(chapter_number, int) or chapter_number < 1:
            raise ValueError("chapter_number must be a positive integer")

        chapter_title = title if title is not None else "Untitled Chapter"
        context = f"Project: {project_title}"

        outline = (
            f"# Chapter {chapter_number} Outline: {chapter_title}\n\n"
            f"{context}\n\n"
            "- Mira follows the archive's new clue to a locked door.\n"
            "- The door forces a choice between preserving the record and protecting a friend.\n"
            "- Mira leaves with a lead that raises the stakes for the next chapter.\n"
        )
        draft = (
            f"# Chapter {chapter_number}: {chapter_title}\n\n"
            f"{context}\n\n"
            "Mira stopped at the locked door beneath the archive, holding the clue she had "
            "promised not to follow. The brass plate offered no name, only a keyhole shaped "
            "like a closed eye.\n\n"
            "When the alarm bell sounded upstairs, she had one decision: preserve the record "
            "inside or leave in time to protect her friend. Mira chose the friend, but copied "
            "the door's inscription before she ran.\n\n"
            "The copied words pointed to a second archive across the river—and to someone who "
            "already knew she was looking.\n"
        )
        summary = (
            f"In Chapter {chapter_number}, {chapter_title}, Mira follows an archive clue to a "
            "locked door. She chooses to protect a friend over opening it, but keeps an "
            "inscription that points to a second archive and a new threat."
        )

        return FakeChapterResult(outline=outline, draft=draft, summary=summary)
