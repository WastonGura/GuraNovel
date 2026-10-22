"""Explanations are additive metadata, and downgrade cannot silently discard them."""

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


async def test_archive_message_migration_preserves_manual_points_and_protects_user_explanations(
    integration_database_url,
):
    def migrate(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ | {"DATABASE_URL": integration_database_url},
            check=check,
            capture_output=True,
            text=True,
            timeout=60,
        )

    await clean_test_data(integration_database_url)
    engine = create_async_engine(integration_database_url)
    collection, document, version, point = [uuid4() for _ in range(4)]
    fields = "id,document_id,version_id,point_number,summary,comments_snapshot,created_at"
    try:
        migrate("downgrade", "0016_setting_restore_points")
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'Preserve collection','/tmp/archive-message-migration')"
                ),
                {"id": collection, "slug": str(collection)},
            )
            await connection.execute(
                text(
                    "INSERT INTO documents(id,setting_collection_id,type,title,path) VALUES (:id,:collection,'world_overview','Current title','world/message.md')"
                ),
                {"id": document, "collection": collection},
            )
            await connection.execute(
                text(
                    "INSERT INTO document_versions(id,document_id,version_number,source,content_hash,file_path) VALUES (:id,:doc,1,'user',:hash,'world/message.md')"
                ),
                {"id": version, "doc": document, "hash": "a" * 64},
            )
            await connection.execute(
                text("UPDATE documents SET current_version_id=:version WHERE id=:doc"),
                {"version": version, "doc": document},
            )
            await connection.execute(
                text(
                    "INSERT INTO setting_document_restore_points(id,document_id,version_id,point_number,summary,comments_snapshot) VALUES (:id,:doc,:version,1,'Original archived title','[]'::jsonb)"
                ),
                {"id": point, "doc": document, "version": version},
            )
            original = dict(
                (
                    await connection.execute(
                        text(f"SELECT {fields} FROM setting_document_restore_points WHERE id=:id"),
                        {"id": point},
                    )
                )
                .mappings()
                .one()
            )
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            preserved = dict(
                (
                    await connection.execute(
                        text(
                            f"SELECT {fields},message FROM setting_document_restore_points WHERE id=:id"
                        ),
                        {"id": point},
                    )
                )
                .mappings()
                .one()
            )
            assert preserved == {**original, "message": None}
            await connection.execute(
                text(
                    "UPDATE setting_document_restore_points SET message='A user explanation' WHERE id=:id"
                ),
                {"id": point},
            )
        refused = migrate("downgrade", "0016_setting_restore_points", check=False)
        assert refused.returncode != 0
        assert "refusing to discard user explanations" in refused.stderr
        async with engine.begin() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0017_setting_archive_messages"
            )
            assert (
                await connection.scalar(
                    text("SELECT message FROM setting_document_restore_points WHERE id=:id"),
                    {"id": point},
                )
                == "A user explanation"
            )
            assert (
                await connection.scalar(
                    text("SELECT current_version_id FROM documents WHERE id=:id"), {"id": document}
                )
                == version
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM document_versions WHERE document_id=:id"),
                    {"id": document},
                )
                == 1
            )
            # Explicit isolated-test clearing exercises the non-lossy reverse migration.
            await connection.execute(
                text("UPDATE setting_document_restore_points SET message=NULL WHERE id=:id"),
                {"id": point},
            )
        migrate("downgrade", "0016_setting_restore_points")
        async with engine.begin() as connection:
            assert (
                dict(
                    (
                        await connection.execute(
                            text(
                                f"SELECT {fields} FROM setting_document_restore_points WHERE id=:id"
                            ),
                            {"id": point},
                        )
                    )
                    .mappings()
                    .one()
                )
                == original
            )
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert (
                await connection.scalar(
                    text("SELECT message FROM setting_document_restore_points WHERE id=:id"),
                    {"id": point},
                )
                is None
            )
    finally:
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM setting_collections WHERE id=:id"), {"id": collection}
            )
        await engine.dispose()
