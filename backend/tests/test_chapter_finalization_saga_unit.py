from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.chapter_finalization_saga import (
    _final_document_path,
    _final_operation_key,
    _valid_final_document_paths,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)
from app.workspace.paths import version_snapshot_path


def _state(document_version_id: str, review_policy_version: str) -> SimpleNamespace:
    return SimpleNamespace(
        document_version_id=document_version_id,
        review_policy_version=review_policy_version,
    )


def test_final_operation_key_is_deterministic_and_content_free() -> None:
    run_id = uuid4()
    run = SimpleNamespace(id=run_id)
    state = _state(str(uuid4()), "chapter-quality-v1")

    key = _final_operation_key(run, state)

    assert key == _final_operation_key(run, state)
    assert type(key) is str and len(key) == 64
    assert all(character in "0123456789abcdef" for character in key)
    assert "chapter" not in key and "final" not in key


def test_final_operation_key_rejects_stale_state_without_source_version() -> None:
    run = SimpleNamespace(id=uuid4())
    state = SimpleNamespace(
        document_version_id=None,
        review_policy_version="chapter-quality-v1",
    )

    with pytest.raises(ChapterProductionV2ValidationError):
        _final_operation_key(run, state)


def test_final_document_path_is_re_derived_from_chapter_and_run() -> None:
    run_id = uuid4()
    chapter = SimpleNamespace(chapter_number=7)
    run = SimpleNamespace(id=run_id)

    path = _final_document_path(chapter=chapter, run=run)

    assert path == f"chapters/chapter-0007-{run_id}-final.md"
    assert path == _final_document_path(chapter=chapter, run=run)


def test_valid_final_document_paths_accepts_exact_canonical_v1() -> None:
    document_id = uuid4()
    run_id = uuid4()
    chapter = SimpleNamespace(chapter_number=3)
    run = SimpleNamespace(id=run_id)
    document = SimpleNamespace(id=document_id, path=_final_document_path(chapter=chapter, run=run))
    version = SimpleNamespace(
        version_number=1,
        file_path=document.path,
        snapshot_path=version_snapshot_path(str(document_id), 1).as_posix(),
    )

    assert _valid_final_document_paths(
        chapter=chapter,
        run=run,
        document=document,
        version=version,
    )


@pytest.mark.parametrize(
    ("version_number", "file_path", "snapshot_path"),
    [
        (2, None, None),
        (True, None, None),
        (1, "chapters/foreign.md", None),
        (1, None, ".versions/other/v0001.md"),
    ],
)
def test_valid_final_document_paths_rejects_noncanonical_paths(
    version_number: object,
    file_path: str | None,
    snapshot_path: str | None,
) -> None:
    document_id = uuid4()
    run_id = uuid4()
    chapter = SimpleNamespace(chapter_number=3)
    run = SimpleNamespace(id=run_id)
    canonical = _final_document_path(chapter=chapter, run=run)
    document = SimpleNamespace(id=document_id, path=canonical)
    version = SimpleNamespace(
        version_number=version_number,
        file_path=file_path if file_path is not None else canonical,
        snapshot_path=(
            snapshot_path
            if snapshot_path is not None
            else version_snapshot_path(str(document_id), 1).as_posix()
        ),
    )

    assert not _valid_final_document_paths(
        chapter=chapter,
        run=run,
        document=document,
        version=version,
    )
