from pathlib import Path
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.chapter_segments import ChapterSegmentError
from app.core.errors import NotFoundError
from app.models import Chapter, DocumentSource, DocumentType, Project
from app.services.document_service import (
    ChapterSegmentSnapshotMismatchError,
    DocumentService,
)


async def create_project_and_chapter(
    async_session: AsyncSession, workspace_root: Path, *, suffix: str
) -> tuple[Project, Chapter]:
    project = Project(
        slug=f"chapter-segments-{suffix}-{uuid4().hex[:8]}",
        title="Chapter Segments",
        workspace_root=str(workspace_root),
    )
    async_session.add(project)
    await async_session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1, title="One")
    async_session.add(chapter)
    await async_session.commit()
    return project, chapter


@pytest.mark.integration
@pytest.mark.anyio
async def test_derives_exact_historical_chapter_version_after_newer_version_exists(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(
        async_session, tmp_path, suffix="historical"
    )
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Chapter one",
        path="drafts/chapter-01.md",
        content="# One\r\n\r\nHistorical text.",
        source=DocumentSource.USER,
    )
    old_version = document.current_version
    assert old_version is not None
    first_map = await service.derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=document.id,
        version_id=old_version.id,
        segmenter_version="markdown-v1",
    )
    assert await service.validate_chapter_evidence_segment_ids(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=document.id,
        version_id=old_version.id,
        segmenter_version="markdown-v1",
        segment_ids=(first_map.segments[0].segment_id,),
    ) == (first_map.segments[0].segment_id,)
    await service.write_document(
        document_id=document.id,
        expected_current_version_id=old_version.id,
        content="# One\n\nNew text.",
        source=DocumentSource.WRITER_AGENT,
    )

    historical_map = await service.derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=document.id,
        version_id=old_version.id,
        segmenter_version="markdown-v1",
    )

    assert historical_map == first_map
    assert historical_map.version_id == old_version.id
    assert [item.content for item in historical_map.segments] == ["# One", "Historical text."]


@pytest.mark.integration
@pytest.mark.anyio
async def test_rejects_cross_scope_non_chapter_and_unknown_algorithm_bindings(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path, suffix="one")
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_project, other_chapter = await create_project_and_chapter(
        async_session, other_root, suffix="two"
    )
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_FINAL,
        title="Final",
        path="chapters/chapter-01.md",
        content="Final text.",
        source=DocumentSource.USER,
    )
    version = document.current_version
    assert version is not None

    for project_id, chapter_id, document_id, version_id in (
        (other_project.id, chapter.id, document.id, version.id),
        (project.id, other_chapter.id, document.id, version.id),
        (project.id, chapter.id, uuid4(), version.id),
        (project.id, chapter.id, document.id, uuid4()),
    ):
        with pytest.raises(NotFoundError):
            await service.derive_chapter_segment_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=document_id,
                version_id=version_id,
            )
        with pytest.raises(NotFoundError):
            await service.validate_chapter_evidence_segment_ids(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=document_id,
                version_id=version_id,
                segmenter_version="markdown-v1",
                segment_ids=(uuid4(),),
            )

    non_chapter = await service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.STYLE_GUIDE,
        title="Not chapter text",
        path="style.md",
        content="Style.",
        source=DocumentSource.USER,
    )
    non_chapter_version = non_chapter.current_version
    assert non_chapter_version is not None
    with pytest.raises(NotFoundError):
        await service.derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=non_chapter.id,
            version_id=non_chapter_version.id,
        )
    with pytest.raises(NotFoundError):
        await service.derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=document.id,
            version_id=non_chapter_version.id,
        )

    with pytest.raises(ChapterSegmentError, match="unknown_chapter_segmenter"):
        await service.derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=document.id,
            version_id=version.id,
            segmenter_version="markdown-v999",
        )


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("corrupt_field", ["content_hash", "byte_size"])
async def test_rejects_corrupt_historical_snapshot_metadata(
    async_session: AsyncSession, tmp_path: Path, corrupt_field: str
) -> None:
    project, chapter = await create_project_and_chapter(
        async_session, tmp_path, suffix=corrupt_field
    )
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Draft",
        path="drafts/chapter-01.md",
        content="Snapshot text.",
        source=DocumentSource.USER,
    )
    version = document.current_version
    assert version is not None
    setattr(version, corrupt_field, "0" * 64 if corrupt_field == "content_hash" else 999)
    await async_session.flush()

    with pytest.raises(ChapterSegmentSnapshotMismatchError) as error:
        await service.derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=document.id,
            version_id=version.id,
        )

    assert error.value.code == "chapter_segment_snapshot_mismatch"
    assert "Snapshot text" not in str(error.value)


@pytest.mark.integration
@pytest.mark.anyio
async def test_rejects_snapshot_file_tampering_without_leaking_content_or_path(
    async_session: AsyncSession, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path, suffix="tamper")
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_FINAL,
        title="Final",
        path="chapters/chapter-01.md",
        content="Original chapter text.",
        source=DocumentSource.USER,
    )
    version = document.current_version
    assert version is not None and version.snapshot_path is not None
    (tmp_path / version.snapshot_path).write_text("TAMPERED-CANARY", encoding="utf-8")

    with pytest.raises(ChapterSegmentSnapshotMismatchError) as error:
        await service.derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=document.id,
            version_id=version.id,
        )

    output = caplog.text + str(error.value) + repr(error.value)
    assert "TAMPERED-CANARY" not in output
    assert version.snapshot_path not in output


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("replacement", ["oversized", "fifo"])
async def test_bounded_snapshot_read_rejects_oversized_files_and_fifos(
    async_session: AsyncSession, tmp_path: Path, replacement: str
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path, suffix=replacement)
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Draft",
        path="drafts/chapter.md",
        content="Original.",
        source=DocumentSource.USER,
    )
    version = document.current_version
    assert version is not None and version.snapshot_path is not None
    snapshot = tmp_path / version.snapshot_path
    snapshot.unlink()
    if replacement == "oversized":
        snapshot.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    else:
        os.mkfifo(snapshot)

    with pytest.raises(ChapterSegmentSnapshotMismatchError):
        await service.derive_chapter_segment_map(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=document.id,
            version_id=version.id,
        )
