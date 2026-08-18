from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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



async def clean_test_data(database_url: str) -> None:
    """Remove all mapped application data from the already-validated test database."""
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"lock_timeout": "5000", "statement_timeout": "15000"}},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    database_url = get_test_database_url()
    apply_migrations(database_url)
    return database_url


@pytest.fixture
async def async_session(integration_database_url: str) -> AsyncIterator[AsyncSession]:
    await clean_test_data(integration_database_url)
    engine = create_async_engine(
        integration_database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"lock_timeout": "5000", "statement_timeout": "15000"}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()
        await clean_test_data(integration_database_url)

