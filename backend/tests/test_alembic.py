from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel"


def run_alembic(*args: str) -> str:
    environment = os.environ | {"DATABASE_URL": DATABASE_URL}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_initial_migration_generates_postgresql_upgrade_sql() -> None:
    sql = run_alembic("upgrade", "head", "--sql")

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in sql
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE review_reports" in sql
    assert "ADD CONSTRAINT fk_documents_current_version" in sql
    assert "CREATE INDEX idx_document_versions_created_at" in sql


def test_initial_migration_generates_downgrade_sql() -> None:
    sql = run_alembic("downgrade", "0001_initial_mvp_schema:base", "--sql")

    assert "DROP CONSTRAINT fk_documents_current_version" in sql
    assert "DROP TABLE document_versions" in sql
    assert "DROP TABLE users" in sql
