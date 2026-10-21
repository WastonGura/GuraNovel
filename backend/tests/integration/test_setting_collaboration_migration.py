"""Publishing authoring storage preserves Gura and refuses loss of discussions."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import clean_test_data

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_collaboration_upgrade_preserves_gura_and_rollback_preserves_proposals(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=Path(__file__).resolve().parents[2],
                              env=os.environ | {"DATABASE_URL": integration_database_url}, check=check, capture_output=True, text=True, timeout=60)

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, document, gura, gura_message, authoring, authoring_message = [uuid4() for _ in range(6)]
    try:
        migrate("downgrade", "0013_setting_assistant_scope")
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'Archive','/tmp/authoring-migration')"), {"id": collection, "slug": str(collection)})
            await connection.execute(text("INSERT INTO documents(id,setting_collection_id,type,title,path) VALUES (:id,:collection,'world_overview','Tide','world/tide.md')"), {"id": document, "collection": collection})
            await connection.execute(text("INSERT INTO studio_assistant_conversations(id,setting_collection_id) VALUES (:id,:collection)"), {"id": gura, "collection": collection})
            await connection.execute(text("INSERT INTO studio_assistant_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Global Gura history')"), {"id": gura_message, "conv": gura})
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id=:id"), {"id": gura_message}) == "Global Gura history"
            await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,document_id,agent_role) VALUES (:id,:collection,:document,'worldbuilding_agent')"), {"id": authoring, "collection": collection, "document": document})
            await connection.execute(text("INSERT INTO setting_collaboration_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Unadopted authoring discussion')"), {"id": authoring_message, "conv": authoring})
        refused = migrate("downgrade", "0013_setting_assistant_scope", check=False)
        assert refused.returncode != 0
        assert "refusing to discard discussions or proposals" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT content FROM setting_collaboration_messages WHERE id=:id"), {"id": authoring_message}) == "Unadopted authoring discussion"
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE id=:id"), {"id": gura_message}) == "Global Gura history"
            await connection.execute(text("DELETE FROM setting_collections WHERE id=:id"), {"id": collection})
        migrate("downgrade", "0013_setting_assistant_scope")
        migrate("upgrade", "head")
    finally:
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM setting_collections WHERE id=:id"), {"id": collection})
        await engine.dispose()
