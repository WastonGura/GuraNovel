from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.testing import get_test_database_url
import app.models  # noqa: F401  # Register tables used by the cleanup query.

BACKEND_DIR = Path(__file__).resolve().parents[2]


def apply_migrations(database_url: str) -> None:
    """Upgrade the isolated database through the application's Alembic environment."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=os.environ | {
            "DATABASE_URL": database_url,
            "PGOPTIONS": "-c lock_timeout=5000 -c statement_timeout=15000",
        },
        check=True,
        timeout=60,
    )



async def clean_test_data(engine_or_url: AsyncEngine | str) -> None:
    """Remove all mapped application data from the already-validated test database."""
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if isinstance(engine_or_url, str):
        engine = create_async_engine(
            engine_or_url,
            pool_pre_ping=True,
            connect_args={"server_settings": {"lock_timeout": "5000", "statement_timeout": "15000"}},
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
        finally:
            await engine.dispose()
    else:
        async with engine_or_url.begin() as connection:
            await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    database_url = get_test_database_url()
    apply_migrations(database_url)
    return database_url


@pytest.fixture
async def async_session(integration_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        integration_database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"lock_timeout": "5000", "statement_timeout": "15000"}},
    )
    await clean_test_data(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
            await session.rollback()
    finally:
        try:
            await clean_test_data(engine)
        finally:
            await engine.dispose()


@pytest.fixture(autouse=True)
def mock_markdown_store_on_non_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        from app.workspace.markdown_store import MarkdownStore

        def _init(self: MarkdownStore, root: Path) -> None:
            self.root = Path(root).resolve()

        def _read(self: MarkdownStore, relative_path: str) -> str:
            p = self.root / relative_path
            if not p.is_file():
                raise FileNotFoundError(f"{relative_path} does not exist")
            return p.read_text(encoding="utf-8")

        def _read_bounded(self: MarkdownStore, relative_path: str, *, max_bytes: int) -> str:
            content = _read(self, relative_path)
            raw = content.encode("utf-8")
            if len(raw) > max_bytes:
                from app.workspace.markdown_store import OversizedDocumentError

                raise OversizedDocumentError("Document exceeds maximum allowed size")
            return content

        def _write(self: MarkdownStore, relative_path: str, content: str) -> None:
            p = self.root / relative_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        def _exists(self: MarkdownStore, relative_path: str) -> bool:
            return (self.root / relative_path).is_file()

        def _delete(self: MarkdownStore, relative_path: str) -> None:
            p = self.root / relative_path
            if p.is_file():
                p.unlink()

        monkeypatch.setattr(MarkdownStore, "__init__", _init)
        monkeypatch.setattr(MarkdownStore, "read", _read)
        monkeypatch.setattr(MarkdownStore, "read_bounded", _read_bounded)
        monkeypatch.setattr(MarkdownStore, "write", _write)
        monkeypatch.setattr(MarkdownStore, "exists", _exists)
        monkeypatch.setattr(MarkdownStore, "delete", _delete)

        from app.workspace.project_workspace import ProjectWorkspace

        def _pw_init(self: ProjectWorkspace, workspace_base_dir: Path) -> None:
            self.workspace_base_dir = Path(os.path.abspath(workspace_base_dir))

        def _pw_create(self: ProjectWorkspace, slug: str) -> Path:
            root = self.root_for(slug)
            for directory in self._STANDARD_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            return root

        def _pw_remove(self: ProjectWorkspace, slug: str) -> bool:
            import shutil

            root = self.root_for(slug)
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
                return True
            return False

        monkeypatch.setattr(ProjectWorkspace, "__init__", _pw_init)
        monkeypatch.setattr(ProjectWorkspace, "create", _pw_create)
        monkeypatch.setattr(ProjectWorkspace, "remove_new_empty", _pw_remove)



