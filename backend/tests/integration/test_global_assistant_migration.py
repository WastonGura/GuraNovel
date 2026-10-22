"""0019 preserves 0018 and legacy histories and refuses to collapse multiple sessions."""

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


async def test_global_sessions_upgrade_existing_home_and_scoped_histories_without_loss(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url},
            check=check, capture_output=True, text=True, timeout=60,
        )

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, project, old_project, old_collection, old_home, new_session = [uuid4() for _ in range(6)]
    fields = "id,project_id,setting_collection_id,home_owner_key,chapter_id,title,created_at,updated_at"
    try:
        migrate("downgrade", "0018_home_assistant_scope")
        async with engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'World','/tmp/global-migration')"
            ), {"id": collection, "slug": str(collection)})
            await connection.execute(text(
                "INSERT INTO projects(id,slug,title,status,workspace_root,setting_collection_id) VALUES (:id,:slug,'Novel','draft','/tmp/global-migration',:collection)"
            ), {"id": project, "slug": str(project), "collection": collection})
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,project_id,title) VALUES (:id,:project,'Old novel conversation')"
            ), {"id": old_project, "project": project})
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,setting_collection_id,title) VALUES (:id,:collection,'Old collection conversation')"
            ), {"id": old_collection, "collection": collection})
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,home_owner_key,title) VALUES (:id,'local','Gura 首页帮助')"
            ), {"id": old_home})
            for conversation_id in (old_project, old_collection, old_home):
                await connection.execute(text(
                    "INSERT INTO studio_assistant_messages(id,conversation_id,role,content,tool_calls) "
                    "VALUES (:id,:conv,'user','Preserve pre-global history','[{\"client_message_id\":\"original\"}]'::jsonb)"
                ), {"id": uuid4(), "conv": conversation_id})
            original = (await connection.execute(text(
                f"SELECT {fields} FROM studio_assistant_conversations ORDER BY id"
            ))).mappings().all()
        # 0018's actual unique contract is verified before the new migration drops it.
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(text(
                    "INSERT INTO studio_assistant_conversations(id,home_owner_key) VALUES (:id,'local')"
                ), {"id": new_session})
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert (await connection.execute(text(
                f"SELECT {fields} FROM studio_assistant_conversations ORDER BY id"
            ))).mappings().all() == original
            assert await connection.scalar(text(
                "SELECT count(*) FROM studio_assistant_messages WHERE tool_calls='[{\"client_message_id\":\"original\"}]'::jsonb"
            )) == 3
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,home_owner_key,title) VALUES (:id,'local','New global conversation')"
            ), {"id": new_session})
            await connection.execute(text(
                "INSERT INTO studio_assistant_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Keep another global session')"
            ), {"id": uuid4(), "conv": new_session})
            head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        refused = migrate("downgrade", "0018_home_assistant_scope", check=False)
        assert refused.returncode != 0
        assert "refusing to discard conversations" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head
            assert await connection.scalar(text("SELECT count(*) FROM studio_assistant_messages")) == 4
            assert await connection.scalar(text(
                "SELECT content FROM studio_assistant_messages WHERE conversation_id=:id"
            ), {"id": new_session}) == "Keep another global session"
            # Explicit isolated-test clearing permits the non-lossy reverse path.
            await connection.execute(text("DELETE FROM studio_assistant_conversations WHERE id=:id"), {"id": new_session})
        migrate("downgrade", "0018_home_assistant_scope")
        async with engine.begin() as connection:
            assert (await connection.execute(text(
                f"SELECT {fields} FROM studio_assistant_conversations ORDER BY id"
            ))).mappings().all() == original
            assert await connection.scalar(text("SELECT count(*) FROM studio_assistant_messages")) == 3
        migrate("upgrade", "head")
    finally:
        migrate("upgrade", "head")
        await engine.dispose()
        await clean_test_data(integration_database_url)
