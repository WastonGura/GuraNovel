from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import ConflictError
from app.models import Document, DocumentSource, DocumentType, DocumentVersion, Project
from app.services.document_service import DocumentCommitIndeterminateError, DocumentService
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore


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
async def test_create_document_rejects_reserved_version_path_before_overwriting_snapshot(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path)
    service = DocumentService(async_session)
    document = await service.create_document(
        project_id=project.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Chapter one",
        path="drafts/chapter-01.md",
        content="original snapshot",
        source=DocumentSource.USER,
    )
    version = document.current_version
    assert version is not None

    with pytest.raises(ConflictError) as error:
        await service.create_document(
            project_id=project.id,
            document_type=DocumentType.CHAPTER_DRAFT,
            title="Malicious document",
            path=version.snapshot_path,
            content="malicious overwrite",
            source=DocumentSource.USER,
        )

    assert error.value.code == "reserved_document_path"
    assert (tmp_path / version.snapshot_path).read_text() == "original snapshot"
    assert (
        await async_session.scalars(select(Document).where(Document.project_id == project.id))
    ).all() == [document]


@pytest.mark.integration
@pytest.mark.anyio
async def test_same_path_creates_are_serialized_before_loser_can_write_files(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await create_project(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    winner_commit_entered = asyncio.Event()
    release_winner_commit = asyncio.Event()
    loser_write_started = asyncio.Event()
    original_write = MarkdownStore.write

    def observe_loser_write(self: MarkdownStore, path: str, content: str) -> None:
        if content == "loser content":
            loser_write_started.set()
        original_write(self, path, content)

    monkeypatch.setattr(MarkdownStore, "write", observe_loser_write)
    try:
        async with session_factory() as winner_session, session_factory() as loser_session:
            original_commit = winner_session.commit

            async def delayed_winner_commit() -> None:
                winner_commit_entered.set()
                await release_winner_commit.wait()
                await original_commit()

            monkeypatch.setattr(winner_session, "commit", delayed_winner_commit)
            winner = asyncio.create_task(
                DocumentService(winner_session).create_document(
                    project_id=project.id,
                    document_type=DocumentType.CHAPTER_DRAFT,
                    title="Winner",
                    path="drafts/chapter-01.md",
                    content="winner content",
                    source=DocumentSource.USER,
                )
            )
            await asyncio.wait_for(winner_commit_entered.wait(), timeout=2)
            loser = asyncio.create_task(
                DocumentService(loser_session).create_document(
                    project_id=project.id,
                    document_type=DocumentType.CHAPTER_DRAFT,
                    title="Loser",
                    path="drafts/chapter-01.md",
                    content="loser content",
                    source=DocumentSource.USER,
                )
            )

            await asyncio.sleep(0.1)
            assert not loser_write_started.is_set()
            assert (tmp_path / "drafts/chapter-01.md").read_text() == "winner content"

            release_winner_commit.set()
            winning_document = await winner
            with pytest.raises(ConflictError):
                await loser

        version = winning_document.current_version
        assert version is not None
        assert (tmp_path / "drafts/chapter-01.md").read_text() == "winner content"
        assert (tmp_path / version.snapshot_path).read_text() == "winner content"
        documents = (
            await async_session.scalars(select(Document).where(Document.project_id == project.id))
        ).all()
        assert [persisted.id for persisted in documents] == [winning_document.id]
    finally:
        await engine.dispose()


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
    assert (
        tmp_path / ".versions" / str(document.id) / "v0002.md"
    ).read_text() == "second draft now"
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
async def test_failed_commit_preserves_workspace_files_and_surfaces_indeterminate_outcome(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_project(async_session, tmp_path)
    service = DocumentService(async_session)

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(async_session, "commit", fail_commit)

    with pytest.raises(DocumentCommitIndeterminateError) as error:
        await service.create_document(
            project_id=project.id,
            document_type=DocumentType.CHAPTER_DRAFT,
            title="Chapter one",
            path="drafts/chapter-01.md",
            content="first draft",
            source=DocumentSource.USER,
        )

    assert error.value.code == "document_commit_indeterminate"
    assert (tmp_path / "drafts/chapter-01.md").read_text() == "first draft"
    snapshots = list((tmp_path / ".versions").rglob("*.md"))
    assert len(snapshots) == 1
    assert snapshots[0].read_text() == "first draft"


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
