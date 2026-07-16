import os
import stat
from pathlib import Path

import pytest

from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import (
    UnsafeWorkspacePathError,
    resolve_workspace_path,
    snapshot_path,
    version_snapshot_path,
)
from app.workspace.hashing import sha256_content
from app.workspace.word_count import count_words


def test_write_read_and_exists_for_nested_markdown_path(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)

    store.write("drafts/chapter-01.md", "First line\r\nSecond line\rThird line")

    document = tmp_path / "drafts" / "chapter-01.md"
    assert document.read_bytes() == b"First line\nSecond line\nThird line"
    assert store.read("drafts/chapter-01.md") == "First line\nSecond line\nThird line"
    assert store.exists("drafts/chapter-01.md") is True
    assert list(document.parent.glob(".tmp-*")) == []


@pytest.mark.parametrize("relative_path", ["/tmp/document.md", "../document.md", "draft/../../x.md"])
def test_resolve_workspace_path_rejects_absolute_and_traversal_paths(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(UnsafeWorkspacePathError):
        resolve_workspace_path(tmp_path, relative_path)


@pytest.mark.parametrize("relative_path", [r"..\\document.md", r"draft\\..\\..\\x.md"])
def test_resolve_workspace_path_rejects_windows_style_traversal_paths(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(UnsafeWorkspacePathError):
        resolve_workspace_path(tmp_path, relative_path)


def test_write_rejects_path_escaping_workspace_before_creating_a_file(tmp_path: Path) -> None:
    store = MarkdownStore(tmp_path)
    outside_document = tmp_path.parent / "outside.md"

    with pytest.raises(UnsafeWorkspacePathError):
        store.write("../outside.md", "must not be written")

    assert outside_document.exists() is False


def test_failed_replacement_preserves_the_current_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MarkdownStore(tmp_path)
    store.write("chapter.md", "original")

    def fail_replace(source: str, destination: str, **_kwargs: object) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("app.workspace.markdown_store.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated replacement failure"):
        store.write("chapter.md", "replacement")

    assert store.read("chapter.md") == "original"
    assert list(tmp_path.glob(".tmp-*")) == []


def test_write_replaces_using_open_parent_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MarkdownStore(tmp_path)
    original_replace = os.replace
    replace_calls: list[tuple[int | None, int | None]] = []

    def track_replace(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        replace_calls.append((src_dir_fd, dst_dir_fd))
        original_replace(
            source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    monkeypatch.setattr("app.workspace.markdown_store.os.replace", track_replace)

    store.write("drafts/chapter.md", "safe")

    assert replace_calls
    assert all(source_fd is not None and destination_fd is not None for source_fd, destination_fd in replace_calls)
    assert (tmp_path / "drafts" / "chapter.md").read_text() == "safe"


def test_write_closes_temporary_descriptor_when_fdopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MarkdownStore(tmp_path)
    temporary_descriptor: int | None = None
    closed_descriptors: list[int] = []
    original_close = os.close

    def fail_fdopen(descriptor: int, *_args: object, **_kwargs: object) -> object:
        nonlocal temporary_descriptor
        temporary_descriptor = descriptor
        raise OSError("simulated fdopen failure")

    def track_close(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr("app.workspace.markdown_store.os.fdopen", fail_fdopen)
    monkeypatch.setattr("app.workspace.markdown_store.os.close", track_close)

    with pytest.raises(OSError, match="simulated fdopen failure"):
        store.write("chapter.md", "replacement")

    assert temporary_descriptor is not None
    assert temporary_descriptor in closed_descriptors


def test_write_fsyncs_containing_directory_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MarkdownStore(tmp_path)
    directory_fsyncs: list[int] = []
    original_fsync = os.fsync

    def track_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr("app.workspace.markdown_store.os.fsync", track_fsync)

    store.write("drafts/chapter.md", "durable")

    assert directory_fsyncs


def test_sha256_content_hash_is_deterministic() -> None:
    assert sha256_content("Gura\n") == "8c56dd52dd78b3b4722a9acbf201e24c108423d861b5b6114770bea0d76c875f"


def test_word_count_counts_whitespace_delimited_tokens() -> None:
    assert count_words("One, two.\n\nThree\tfour") == 4


def test_version_snapshot_path_uses_the_document_version_convention() -> None:
    assert version_snapshot_path("chapter-01", 1) == Path(".versions/chapter-01/v0001.md")


@pytest.mark.parametrize("suffix", ["../v0001", "v0001/extra", "v0001.md"])
def test_snapshot_path_rejects_unsafe_version_suffixes(suffix: str) -> None:
    with pytest.raises(UnsafeWorkspacePathError):
        snapshot_path("chapter-01", suffix)


def test_version_snapshot_path_rejects_non_integer_versions() -> None:
    with pytest.raises(ValueError):
        version_snapshot_path("chapter-01", "1")  # type: ignore[arg-type]
