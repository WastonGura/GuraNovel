import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


@pytest.mark.integration
@pytest.mark.anyio
async def test_postgresql_migrations_support_async_persistence(async_session: AsyncSession) -> None:
    user = User(username="postgres-integration")
    async_session.add(user)
    await async_session.flush()

    assert await async_session.scalar(select(User.username)) == "postgres-integration"
