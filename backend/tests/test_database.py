import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import configure_mappers

from app.api.deps import get_db_session
from app.core.config import Settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models import ActionRequest, Document, Project, ReviewReport, User, WorkflowRun


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
    }
    assert Project.__table__.c.metadata.name == "metadata"
    assert "metadata" not in Project.__dict__
    assert Document.__table__.c.current_version_id.foreign_keys
    assert ActionRequest.__table__.c.options.default.is_callable
    assert ReviewReport.__table__.c.blocking_issues.default.is_callable
    assert User.__table__.c.id.type.as_uuid is True
    assert WorkflowRun.__table__.c.started_at.type.timezone is True
    configure_mappers()
