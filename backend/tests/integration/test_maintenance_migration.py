from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]


def run_alembic(database_url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


async def schema_snapshot(database_url: str) -> tuple[str, str | None, str | None]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            changes = await connection.scalar(text("SELECT to_regclass('maintenance_changes')::text"))
            items = await connection.scalar(
                text("SELECT to_regclass('maintenance_affected_items')::text")
            )
            assert isinstance(revision, str)
            return revision, changes, items
    finally:
        await engine.dispose()


async def seed_v070_rows(database_url: str, project_id: UUID, run_id: UUID) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, slug, title, workspace_root, current_workflow_id) "
                    "VALUES (:project_id, :slug, :title, :workspace_root, :run_id)"
                ),
                {
                    "project_id": project_id,
                    "slug": "migration-v070-survival",
                    "title": "v0.7.0 survives",
                    "workspace_root": "/tmp/migration-v070-survival",
                    "run_id": run_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO workflow_runs "
                    "(id, project_id, workflow_type, status, current_node) "
                    "VALUES (:run_id, :project_id, 'project_maintenance', "
                    "'CHANGE_REQUESTED', 'user_change_request')"
                ),
                {"run_id": run_id, "project_id": project_id},
            )
    finally:
        await engine.dispose()


async def assert_v070_rows_survive(database_url: str, project_id: UUID, run_id: UUID) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT title FROM projects WHERE id = :project_id"),
                {"project_id": project_id},
            ) == "v0.7.0 survives"
            assert await connection.scalar(
                text("SELECT project_id FROM workflow_runs WHERE id = :run_id"),
                {"run_id": run_id},
            ) == project_id
    finally:
        await engine.dispose()


async def maintenance_schema_evidence(database_url: str) -> dict[str, object]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            columns = list(
                await connection.execute(
                    text(
                        "SELECT table_name, column_name, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name IN "
                        "('maintenance_changes', 'maintenance_affected_items')"
                    )
                )
            )
            constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid IN "
                        "('maintenance_changes'::regclass, "
                        "'maintenance_affected_items'::regclass, 'workflow_runs'::regclass)"
                    )
                )
            )
            indexes = set(
                await connection.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                        "AND tablename IN ('maintenance_changes', 'maintenance_affected_items')"
                    )
                )
            )
            delete_rule_rows = await connection.execute(
                text(
                    "SELECT conname, confdeltype::text FROM pg_constraint "
                    "WHERE conrelid IN ('maintenance_changes'::regclass, "
                    "'maintenance_affected_items'::regclass) AND contype = 'f'"
                )
            )
            delete_rules = {row[0]: row[1] for row in delete_rule_rows}
            return {
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
                "delete_rules": delete_rules,
            }
    finally:
        await engine.dispose()


async def assert_no_0002_residue(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            constraints = list(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname LIKE '%maintenance%' "
                        "OR conname = 'uq_workflow_runs_project_id_id'"
                    )
                )
            )
            indexes = list(
                await connection.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                        "AND indexname LIKE 'idx_maintenance_%'"
                    )
                )
            )
            assert constraints == []
            assert indexes == []
    finally:
        await engine.dispose()


async def cleanup_seed(database_url: str, project_id: UUID) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM projects WHERE id = :project_id"),
                {"project_id": project_id},
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_real_upgrade_from_v070_and_clean_downgrade(
    integration_database_url: str,
) -> None:
    project_id = UUID("10000000-0000-4000-8000-000000000091")
    run_id = UUID("20000000-0000-4000-8000-000000000091")
    try:
        run_alembic(integration_database_url, "downgrade", "0001_initial_mvp_schema")
        assert await schema_snapshot(integration_database_url) == (
            "0001_initial_mvp_schema",
            None,
            None,
        )
        await seed_v070_rows(integration_database_url, project_id, run_id)
        run_alembic(integration_database_url, "upgrade", "head")
        assert await schema_snapshot(integration_database_url) == (
            "0002_maintenance_persistence",
            "maintenance_changes",
            "maintenance_affected_items",
        )
        await assert_v070_rows_survive(integration_database_url, project_id, run_id)
        evidence = await maintenance_schema_evidence(integration_database_url)
        assert {
            (table, column, nullable)
            for table, column, nullable in evidence["columns"]  # type: ignore[union-attr]
        } >= {
            ("maintenance_changes", "project_id", "NO"),
            ("maintenance_changes", "workflow_run_id", "NO"),
            ("maintenance_changes", "revision_plan_document_id", "YES"),
            ("maintenance_changes", "applied_at", "YES"),
            ("maintenance_affected_items", "position", "NO"),
            ("maintenance_affected_items", "existing_document_id", "YES"),
            ("maintenance_affected_items", "existing_chapter_id", "YES"),
        }
        assert evidence["constraints"] >= {  # type: ignore[operator]
            "uq_workflow_runs_project_id_id",
            "fk_maintenance_changes_project_run",
            "uq_maintenance_changes_workflow_run_id",
            "uq_maintenance_affected_items_position",
            "ck_maintenance_changes_late_plan",
            "ck_maintenance_affected_items_type",
        }
        assert evidence["indexes"] >= {  # type: ignore[operator]
            "idx_maintenance_changes_project_status",
            "idx_maintenance_affected_items_change_id",
        }
        assert evidence["delete_rules"] == {  # type: ignore[comparison-overlap]
            "maintenance_changes_project_id_fkey": "c",
            "fk_maintenance_changes_project_run": "c",
            "maintenance_changes_revision_plan_document_id_fkey": "n",
            "maintenance_affected_items_maintenance_change_id_fkey": "c",
            "maintenance_affected_items_existing_document_id_fkey": "n",
            "maintenance_affected_items_existing_chapter_id_fkey": "n",
        }
        run_alembic(integration_database_url, "downgrade", "0001_initial_mvp_schema")
        assert await schema_snapshot(integration_database_url) == (
            "0001_initial_mvp_schema",
            None,
            None,
        )
        await assert_v070_rows_survive(integration_database_url, project_id, run_id)
        await assert_no_0002_residue(integration_database_url)
    finally:
        run_alembic(integration_database_url, "upgrade", "head")
        await cleanup_seed(integration_database_url, project_id)
