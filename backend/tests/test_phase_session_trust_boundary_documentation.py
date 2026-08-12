"""Executable architecture contract for constructed phase sessions."""

from pathlib import Path


ARCHITECTURE = (
    Path(__file__).resolve().parents[2] / "docs" / "architecture.md"
).read_text(encoding="utf-8")


def _section() -> str:
    heading = "### Constructed phase-session trust boundary"
    start = ARCHITECTURE.index(heading)
    end = ARCHITECTURE.index("### Typed node contracts", start)
    return ARCHITECTURE[start:end]


def _first_column(section: str) -> set[str]:
    return {
        line.split("|", 2)[1].strip().strip("`")
        for line in section.splitlines()
        if line.startswith("| `")
    }


def _text(section: str) -> str:
    return " ".join(section.split())


def test_trust_boundary_uses_construction_not_runtime_attestation() -> None:
    section = _section()
    text = _text(section)

    assert "construction, not attestation" in text
    assert _first_column(section) >= {
        "Trusted runtime integrity",
        "Untrusted application inputs",
    }
    assert "CPython builtins" in text
    assert "pinned SQLAlchemy package" in text
    assert "arbitrary post-import monkeypatching" in text.lower()
    assert "not an application authorization boundary" in text
    assert "restart or fail deployment health" in text
    for stopped in ("#174", "#176", "#178"):
        assert stopped in text


def test_owned_source_never_adopts_a_caller_session_or_factory() -> None:
    section = _section()
    text = _text(section)

    assert "`ChapterPhaseSessionSource`" in text
    assert "application composition root" in text
    assert "exact server-owned `AsyncEngine`" in text
    assert "private `async_sessionmaker`" in text
    assert "caller-supplied `AsyncSession`" in text
    assert "caller-supplied factory" in text
    assert "`AsyncConnection`" in text
    assert "per-entity bind" in text
    assert "does not derive authority from `session.bind`" in text
    assert "control flow, not a forgeable data token" in text
    assert "never adopted, closed, committed, rolled back, or expired" in text


def test_lifecycle_and_failure_ownership_are_explicit() -> None:
    section = _section()
    text = _text(section)

    assert _first_column(section) >= {
        "Composition",
        "Construction",
        "Fresh-state invariant",
        "Lease lifecycle",
        "Coordinator phase",
        "Provider handoff",
    }
    assert "new exact async and sync session identities" in text
    assert "no active transaction" in text
    assert "empty new, dirty, deleted, and identity-map state" in text
    assert "fixed content-free" in text
    assert "only the source-owned candidate" in text
    assert "CancelledError" in text
    assert "no session, orm instance, repository, or filesystem handle" in text.lower()


def test_implementation_split_is_one_way_bounded_and_compatible() -> None:
    section = _section()
    text = _text(section)
    lower = text.lower()

    assert text.index("**#181") < text.index("**#182") < text.index("**#177")
    assert "`chapter_phase_session_source.py`" in text
    assert "`chapter_phase_session_invariants.py`" in text
    assert "`chapter_phase_session_lease.py`" in text
    assert "source <- invariants <- lease <- facade/coordinators" in text
    assert "must not import back" in text
    assert "<=180 production lines" in text
    assert "<=160 production lines" in text
    assert "<=300 production lines" in text
    assert "existing public methods, DTOs, fixed exceptions, and export paths" in text
    assert "does not add a public HTTP API" in text
    for non_goal in (
        "provider attempts",
        "draft/revision behavior",
        "schema",
        "frontend",
        "langgraph",
    ):
        assert non_goal in lower
