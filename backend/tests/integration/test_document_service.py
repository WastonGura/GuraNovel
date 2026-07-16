from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import Document, DocumentSource, DocumentType, DocumentVersion, Project
from app.services.document_service import DocumentService
from app.workspace.hashing import sha256_content


async def create_project(async_session: AsyncSession, workspace_root: Path) -> Project:
    project = Project(
        slug=f"document-service-{workspace_root.name}",
        title="Document Service Test",
        workspace_root=str(workspace_root),
    )
    async_session.add(project)
    await async_session.commit()
    return project


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_document_persists_initial_version_and_workspace_files(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)

    document = await DocumentService(async_session).create_document(
        project_id=project.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Chapter one",
        path="drafts/chapter-01.md",
        content="# One\n\nHello world",
        source=DocumentSource.USER,
        change_summary="Initial draft",
    )

    version = document.current_version
    assert version is not None
    assert version.version_number == 1
    assert version.parent_version_id is None
    assert version.source == DocumentSource.USER.value
    assert version.content_hash == sha256_content("# One\n\nHello world")
    assert version.byte_size == len("# One\n\nHello world".encode("utf-8"))
    assert version.word_count == 4
    assert version.file_path == "drafts/chapter-01.md"
    assert version.snapshot_path == f".versions/{document.id}/v0001.md"
    assert (tmp_path / "drafts/chapter-01.md").read_text() == "# One\n\nHello world"
    assert (tmp_path / version.snapshot_path).read_text() == "# One\n\nHello world"

    persisted = await async_session.get(Document, document.id)
    assert persisted is not None
    assert persisted.current_version_id == version.id


@pytest.mark.integration
@pytest.mark.anyio
async def test_write_and_restore_append_versions_and_read_historical_content(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Chapter one",
        path="drafts/chapter-01.md",
        content="first draft",
        source=DocumentSource.USER,
    )
    first_version = document.current_version
    assert first_version is not None

    second_version = await service.write_document(
        document_id=document.id,
        content="second draft now",
        source=DocumentSource.WRITER_AGENT,
        expected_current_version_id=first_version.id,
        agent_role="writer",
        change_summary="Expanded draft",
    )

    assert second_version.version_number == 2
    assert second_version.parent_version_id == first_version.id
    assert await service.read_current_content(document.id) == "second draft now"
    assert await service.read_version_content(document.id, first_version.id) == "first draft"

    restored_version = await service.restore_document(
        document_id=document.id,
        version_id=first_version.id,
        source=DocumentSource.USER,
        expected_current_version_id=second_version.id,
        change_summary="Restored first draft",
    )

    assert restored_version.version_number == 3
    assert restored_version.parent_version_id == second_version.id
    assert await service.read_current_content(document.id) == "first draft"
    versions = (
        await async_session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number)
        )
    ).all()
    assert [version.version_number for version in versions] == [1, 2, 3]
    assert (tmp_path / ".versions" / str(document.id) / "v0002.md").read_text() == "second draft now"
    assert (tmp_path / ".versions" / str(document.id) / "v0003.md").read_text() == "first draft"


@pytest.mark.integration
@pytest.mark.anyio
async def test_stale_expected_version_raises_conflict_without_database_or_workspace_changes(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Chapter one",
        path="drafts/chapter-01.md",
        content="first draft",
        source=DocumentSource.USER,
    )
    first_version = document.current_version
    assert first_version is not None
    document_id = document.id
    await service.write_document(
        document_id=document.id,
        content="second draft",
        source=DocumentSource.WRITER_AGENT,
        expected_current_version_id=first_version.id,
    )

    with pytest.raises(ConflictError) as error:
        await service.write_document(
            document_id=document_id,
            content="stale overwrite",
            source=DocumentSource.USER,
            expected_current_version_id=first_version.id,
        )

    assert error.value.code == "document_version_conflict"
    assert await service.read_current_content(document.id) == "second draft"
    versions = (
        await async_session.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document.id)
        )
    ).all()
    assert len(versions) == 2
    assert not (tmp_path / ".versions" / str(document.id) / "v0003.md").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_failed_commit_compensates_new_workspace_files(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_project(async_session, tmp_path)
    service = DocumentService(async_session)

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(async_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.create_document(
            project_id=project.id,
            document_type=DocumentType.CHAPTER_DRAFT,
            title="Chapter one",
            path="drafts/chapter-01.md",
            content="first draft",
            source=DocumentSource.USER,
        )

    assert not (tmp_path / "drafts/chapter-01.md").exists()
    assert list(tmp_path.rglob("*.md")) == []
    assert await async_session.scalar(select(Document.id)) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_failed_flush_restores_the_previous_current_workspace_content(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_project(async_session, tmp_path)
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Chapter one",
        path="drafts/chapter-01.md",
        content="first draft",
        source=DocumentSource.USER,
    )
    first_version = document.current_version
    assert first_version is not None
    document_id = document.id

    async def fail_flush() -> None:
        raise RuntimeError("flush failed")

    monkeypatch.setattr(async_session, "flush", fail_flush)

    with pytest.raises(RuntimeError, match="flush failed"):
        await service.write_document(
            document_id=document_id,
            content="second draft",
            source=DocumentSource.WRITER_AGENT,
            expected_current_version_id=first_version.id,
        )

    assert (tmp_path / "drafts/chapter-01.md").read_text() == "first draft"
    assert not (tmp_path / ".versions" / str(document_id) / "v0002.md").exists()
    versions = (
        await async_session.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        )
    ).all()
    assert [version.version_number for version in versions] == [1]
