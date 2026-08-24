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
    assert "CREATE TABLE maintenance_changes" in sql
    assert "CREATE TABLE maintenance_affected_items" in sql
    assert "uq_workflow_runs_project_id_id" in sql
    assert "fk_maintenance_changes_project_run" in sql
    assert "ck_maintenance_changes_metadata_size" in sql


def test_initial_migration_generates_downgrade_sql() -> None:
    sql = run_alembic("downgrade", "0001_initial_mvp_schema:base", "--sql")

    assert "DROP CONSTRAINT fk_documents_current_version" in sql
    assert "DROP TABLE document_versions" in sql
    assert "DROP TABLE users" in sql


def test_maintenance_migration_downgrades_in_dependency_order() -> None:
    sql = run_alembic("downgrade", "0002_maintenance_persistence:0001_initial_mvp_schema", "--sql")

    affected = sql.index("DROP TABLE maintenance_affected_items")
    changes = sql.index("DROP TABLE maintenance_changes")
    workflow_unique = sql.index("DROP CONSTRAINT uq_workflow_runs_project_id_id")
    assert affected < changes < workflow_unique


def test_runtime_pin_migration_downgrades_in_dependency_order() -> None:
    sql = run_alembic(
        "downgrade", "0003_runtime_pin_event_sequence:0002_maintenance_persistence", "--sql"
    )

    assert "DROP INDEX uq_workflow_events_run_sequence" in sql
    assert "DROP COLUMN event_sequence" in sql


def test_reader_panel_migration_generates_upgrade_sql() -> None:
    sql = run_alembic(
        "upgrade", "0003_runtime_pin_event_sequence:0004_reader_panel_persistence", "--sql"
    )

    assert "CREATE TABLE reader_panel_sessions" in sql
    assert "CREATE TABLE reader_runs" in sql
    assert "CREATE TABLE reader_initial_reports" in sql
    assert "CREATE TABLE reader_panel_issues" in sql
    assert "CREATE TABLE reader_panel_ballots" in sql
    assert "CREATE TABLE reader_panel_messages" in sql
    assert "uq_reader_panel_sessions_run_id" in sql
    assert "uq_reader_runs_session_profile" in sql
    assert "uq_reader_initial_reports_run_id" in sql
    assert "uq_reader_panel_issues_session_number" in sql
    assert "uq_reader_panel_ballots_run_issue_phase" in sql
    assert "uq_reader_panel_messages_issue_round_turn" in sql


def test_reader_panel_migration_downgrades_in_dependency_order() -> None:
    sql = run_alembic(
        "downgrade", "0004_reader_panel_persistence:0003_runtime_pin_event_sequence", "--sql"
    )

    msg = sql.index("DROP TABLE reader_panel_messages")
    ballot = sql.index("DROP TABLE reader_panel_ballots")
    issue = sql.index("DROP TABLE reader_panel_issues")
    report = sql.index("DROP TABLE reader_initial_reports")
    run = sql.index("DROP TABLE reader_runs")
    session = sql.index("DROP TABLE reader_panel_sessions")

    assert msg < ballot < issue < report < run < session


def test_reader_panel_recovery_migration_matches_invocation_ledger() -> None:
    sql = run_alembic(
        "upgrade", "0004_reader_panel_persistence:0005_reader_panel_recovery", "--sql"
    )

    assert "CREATE TABLE reader_panel_invocations" in sql
    assert "uq_reader_panel_invocations_work_attempt" in sql
    assert "uq_reader_panel_invocations_success" in sql
    assert "ck_reader_panel_invocations_status" in sql
    assert "ck_reader_panel_invocations_safe_error" in sql
    assert "ck_reader_panel_invocations_work_key" in sql
    assert "UPDATE reader_panel_sessions" in sql
    for key in (
        "max_model_calls_per_phase",
        "max_total_input_tokens",
        "max_total_output_tokens",
        "max_output_tokens_per_call",
        "max_messages",
        "max_provider_attempts",
        "max_invalid_output_repairs",
    ):
        assert key in sql
    assert "prompt" not in sql.lower()
    assert "response" not in sql.lower()


def test_reader_panel_recovery_migration_downgrades_ledger() -> None:
    sql = run_alembic(
        "downgrade", "0005_reader_panel_recovery:0004_reader_panel_persistence", "--sql"
    )

    assert "DROP TABLE reader_panel_invocations" in sql
    assert "config_snapshot -" in sql
