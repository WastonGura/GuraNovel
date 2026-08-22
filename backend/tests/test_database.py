import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import configure_mappers
from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from app.api.deps import get_db_session
from app.core.config import Settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    ActionRequest,
    Document,
    MaintenanceAffectedItem,
    MaintenanceChange,
    Project,
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
    ReaderPanelMessage,
    ReaderPanelSession,
    ReaderRun,
    ReviewReport,
    User,
    WorkflowRun,
)


def test_database_url_can_be_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:password@db:5432/novels")

    configured_settings = Settings(_env_file=None)

    assert configured_settings.database_url == "postgresql+asyncpg://user:password@db:5432/novels"


def test_workspace_base_dir_can_be_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_BASE_DIR", "/srv/guranovel-workspaces")

    configured_settings = Settings(_env_file=None)

    assert configured_settings.workspace_base_dir == Path("/srv/guranovel-workspaces")


def test_workspace_base_dir_default_is_cwd_independent_and_under_user_local_data(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("WORKSPACE_BASE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    first_settings = Settings(_env_file=None)
    monkeypatch.chdir(tmp_path.parent)
    second_settings = Settings(_env_file=None)

    expected = Path.home() / ".local" / "share" / "guranovel" / "workspaces"
    assert first_settings.workspace_base_dir == expected
    assert second_settings.workspace_base_dir == expected
    assert expected.is_relative_to(Path.home())
    assert not expected.is_relative_to(Path(__file__).resolve().parents[2])


def test_async_session_infrastructure_is_constructed_without_connecting() -> None:
    assert engine.url.drivername == "postgresql+asyncpg"

    async def get_session_type() -> type[AsyncSession]:
        session_generator = get_db_session()
        session = await anext(session_generator)
        try:
            return type(session)
        finally:
            await session_generator.aclose()

    assert issubclass(asyncio.run(get_session_type()), AsyncSession)
    assert AsyncSessionLocal.kw["expire_on_commit"] is False


def test_mvp_models_are_imported_and_registered() -> None:
    assert set(Base.metadata.tables) == {
        "users", "projects", "chapters", "workflow_runs", "workflow_checkpoints", "workflow_events",
        "documents", "document_versions", "action_requests", "agent_conversations", "agent_messages", "review_reports",
        "maintenance_changes", "maintenance_affected_items",
        "reader_panel_sessions", "reader_runs", "reader_initial_reports", "reader_panel_issues",
        "reader_panel_ballots", "reader_panel_messages",
    }
    assert Project.__table__.c.metadata.name == "metadata"
    assert "metadata" not in Project.__dict__
    assert Document.__table__.c.current_version_id.foreign_keys
    assert ActionRequest.__table__.c.options.default.is_callable
    assert ReviewReport.__table__.c.blocking_issues.default.is_callable
    assert User.__table__.c.id.type.as_uuid is True
    assert WorkflowRun.__table__.c.started_at.type.timezone is True
    assert MaintenanceChange.__table__.c.workflow_run_id.nullable is False
    assert MaintenanceAffectedItem.__table__.c.position.nullable is False
    assert ReaderPanelSession.__table__.c.source_hash.nullable is False
    assert ReaderRun.__table__.c.reader_profile_id.nullable is False
    assert ReaderInitialReport.__table__.c.reader_run_id.nullable is False
    assert ReaderPanelIssue.__table__.c.issue_number.nullable is False
    assert ReaderPanelBallot.__table__.c.phase.nullable is False
    assert ReaderPanelMessage.__table__.c.speaker_type.nullable is False
    configure_mappers()


def test_reader_panel_model_constraint_and_index_names_match_migration_contract() -> None:
    session_constraints = {
        c.name for c in ReaderPanelSession.__table__.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))
    }
    run_constraints = {
        c.name for c in ReaderRun.__table__.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))
    }
    report_constraints = {
        c.name for c in ReaderInitialReport.__table__.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))
    }
    issue_constraints = {
        c.name for c in ReaderPanelIssue.__table__.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))
    }
    ballot_constraints = {
        c.name for c in ReaderPanelBallot.__table__.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))
    }
    message_constraints = {
        c.name for c in ReaderPanelMessage.__table__.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))
    }

    assert "uq_reader_panel_sessions_run_id" in session_constraints
    assert "ck_reader_panel_sessions_mode" in session_constraints
    assert "ck_reader_panel_sessions_source_hash_sha256" in session_constraints
    assert "uq_reader_runs_session_profile" in run_constraints
    assert "uq_reader_initial_reports_run_id" in report_constraints
    assert "uq_reader_panel_issues_session_number" in issue_constraints
    assert "uq_reader_panel_ballots_run_issue_phase" in ballot_constraints
    assert "uq_reader_panel_messages_issue_round_turn" in message_constraints


def test_maintenance_model_constraint_and_index_names_match_migration_contract() -> None:
    change_constraints = {
        constraint.name
        for constraint in MaintenanceChange.__table__.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }
    item_constraints = {
        constraint.name
        for constraint in MaintenanceAffectedItem.__table__.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }
    change_indexes = {
        index.name for index in MaintenanceChange.__table__.indexes if isinstance(index, Index)
    }
    item_indexes = {
        index.name
        for index in MaintenanceAffectedItem.__table__.indexes
        if isinstance(index, Index)
    }

    assert change_constraints == {
        "uq_maintenance_changes_workflow_run_id",
        "ck_maintenance_changes_title_nonblank",
        "ck_maintenance_changes_title_length",
        "ck_maintenance_changes_request_nonblank",
        "ck_maintenance_changes_request_length",
        "ck_maintenance_changes_status",
        "ck_maintenance_changes_postapply_timestamp",
        "ck_maintenance_changes_preapply_timestamp",
        "ck_maintenance_changes_early_plan",
        "ck_maintenance_changes_late_plan",
        "ck_maintenance_changes_metadata_object",
        "ck_maintenance_changes_metadata_size",
    }
    assert item_constraints == {
        "uq_maintenance_affected_items_position",
        "uq_maintenance_affected_items_reference",
        "ck_maintenance_affected_items_position",
        "ck_maintenance_affected_items_type",
        "ck_maintenance_affected_items_impact",
        "ck_maintenance_affected_items_reference_nonblank",
        "ck_maintenance_affected_items_reference_length",
        "ck_maintenance_affected_items_reason_nonblank",
        "ck_maintenance_affected_items_reason_length",
    }
    assert change_indexes == {
        "idx_maintenance_changes_project_id",
        "idx_maintenance_changes_project_status",
        "idx_maintenance_changes_created_at",
    }
    assert item_indexes == {
        "idx_maintenance_affected_items_change_id",
        "idx_maintenance_affected_items_document_id",
        "idx_maintenance_affected_items_chapter_id",
    }
