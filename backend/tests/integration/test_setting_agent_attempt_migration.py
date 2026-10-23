"""0020 preserves old setting/Gura messages and refuses to erase dispatch evidence."""

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


async def test_attempt_upgrade_preserves_histories_and_downgrade_refuses_any_dispatch_identity(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url}, check=check,
            capture_output=True, text=True, timeout=60,
        )

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, document, document_conversation, collection_conversation, gura, attempt = [uuid4() for _ in range(6)]
    try:
        migrate("downgrade", "0019_global_assistant_sessions")
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'Old archive','/tmp/setting-attempt-migration')"), {"id": collection, "slug": str(collection)})
            await connection.execute(text("INSERT INTO documents(id,setting_collection_id,type,title,path) VALUES (:id,:collection,'world_overview','Tide','world/tide.md')"), {"id": document, "collection": collection})
            for conversation_id, document_id in ((document_conversation, document), (collection_conversation, None)):
                await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,document_id,agent_role) VALUES (:id,:collection,:document,'lore_agent')"), {"id": conversation_id, "collection": collection, "document": document_id})
                for role in ("user", "assistant"):
                    await connection.execute(text("INSERT INTO setting_collaboration_messages(id,conversation_id,role,content) VALUES (:id,:conv,:role,:content)"), {"id": uuid4(), "conv": conversation_id, "role": role, "content": f"Original {role} history"})
            await connection.execute(text("INSERT INTO studio_assistant_conversations(id,home_owner_key,title) VALUES (:id,'local','Independent Gura')"), {"id": gura})
            await connection.execute(text("INSERT INTO studio_assistant_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Gura stays independent')"), {"id": uuid4(), "conv": gura})
            original = (await connection.execute(text("SELECT id,conversation_id,role,content,created_at FROM setting_collaboration_messages ORDER BY id"))).all()
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT id,conversation_id,role,content,created_at FROM setting_collaboration_messages ORDER BY id"))).all() == original
            assert await connection.scalar(text("SELECT count(*) FROM setting_collaboration_messages WHERE attempt_id IS NOT NULL")) == 0
            assert await connection.scalar(text("SELECT count(*) FROM setting_collaboration_attempts")) == 0
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE conversation_id=:id"), {"id": gura}) == "Gura stays independent"
        # Empty additive storage can be removed without changing historical rows.
        migrate("downgrade", "0019_global_assistant_sessions")
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO setting_collaboration_attempts(id,conversation_id,client_message_id,request_fingerprint,request,context,history,status) VALUES (:id,:conv,'permanent-identity',:fingerprint,'{\"content\":\"Keep this input\",\"client_message_id\":\"permanent-identity\"}'::jsonb,'{}'::jsonb,'[]'::jsonb,'finished')"), {"id": attempt, "conv": collection_conversation, "fingerprint": "a" * 64})
            head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        refused = migrate("downgrade", "0019_global_assistant_sessions", check=False)
        assert refused.returncode != 0
        assert "refusing to discard dispatch identities or results" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head
            assert await connection.scalar(text("SELECT request->>'content' FROM setting_collaboration_attempts WHERE id=:id"), {"id": attempt}) == "Keep this input"
            assert (await connection.execute(text("SELECT id,conversation_id,role,content,created_at FROM setting_collaboration_messages ORDER BY id"))).all() == original
    finally:
        await engine.dispose()
        await clean_test_data(integration_database_url)
        migrate("upgrade", "head")
