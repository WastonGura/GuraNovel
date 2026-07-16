import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine


def test_database_url_can_be_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:password@db:5432/novels")

    configured_settings = Settings(_env_file=None)

    assert configured_settings.database_url == "postgresql+asyncpg://user:password@db:5432/novels"


def test_async_session_infrastructure_is_constructed_without_connecting() -> None:
    assert Base.metadata.tables == {}
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
