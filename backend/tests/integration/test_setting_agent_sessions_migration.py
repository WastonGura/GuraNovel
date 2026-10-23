"""0021 adds sessions without losing default/document/Gura or durable attempts."""

import json
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


async def test_sessions_upgrade_preserves_original_ids_attempts_and_proposals_and_guards_downgrade(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url}, check=check,
            capture_output=True, text=True, timeout=60,
        )

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, pending_collection, empty_collection, document, default, pending, legacy, gura, extra, explicit = [uuid4() for _ in range(10)]
    try:
        migrate("downgrade", "0020_setting_agent_attempts")
        async with engine.begin() as connection:
            for identity in (collection, pending_collection, empty_collection):
                await connection.execute(text("INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'Existing archive','/tmp/sessions-migration')"), {"id": identity, "slug": str(identity)})
            await connection.execute(text("INSERT INTO documents(id,setting_collection_id,type,title,path) VALUES (:id,:collection,'world_overview','Old note','world/old.md')"), {"id": document, "collection": collection})
            for identity, collection_id, document_id in ((default, collection, None), (legacy, collection, document), (pending, pending_collection, None)):
                await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,document_id,agent_role) VALUES (:id,:collection,:document,'lore_agent')"), {"id": identity, "collection": collection_id, "document": document_id})
            for identity, content in ((default, "  Existing collection question  "), (legacy, "Original document discussion")):
                await connection.execute(text("INSERT INTO setting_collaboration_messages(id,conversation_id,role,content,client_message_id,request_fingerprint) VALUES (:id,:conv,'user',:content,'old-request',:fingerprint)"), {"id": uuid4(), "conv": identity, "content": content, "fingerprint": "b" * 64})
            await connection.execute(text("INSERT INTO setting_collaboration_messages(id,conversation_id,role,content,proposal_id,proposal,proposal_status,base_content,base_title) VALUES (:id,:conv,'assistant','Unadopted proposal',:proposal,:body,'pending','','')"), {"id": uuid4(), "conv": default, "proposal": uuid4(), "body": json.dumps({"unchanged": "proposal evidence"})})
            request = {"client_message_id": "old-pending", "content": "First pending question"}
            await connection.execute(text("INSERT INTO setting_collaboration_attempts(id,conversation_id,client_message_id,request_fingerprint,request,context,history,status,provenance) VALUES (:id,:conv,'old-pending',:fingerprint,:request,'{\"scope\":\"collection\",\"documents\":[]}'::jsonb,'[]'::jsonb,'dispatched','{\"model_identifier\":\"mock-before-upgrade\"}'::jsonb)"), {"id": uuid4(), "conv": pending, "fingerprint": "a" * 64, "request": json.dumps(request)})
            await connection.execute(text("INSERT INTO studio_assistant_conversations(id,home_owner_key,title) VALUES (:id,'local','Unrelated Gura')"), {"id": gura})
            await connection.execute(text("INSERT INTO studio_assistant_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Gura is independent')"), {"id": uuid4(), "conv": gura})
            original_messages = (await connection.execute(text("SELECT * FROM setting_collaboration_messages ORDER BY id"))).all()
            original_attempts = (await connection.execute(text("SELECT * FROM setting_collaboration_attempts ORDER BY id"))).all()
            original_scopes = (await connection.execute(text("SELECT id,setting_collection_id,document_id,agent_role,created_at,updated_at FROM setting_collaborations ORDER BY id"))).all()
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT * FROM setting_collaboration_messages ORDER BY id"))).all() == original_messages
            assert (await connection.execute(text("SELECT * FROM setting_collaboration_attempts ORDER BY id"))).all() == original_attempts
            assert (await connection.execute(text("SELECT id,setting_collection_id,document_id,agent_role,created_at,updated_at FROM setting_collaborations ORDER BY id"))).all() == original_scopes
            assert await connection.scalar(text("SELECT is_default FROM setting_collaborations WHERE id=:id"), {"id": default}) is True
            assert await connection.scalar(text("SELECT is_default FROM setting_collaborations WHERE id=:id"), {"id": legacy}) is False
            assert await connection.scalar(text("SELECT title FROM setting_collaborations WHERE id=:id"), {"id": default}) == "Existing collection question"
            assert await connection.scalar(text("SELECT title FROM setting_collaborations WHERE id=:id"), {"id": pending}) == "First pending question"
            assert await connection.scalar(text("SELECT content FROM studio_assistant_messages WHERE conversation_id=:id"), {"id": gura}) == "Gura is independent"
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,agent_role,is_default) VALUES (:id,:collection,'lore_agent',true)"), {"id": uuid4(), "collection": collection})
            await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,agent_role) VALUES (:id,:collection,'lore_agent')"), {"id": extra, "collection": collection})
        refused = migrate("downgrade", "0020_setting_agent_attempts", check=False)
        assert refused.returncode != 0 and "refusing to discard conversations or creation identities" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0021_setting_agent_sessions"
            assert await connection.scalar(text("SELECT id FROM setting_collaborations WHERE id=:id"), {"id": extra}) == extra
            await connection.execute(text("DELETE FROM setting_collaborations WHERE id=:id"), {"id": extra})
            await connection.execute(text("INSERT INTO setting_collaborations(id,setting_collection_id,agent_role,client_request_id) VALUES (:id,:collection,'lore_agent',:request)"), {"id": explicit, "collection": empty_collection, "request": uuid4()})
        # Even a sole new session must retain the creation identity used by retries.
        refused = migrate("downgrade", "0020_setting_agent_attempts", check=False)
        assert refused.returncode != 0 and "creation identities" in refused.stderr
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM setting_collaborations WHERE id=:id"), {"id": explicit})
        migrate("downgrade", "0020_setting_agent_attempts")
        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT * FROM setting_collaboration_messages ORDER BY id"))).all() == original_messages
            assert (await connection.execute(text("SELECT * FROM setting_collaboration_attempts ORDER BY id"))).all() == original_attempts
        migrate("upgrade", "head")
    finally:
        migrate("upgrade", "head")
        await engine.dispose()
        await clean_test_data(integration_database_url)
