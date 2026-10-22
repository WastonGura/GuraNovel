"""Collection scope is additive; existing per-note and Gura histories stay intact."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import clean_test_data

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_collection_scope_upgrade_preserves_both_existing_histories_and_refuses_lossy_rollback(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=Path(__file__).resolve().parents[2],
                              env=os.environ | {"DATABASE_URL": integration_database_url}, check=check, capture_output=True, text=True, timeout=60)

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, document, old_conversation, old_message, gura, gura_message, whole_conversation = [uuid4() for _ in range(7)]
    try:
        migrate("downgrade", "0014_setting_collaboration")
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'Whole archive','/tmp/collection-scope-migration')"), {"id": collection, "slug": str(collection)})
            await connection.execute(text("INSERT INTO documents(id,setting_collection_id,type,title,path) VALUES (:id,:collection,'world_overview','Old world','world/old.md')"), {"id": document, "collection": collection})
            await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,document_id,agent_role) VALUES (:id,:collection,:document,'worldbuilding_agent')"), {"id": old_conversation, "collection": collection, "document": document})
            await connection.execute(text("INSERT INTO setting_collaboration_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Keep per-note authoring history exactly')"), {"id": old_message, "conv": old_conversation})
            await connection.execute(text("INSERT INTO studio_assistant_conversations(id,setting_collection_id) VALUES (:id,:collection)"), {"id": gura, "collection": collection})
            await connection.execute(text("INSERT INTO studio_assistant_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Keep global Gura history exactly')"), {"id": gura_message, "conv": gura})
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT content FROM setting_collaboration_messages WHERE id=:id"), {"id": old_message}) == "Keep per-note authoring history exactly"
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id=:id"), {"id": gura_message}) == "Keep global Gura history exactly"
            await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,document_id,agent_role) VALUES (:id,:collection,NULL,'lore_agent')"), {"id": whole_conversation, "collection": collection})
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,document_id,agent_role) VALUES (:id,:collection,NULL,'lore_agent')"), {"id": uuid4(), "collection": collection})
            head_before_refused_downgrade = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        refused = migrate("downgrade", "0014_setting_collaboration", check=False)
        assert refused.returncode != 0
        assert "refusing to discard collection discussions or proposals" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head_before_refused_downgrade
            assert await connection.scalar(text("SELECT id FROM setting_collaborations WHERE id=:id"), {"id": whole_conversation}) == whole_conversation
            # Only isolated test data is removed to exercise a non-lossy reverse migration.
            await connection.execute(text("DELETE FROM setting_collaborations WHERE id=:id"), {"id": whole_conversation})
        migrate("downgrade", "0014_setting_collaboration")
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT document_id FROM setting_collaborations WHERE id=:id"), {"id": old_conversation}) == document
            assert await connection.scalar(text("SELECT content FROM setting_collaboration_messages WHERE id=:id"), {"id": old_message}) == "Keep per-note authoring history exactly"
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id=:id"), {"id": gura_message}) == "Keep global Gura history exactly"
        migrate("upgrade", "head")
    finally:
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM setting_collections WHERE id=:id"), {"id": collection})
        await engine.dispose()
