"""Manual archives are additive; old automatic versions are never backfilled or discarded."""

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


async def test_manual_archive_upgrade_leaves_auto_versions_unlisted_and_refuses_lossy_downgrade(
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
    collection, document, first, latest, point = [uuid4() for _ in range(5)]
    try:
        migrate("downgrade", "0015_setting_collection_collab")
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO setting_collections(id,slug,title,workspace_root) VALUES (:id,:slug,'Keep lore','/tmp/manual-point-migration')"
                ),
                {"id": collection, "slug": str(collection)},
            )
            await connection.execute(
                text(
                    "INSERT INTO documents(id,setting_collection_id,type,title,path) VALUES (:id,:collection,'world_overview','Keep current','world/current.md')"
                ),
                {"id": document, "collection": collection},
            )
            for number, identity, parent in [(1, first, None), (2, latest, first)]:
                await connection.execute(
                    text(
                        "INSERT INTO document_versions(id,document_id,version_number,parent_version_id,source,content_hash,file_path) VALUES (:id,:doc,:number,:parent,'user',:hash,'world/current.md')"
                    ),
                    {
                        "id": identity,
                        "doc": document,
                        "number": number,
                        "parent": parent,
                        "hash": str(number) * 64,
                    },
                )
            await connection.execute(
                text("UPDATE documents SET current_version_id=:version WHERE id=:doc"),
                {"version": latest, "doc": document},
            )
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM setting_document_restore_points")
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text("SELECT current_version_id FROM documents WHERE id=:id"), {"id": document}
                )
                == latest
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM document_versions WHERE document_id=:id"),
                    {"id": document},
                )
                == 2
            )
            await connection.execute(
                text(
                    "INSERT INTO setting_document_restore_points(id,document_id,version_id,point_number,comments_snapshot) VALUES (:id,:doc,:version,1,'[]'::jsonb)"
                ),
                {"id": point, "doc": document, "version": latest},
            )
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "INSERT INTO setting_document_restore_points(id,document_id,version_id,point_number,comments_snapshot) VALUES (:id,:doc,:version,1,'[]'::jsonb)"
                        ),
                        {"id": uuid4(), "doc": document, "version": latest},
                    )
            head_before_refused_downgrade = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        refused = migrate("downgrade", "0015_setting_collection_collab", check=False)
        assert refused.returncode != 0
        assert "refusing to discard restore points" in refused.stderr
        async with engine.begin() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == head_before_refused_downgrade
            )
            assert (
                await connection.scalar(
                    text("SELECT version_id FROM setting_document_restore_points WHERE id=:id"),
                    {"id": point},
                )
                == latest
            )
            # Remove only isolated test evidence to exercise the non-lossy reverse path.
            await connection.execute(
                text("DELETE FROM setting_document_restore_points WHERE id=:id"), {"id": point}
            )
        migrate("downgrade", "0015_setting_collection_collab")
        async with engine.begin() as connection:
            assert (
                await connection.scalar(
                    text("SELECT current_version_id FROM documents WHERE id=:id"), {"id": document}
                )
                == latest
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM document_versions WHERE document_id=:id"),
                    {"id": document},
                )
                == 2
            )
        migrate("upgrade", "head")
    finally:
        migrate("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM setting_collections WHERE id=:id"), {"id": collection}
            )
        await engine.dispose()
