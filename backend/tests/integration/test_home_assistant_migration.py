"""The home scope upgrades existing history and refuses a destructive downgrade."""

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


async def test_home_migration_preserves_scoped_history_and_refuses_lossy_downgrade(integration_database_url):
    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url},
            check=check, capture_output=True, text=True, timeout=60,
        )

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, project, project_conv, collection_conv, home_conv = [uuid4() for _ in range(5)]
    fields = "id,project_id,setting_collection_id,chapter_id,title,created_at,updated_at"
    try:
        migrate("downgrade", "0017_setting_archive_messages")
        async with engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'World','/tmp/home-migration')"
            ), {"id": collection, "slug": str(collection)})
            await connection.execute(text(
                "INSERT INTO projects(id,slug,title,status,workspace_root,setting_collection_id) VALUES (:id,:slug,'Novel','draft','/tmp/home-migration',:collection)"
            ), {"id": project, "slug": str(project), "collection": collection})
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,project_id) VALUES (:id,:project)"
            ), {"id": project_conv, "project": project})
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,setting_collection_id) VALUES (:id,:collection)"
            ), {"id": collection_conv, "collection": collection})
            for conversation_id in (project_conv, collection_conv):
                await connection.execute(text(
                    "INSERT INTO studio_assistant_messages(id,conversation_id,role,content,tool_calls) "
                    "VALUES (:id,:conv,'user','Preserve original history','[{\"client_message_id\":\"legacy\"}]'::jsonb)"
                ), {"id": uuid4(), "conv": conversation_id})
            original = (await connection.execute(text(
                f"SELECT {fields} FROM studio_assistant_conversations ORDER BY id"
            ))).mappings().all()

        migrate("upgrade", "0018_home_assistant_scope")
        async with engine.begin() as connection:
            preserved = (await connection.execute(text(
                f"SELECT {fields} FROM studio_assistant_conversations ORDER BY id"
            ))).mappings().all()
            assert preserved == original
            assert await connection.scalar(text(
                "SELECT count(*) FROM studio_assistant_messages WHERE tool_calls = '[{\"client_message_id\":\"legacy\"}]'::jsonb"
            )) == 2
            await connection.execute(text(
                "INSERT INTO studio_assistant_conversations(id,home_owner_key) VALUES (:id,'local')"
            ), {"id": home_conv})
            await connection.execute(text(
                "INSERT INTO studio_assistant_messages(id,conversation_id,role,content) VALUES (:id,:conv,'assistant','Keep home guidance')"
            ), {"id": uuid4(), "conv": home_conv})
            head = await connection.scalar(text("SELECT version_num FROM alembic_version"))

        refused = migrate("downgrade", "0017_setting_archive_messages", check=False)
        assert refused.returncode != 0
        assert "refusing to discard messages" in refused.stderr
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head
            assert await connection.scalar(text(
                "SELECT content FROM studio_assistant_messages WHERE conversation_id=:id"
            ), {"id": home_conv}) == "Keep home guidance"
            # Explicit disposable-test cleanup enables the non-lossy reverse path.
            await connection.execute(text(
                "DELETE FROM studio_assistant_conversations WHERE id=:id"
            ), {"id": home_conv})
        migrate("downgrade", "0017_setting_archive_messages")
        async with engine.begin() as connection:
            assert (await connection.execute(text(
                f"SELECT {fields} FROM studio_assistant_conversations ORDER BY id"
            ))).mappings().all() == original
            assert await connection.scalar(text("SELECT count(*) FROM studio_assistant_messages")) == 2
        migrate("upgrade", "head")
    finally:
        migrate("upgrade", "head")
        await engine.dispose()
        await clean_test_data(integration_database_url)
