"""PostgreSQL integration tests for setting collection separation and migration (Issue #281)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.errors import ConflictError
from app.models import (
    Chapter,
    Document,
    DocumentSource,
    DocumentVersion,
    Project,
    SettingCollection,
)
from app.services.document_service import DocumentService
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore

BACKEND_DIR = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def run_alembic(database_url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ
        | {
            "DATABASE_URL": database_url,
            "PGOPTIONS": "-c lock_timeout=5000 -c statement_timeout=15000",
        },
        check=True,
        timeout=60,
    )


async def terminate_other_connections(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
    finally:
        await engine.dispose()


async def test_migration_upgrade_downgrade_cycle(integration_database_url: str) -> None:
    """Verifies upgrade -> downgrade -> upgrade cycle is fully reversible without errors."""
    await terminate_other_connections(integration_database_url)
    run_alembic(integration_database_url, "downgrade", "0010_studio_assistant")
    run_alembic(integration_database_url, "upgrade", "head")
    run_alembic(integration_database_url, "downgrade", "0010_studio_assistant")
    run_alembic(integration_database_url, "upgrade", "head")


async def test_migration_backfills_projects_and_setting_documents(
    integration_database_url: str, tmp_path: Path
) -> None:
    """Verifies that running migration 0011 backfills setting collections and copies setting documents."""
    try:
        await terminate_other_connections(integration_database_url)
        run_alembic(integration_database_url, "downgrade", "0010_studio_assistant")

        engine = create_async_engine(integration_database_url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE users, projects, chapters, documents, document_versions CASCADE"
                )
            )

            user_id = uuid4()
            await conn.execute(
                text("INSERT INTO users (id, username, display_name) VALUES (:id, :u, :d)"),
                {"id": user_id, "u": f"author_{uuid4()}", "d": "Test Author"},
            )

            p1_id = uuid4()
            p1_slug = f"novel-one-{uuid4().hex[:8]}"
            p1_ws = str(tmp_path / "novel1")
            await conn.execute(
                text(
                    """
                    INSERT INTO projects (id, owner_id, slug, title, status, workspace_root)
                    VALUES (:id, :owner_id, :slug, :title, 'draft', :ws)
                    """
                ),
                {
                    "id": p1_id,
                    "owner_id": user_id,
                    "slug": p1_slug,
                    "title": "Novel One",
                    "ws": p1_ws,
                },
            )

            p2_id = uuid4()
            p2_slug = f"novel-two-{uuid4().hex[:8]}"
            p2_ws = str(tmp_path / "novel2")
            await conn.execute(
                text(
                    """
                    INSERT INTO projects (id, owner_id, slug, title, status, workspace_root)
                    VALUES (:id, :owner_id, :slug, :title, 'draft', :ws)
                    """
                ),
                {
                    "id": p2_id,
                    "owner_id": user_id,
                    "slug": p2_slug,
                    "title": "Novel Two",
                    "ws": p2_ws,
                },
            )

            # Add a world overview setting document to project 1
            d1_id = uuid4()
            v1_id = uuid4()
            content1 = "# Ancient World\nDeep history."
            h1 = sha256_content(content1)
            await conn.execute(
                text(
                    """
                    INSERT INTO documents (id, project_id, type, title, path, current_version_id, metadata)
                    VALUES (:id, :p_id, 'world_overview', 'World Overview', 'lore/world_overview.md', NULL, '{"category": "lore"}'::jsonb)
                    """
                ),
                {"id": d1_id, "p_id": p1_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, document_id, version_number, source, content_hash, byte_size, word_count, file_path, snapshot_path, metadata
                    )
                    VALUES (:id, :d_id, 1, 'user', :h, :bs, :wc, 'lore/world_overview.md', '.versions/v1.md', '{"initial": true}'::jsonb)
                    """
                ),
                {"id": v1_id, "d_id": d1_id, "h": h1, "bs": len(content1.encode()), "wc": 4},
            )
            await conn.execute(
                text("UPDATE documents SET current_version_id = :v_id WHERE id = :id"),
                {"id": d1_id, "v_id": v1_id},
            )

            # Add a novel document (outline) to project 1 that should NOT be migrated to setting_collections
            d2_id = uuid4()
            v2_id = uuid4()
            content2 = "# Full Outline"
            await conn.execute(
                text(
                    """
                    INSERT INTO documents (id, project_id, type, title, path, current_version_id, metadata)
                    VALUES (:id, :p_id, 'full_outline', 'Outline', 'outline/full.md', NULL, '{}'::jsonb)
                    """
                ),
                {"id": d2_id, "p_id": p1_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, document_id, version_number, source, content_hash, byte_size, word_count, file_path, snapshot_path, metadata
                    )
                    VALUES (:id, :d_id, 1, 'user', :h, :bs, :wc, 'outline/full.md', '.versions/v2.md', '{}'::jsonb)
                    """
                ),
                {
                    "id": v2_id,
                    "d_id": d2_id,
                    "h": sha256_content(content2),
                    "bs": len(content2.encode()),
                    "wc": 2,
                },
            )
            await conn.execute(
                text("UPDATE documents SET current_version_id = :v_id WHERE id = :id"),
                {"id": d2_id, "v_id": v2_id},
            )

        await engine.dispose()
        await terminate_other_connections(integration_database_url)

        # Run upgrade to head
        run_alembic(integration_database_url, "upgrade", "head")

        # Verify state after upgrade
        engine = create_async_engine(integration_database_url)
        async with engine.begin() as conn:
            p1 = (
                (
                    await conn.execute(
                        text("SELECT setting_collection_id, slug FROM projects WHERE id = :id"),
                        {"id": p1_id},
                    )
                )
                .mappings()
                .one()
            )
            p2 = (
                (
                    await conn.execute(
                        text("SELECT setting_collection_id, slug FROM projects WHERE id = :id"),
                        {"id": p2_id},
                    )
                )
                .mappings()
                .one()
            )

            assert p1["setting_collection_id"] is not None
            assert p2["setting_collection_id"] is not None
            assert p1["setting_collection_id"] != p2["setting_collection_id"]

            sc1 = (
                (
                    await conn.execute(
                        text("SELECT * FROM setting_collections WHERE id = :id"),
                        {"id": p1["setting_collection_id"]},
                    )
                )
                .mappings()
                .one()
            )
            assert sc1["slug"] == p1_slug
            assert sc1["title"] == "Novel One"
            assert sc1["workspace_root"] == p1_ws
            assert sc1["status"] == "active"
            assert sc1["revision"] == 1
            assert sc1["metadata"]["migrated_from_project_id"] == str(p1_id)

            # Check migrated setting document
            migrated_doc = (
                (
                    await conn.execute(
                        text("SELECT * FROM documents WHERE setting_collection_id = :sc_id"),
                        {"sc_id": sc1["id"]},
                    )
                )
                .mappings()
                .one()
            )
            assert migrated_doc["type"] == "world_overview"
            assert migrated_doc["project_id"] is None
            assert migrated_doc["chapter_id"] is None
            assert migrated_doc["metadata"]["migrated_from_document_id"] == str(d1_id)
            assert migrated_doc["metadata"]["migrated_from_version_id"] == str(v1_id)

            # Check migrated setting document version
            migrated_ver = (
                (
                    await conn.execute(
                        text("SELECT * FROM document_versions WHERE document_id = :d_id"),
                        {"d_id": migrated_doc["id"]},
                    )
                )
                .mappings()
                .one()
            )
            assert migrated_ver["version_number"] == 1
            assert migrated_ver["content_hash"] == h1
            assert migrated_ver["metadata"]["migrated_from_document_id"] == str(d1_id)

            # Check original setting document is preserved as legacy read-only
            old_doc = (
                (await conn.execute(text("SELECT * FROM documents WHERE id = :id"), {"id": d1_id}))
                .mappings()
                .one()
            )
            assert old_doc["project_id"] == p1_id
            assert old_doc["setting_collection_id"] is None
            assert old_doc["metadata"].get("legacy_setting_context") is True

            # Check non-setting document was not duplicated
            sc_outline_count = await conn.scalar(
                text(
                    "SELECT count(*) FROM documents WHERE setting_collection_id = :sc_id AND type = 'full_outline'"
                ),
                {"sc_id": sc1["id"]},
            )
            assert sc_outline_count == 0

            # Check old outline document is unaffected
            old_outline = (
                (await conn.execute(text("SELECT * FROM documents WHERE id = :id"), {"id": d2_id}))
                .mappings()
                .one()
            )
            assert old_outline["project_id"] == p1_id
            assert old_outline["setting_collection_id"] is None
            assert "legacy_setting_context" not in old_outline["metadata"]

        await engine.dispose()
    finally:
        await terminate_other_connections(integration_database_url)
        run_alembic(integration_database_url, "upgrade", "head")


async def test_database_constraints_rejection(async_session: AsyncSession) -> None:
    """Verifies that database constraints reject dual owner, zero owner, and setting chapter documents."""
    sc = SettingCollection(
        slug=f"sc-{uuid4().hex[:8]}",
        title="Shared Setting",
        workspace_root="/tmp/sc",
    )
    async_session.add(sc)
    await async_session.flush()

    project = Project(
        slug=f"proj-{uuid4().hex[:8]}",
        title="Project",
        workspace_root="/tmp/proj",
        setting_collection=sc,
    )
    async_session.add(project)
    await async_session.flush()

    chapter = Chapter(project_id=project.id, chapter_number=1, title="Chapter 1")
    async_session.add(chapter)
    await async_session.flush()

    # 1. Dual ownership rejection
    dual_doc = Document(
        project_id=project.id,
        setting_collection_id=sc.id,
        type="world_overview",
        path="lore/dual.md",
    )
    async_session.add(dual_doc)
    with pytest.raises(IntegrityError) as exc_info:
        await async_session.flush()
    assert "ck_documents_owner_xor" in str(exc_info.value)
    await async_session.rollback()

    # 2. Zero ownership rejection
    zero_doc = Document(
        project_id=None,
        setting_collection_id=None,
        type="world_overview",
        path="lore/zero.md",
    )
    async_session.add(zero_doc)
    with pytest.raises(IntegrityError) as exc_info:
        await async_session.flush()
    assert "ck_documents_owner_xor" in str(exc_info.value)
    await async_session.rollback()

    # 3. Chapter document belonging to setting collection rejection
    sc_chapter_doc = Document(
        project_id=None,
        setting_collection_id=sc.id,
        chapter_id=chapter.id,
        type="chapter_draft",
        path="chapters/draft.md",
    )
    async_session.add(sc_chapter_doc)
    with pytest.raises(IntegrityError) as exc_info:
        await async_session.flush()
    assert "ck_documents_chapter_requires_project" in str(exc_info.value)
    await async_session.rollback()


async def test_partial_unique_index_enforced(async_session: AsyncSession) -> None:
    """Verifies that partial unique indexes enforce path uniqueness per owner and allow path reuse across owners."""
    sc = SettingCollection(
        slug=f"sc-{uuid4().hex[:8]}",
        title="Setting",
        workspace_root="/tmp/sc",
    )
    async_session.add(sc)
    await async_session.flush()

    project = Project(
        slug=f"proj-{uuid4().hex[:8]}",
        title="Project",
        workspace_root="/tmp/proj",
        setting_collection=sc,
    )
    async_session.add(project)
    await async_session.flush()

    # Document in setting collection
    doc_sc = Document(
        setting_collection_id=sc.id,
        type="world_overview",
        path="lore/overview.md",
    )
    async_session.add(doc_sc)
    await async_session.flush()

    # Same path in novel project is ALLOWED because of partial unique indexes
    doc_project = Document(
        project_id=project.id,
        type="world_overview",
        path="lore/overview.md",
    )
    async_session.add(doc_project)
    await async_session.flush()

    # Duplicate path in same setting collection is REJECTED
    doc_sc_dup = Document(
        setting_collection_id=sc.id,
        type="world_overview",
        path="lore/overview.md",
    )
    async_session.add(doc_sc_dup)
    with pytest.raises(IntegrityError) as exc_info:
        await async_session.flush()
    assert "uq_documents_setting_collection_path" in str(exc_info.value)
    await async_session.rollback()


async def test_deletion_and_foreign_key_semantics(async_session: AsyncSession) -> None:
    """Verifies that deleting a project does not delete its setting collection, and referenced setting collection cannot be deleted."""
    sc = SettingCollection(
        slug=f"sc-{uuid4().hex[:8]}",
        title="Shared Setting",
        workspace_root="/tmp/sc",
    )
    async_session.add(sc)
    await async_session.flush()
    sc_id = sc.id

    p1 = Project(
        slug=f"p1-{uuid4().hex[:8]}",
        title="P1",
        workspace_root="/tmp/p1",
        setting_collection=sc,
    )
    p2 = Project(
        slug=f"p2-{uuid4().hex[:8]}",
        title="P2",
        workspace_root="/tmp/p2",
        setting_collection=sc,
    )
    async_session.add_all([p1, p2])
    await async_session.commit()
    p1_id = p1.id
    p2_id = p2.id

    # Deleting p1 must not delete shared setting collection
    p1 = await async_session.get(Project, p1_id)
    assert p1 is not None
    await async_session.delete(p1)
    await async_session.commit()

    remaining_sc = await async_session.get(SettingCollection, sc_id)
    assert remaining_sc is not None

    # Hard deleting setting collection while referenced by p2 must be rejected by RESTRICT FK
    await async_session.delete(remaining_sc)
    with pytest.raises(IntegrityError) as exc_info:
        await async_session.flush()
    assert "fk_projects_setting_collection_id" in str(exc_info.value)
    await async_session.rollback()

    # Reload p2 and delete p2
    reloaded_p2 = await async_session.get(Project, p2_id)
    assert reloaded_p2 is not None
    await async_session.delete(reloaded_p2)
    await async_session.commit()

    # Now unreferenced setting collection can be deleted
    reloaded_sc = await async_session.get(SettingCollection, sc_id)
    assert reloaded_sc is not None
    await async_session.delete(reloaded_sc)
    await async_session.commit()

    assert (await async_session.get(SettingCollection, sc_id)) is None


async def test_legacy_setting_documents_read_only(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that DocumentService rejects write and restore on legacy setting documents."""
    ws = tmp_path / "legacy_novel"
    lore_dir = ws / "lore"
    lore_dir.mkdir(parents=True, exist_ok=True)
    versions_dir = ws / ".versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    content = "# Old Lore"
    (lore_dir / "world.md").write_text(content, encoding="utf-8")
    (versions_dir / "old.md").write_text(content, encoding="utf-8")

    if os.name != "posix":
        monkeypatch.setattr(
            MarkdownStore, "__init__", lambda self, root: setattr(self, "root", root)
        )
        monkeypatch.setattr(
            MarkdownStore, "read", lambda self, path: (self.root / path).read_text(encoding="utf-8")
        )

    project = Project(
        slug=f"novel-{uuid4().hex[:8]}",
        title="Novel",
        workspace_root=str(ws),
    )
    async_session.add(project)
    await async_session.flush()

    doc = Document(
        project_id=project.id,
        type="world_overview",
        title="Legacy World",
        path="lore/world.md",
        metadata_={"legacy_setting_context": True},
    )
    async_session.add(doc)
    await async_session.flush()

    version = DocumentVersion(
        document_id=doc.id,
        version_number=1,
        source="user",
        content_hash=sha256_content(content),
        byte_size=len(content.encode()),
        word_count=2,
        file_path="lore/world.md",
        snapshot_path=".versions/old.md",
    )
    doc.current_version = version
    async_session.add(version)
    await async_session.flush()

    service = DocumentService(async_session)

    # Calling write_document on legacy setting document must fail
    with pytest.raises(ConflictError) as exc_info:
        await service.write_document(
            document_id=doc.id,
            content="# New Lore",
            source=DocumentSource.USER,
            expected_current_version_id=version.id,
        )
    assert "Legacy project setting documents are read-only" in str(exc_info.value)

    # Calling stage_write_document on legacy setting document must fail
    with pytest.raises(ConflictError) as exc_info:
        await service.stage_write_document(
            document_id=doc.id,
            content="# New Lore",
            source=DocumentSource.USER,
            expected_current_version_id=version.id,
        )
    assert "Legacy project setting documents are read-only" in str(exc_info.value)

    # Calling restore_document on legacy setting document must fail
    with pytest.raises(ConflictError) as exc_info:
        await service.restore_document(
            document_id=doc.id,
            version_id=version.id,
            source=DocumentSource.USER,
            expected_current_version_id=version.id,
        )
    assert "Legacy project setting documents are read-only" in str(exc_info.value)

    # Reading the legacy document must succeed
    read_doc = await async_session.get(Document, doc.id)
    assert read_doc is not None
    assert read_doc.id == doc.id
    current_content = await service.read_current_content(doc.id)
    assert current_content.content == content
