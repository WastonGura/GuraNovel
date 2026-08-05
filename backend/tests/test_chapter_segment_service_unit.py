"""Unit tests for chapter segment service authority and error handling."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.chapter_segments import ChapterSegmentError
from app.models import Document, DocumentType, DocumentVersion, Project
from app.services.document_service import (
    ChapterSegmentSnapshotMismatchError,
    DocumentService,
)
from app.workspace.hashing import sha256_content


class _ScalarSession:
    def __init__(self, *results: object) -> None:
        self.results: Iterator[object] = iter(results)

    async def scalar(self, _statement: object) -> object:
        return next(self.results)


class _SnapshotStore:
    def __init__(self, content: str | None = None, *, error: Exception | None = None) -> None:
        self.content = content
        self.error = error

    def read_bounded(self, _path: str, *, max_bytes: int) -> str:
        if self.error is not None:
            raise self.error
        assert self.content is not None
        assert max_bytes == 2 * 1024 * 1024
        return self.content


def bound_models(content: str) -> tuple[Project, Document, DocumentVersion]:
    project = Project(slug="segment-unit", title="Unit", workspace_root=str(Path("workspace")))
    project.id = uuid4()
    document = Document(
        id=uuid4(),
        project_id=project.id,
        chapter_id=uuid4(),
        type=DocumentType.CHAPTER_DRAFT.value,
        path="drafts/chapter.md",
    )
    document.project = project
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    version = DocumentVersion(
        id=uuid4(),
        document_id=document.id,
        version_number=1,
        source="user",
        content_hash=sha256_content(normalized),
        byte_size=len(normalized.encode()),
        word_count=2,
        file_path=document.path,
        snapshot_path=f".versions/{document.id}/v0001.md",
    )
    return project, document, version


@pytest.mark.anyio
async def test_service_hashes_and_segments_the_same_normalized_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "# One\r\n\r\nCanary text."
    project, document, version = bound_models(content)
    session = cast(AsyncSession, _ScalarSession(document, version))
    monkeypatch.setattr(
        DocumentService,
        "_store_for",
        staticmethod(lambda _document: _SnapshotStore(content)),
    )

    result = await DocumentService(session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=document.chapter_id,
        document_id=document.id,
        version_id=version.id,
    )

    assert result.content_hash == version.content_hash
    assert result.byte_size == version.byte_size
    assert [item.content for item in result.segments] == ["# One", "Canary text."]


@pytest.mark.anyio
async def test_service_rederives_authoritative_map_before_accepting_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "# One\n\nEvidence."
    project, document, version = bound_models(content)
    monkeypatch.setattr(
        DocumentService,
        "_store_for",
        staticmethod(lambda _document: _SnapshotStore(content)),
    )
    initial = await DocumentService(
        cast(AsyncSession, _ScalarSession(document, version))
    ).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=document.chapter_id,
        document_id=document.id,
        version_id=version.id,
    )

    validated = await DocumentService(
        cast(AsyncSession, _ScalarSession(document, version))
    ).validate_chapter_evidence_segment_ids(
        project_id=project.id,
        chapter_id=document.chapter_id,
        document_id=document.id,
        version_id=version.id,
        segmenter_version="markdown-v1",
        segment_ids=(initial.segments[1].segment_id,),
    )

    assert validated == (initial.segments[1].segment_id,)
    with pytest.raises(ChapterSegmentError, match="invalid_evidence_segments"):
        await DocumentService(
            cast(AsyncSession, _ScalarSession(document, version))
        ).validate_chapter_evidence_segment_ids(
            project_id=project.id,
            chapter_id=document.chapter_id,
            document_id=document.id,
            version_id=version.id,
            segmenter_version="markdown-v1",
            segment_ids=(uuid4(),),
        )


@pytest.mark.anyio
async def test_service_rejects_oversized_version_metadata_before_snapshot_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, document, version = bound_models("Text.")
    version.byte_size = 2 * 1024 * 1024 + 1
    session = cast(AsyncSession, _ScalarSession(document, version))

    def fail_store(_document: Document) -> _SnapshotStore:
        raise AssertionError("snapshot store must not be opened")

    monkeypatch.setattr(DocumentService, "_store_for", staticmethod(fail_store))

    with pytest.raises(ChapterSegmentSnapshotMismatchError):
        await DocumentService(session).derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=document.chapter_id,
            document_id=document.id,
            version_id=version.id,
        )


@pytest.mark.anyio
async def test_service_sanitizes_snapshot_path_errors_and_does_not_log_content_or_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    project, document, version = bound_models("TOP-SECRET-CANARY")
    session = cast(AsyncSession, _ScalarSession(document, version))
    monkeypatch.setattr(
        DocumentService,
        "_store_for",
        staticmethod(
            lambda _document: _SnapshotStore(error=OSError("D:\\sensitive\\TOP-SECRET-CANARY.md"))
        ),
    )

    with pytest.raises(ChapterSegmentSnapshotMismatchError) as error:
        await DocumentService(session).derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=document.chapter_id,
            document_id=document.id,
            version_id=version.id,
        )

    assert str(error.value) == "The chapter snapshot could not be verified."
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    output = caplog.text + repr(error.value) + str(error.value)
    assert "TOP-SECRET-CANARY" not in output
    assert "sensitive" not in output
