"""The additive assistant scope preserves novel history and refuses lossy rollback."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_upgrade_preserves_project_messages_and_downgrade_protects_collection_history(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url},
            check=check, capture_output=True, text=True, timeout=60,
        )

    engine = create_async_engine(integration_database_url)
    project, collection, novel_conv, world_conv, message, world_message = [uuid4() for _ in range(6)]
    try:
        migrate("downgrade", "0012_setting_document_trash")
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO setting_collections (id, slug, title, workspace_root) VALUES (:id, :slug, 'World', '/tmp/world')"), {"id": collection, "slug": str(collection)})
            await connection.execute(text("INSERT INTO projects (id, slug, title, status, workspace_root, setting_collection_id) VALUES (:id, :slug, 'Novel', 'draft', '/tmp/novel', :collection)"), {"id": project, "slug": str(project), "collection": collection})
            await connection.execute(text("INSERT INTO studio_assistant_conversations (id, project_id) VALUES (:id, :project)"), {"id": novel_conv, "project": project})
            await connection.execute(text("INSERT INTO studio_assistant_messages (id, conversation_id, role, content) VALUES (:id, :conv, 'user', 'Keep this novel history')"), {"id": message, "conv": novel_conv})
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id = :id"), {"id": message}) == "Keep this novel history"
            await connection.execute(text("INSERT INTO studio_assistant_conversations (id, setting_collection_id) VALUES (:id, :collection)"), {"id": world_conv, "collection": collection})
            await connection.execute(text("INSERT INTO studio_assistant_messages (id, conversation_id, role, content) VALUES (:id, :conv, 'assistant', 'Keep this world history')"), {"id": world_message, "conv": world_conv})
            head_before_refused_downgrade = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        refused = migrate("downgrade", "0012_setting_document_trash", check=False)
        assert refused.returncode != 0
        assert "refusing to discard messages" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head_before_refused_downgrade
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id = :id"), {"id": world_message}) == "Keep this world history"
            # Explicit test-only cleanup permits a non-lossy downgrade; no application purge is added.
            await connection.execute(text("DELETE FROM studio_assistant_conversations WHERE id = :id"), {"id": world_conv})
        migrate("downgrade", "0012_setting_document_trash")
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id = :id"), {"id": message}) == "Keep this novel history"
    finally:
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project})
            await connection.execute(text("DELETE FROM setting_collections WHERE id = :id"), {"id": collection})
        await engine.dispose()
