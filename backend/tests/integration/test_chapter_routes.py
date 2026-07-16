from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db_session
from app.main import create_app
from app.models import Chapter, Project


@pytest.fixture
async def chapter_client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def create_project(async_session: AsyncSession, slug: str = "chapter-test") -> Project:
    project = Project(slug=slug, title="Chapter Test", workspace_root="/unused")
    async_session.add(project)
    await async_session.commit()
    return project


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_chapter_assigns_defaults_and_serializes_metadata(
    chapter_client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    project = await create_project(async_session)

    response = await chapter_client.post(
        f"/api/v1/projects/{project.id}/chapters",
        json={"title": "Arrival", "metadata": {"pov": "Mina"}},
    )

    assert response.status_code == 201
    assert response.json()["project_id"] == str(project.id)
    assert response.json()["chapter_number"] == 1
    assert response.json()["title"] == "Arrival"
    assert response.json()["status"] == "OUTLINE_DISCUSSION"
    assert response.json()["metadata"] == {"pov": "Mina"}

    defaulted = await chapter_client.post(f"/api/v1/projects/{project.id}/chapters", json={})

    assert defaulted.status_code == 201
    assert defaulted.json()["chapter_number"] == 2
    assert defaulted.json()["title"] is None
    assert defaulted.json()["metadata"] == {}


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_and_list_chapters_are_project_scoped_and_number_ordered(
    chapter_client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    project = await create_project(async_session, "first-project")
    other_project = await create_project(async_session, "second-project")
    later = Chapter(project_id=project.id, chapter_number=2, title="Later")
    first = Chapter(project_id=project.id, chapter_number=1, title="First")
    foreign = Chapter(project_id=other_project.id, chapter_number=1, title="Private")
    async_session.add_all([later, first, foreign])
    await async_session.commit()

    listed = await chapter_client.get(f"/api/v1/projects/{project.id}/chapters")
    fetched = await chapter_client.get(f"/api/v1/projects/{project.id}/chapters/{first.id}")
    hidden = await chapter_client.get(f"/api/v1/projects/{project.id}/chapters/{foreign.id}")
    missing_project = await chapter_client.get(f"/api/v1/projects/{uuid4()}/chapters")

    assert listed.status_code == 200
    assert [chapter["id"] for chapter in listed.json()] == [str(first.id), str(later.id)]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == str(first.id)
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "not_found"
    assert missing_project.status_code == 404
    assert missing_project.json()["error"]["code"] == "not_found"


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_chapter_rejects_mass_assignment_and_unknown_fields(
    chapter_client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    project = await create_project(async_session)
    forbidden_payloads = (
        {"project_id": str(uuid4())},
        {"chapter_number": 9},
        {"status": "COMPLETE"},
        {"current_outline_document_id": str(uuid4())},
        {"current_draft_document_id": str(uuid4())},
        {"final_document_id": str(uuid4())},
        {"summary_document_id": str(uuid4())},
        {"workspace_root": "/tmp/nope"},
        {"unexpected": True},
    )

    for payload in forbidden_payloads:
        response = await chapter_client.post(f"/api/v1/projects/{project.id}/chapters", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_chapter_for_unknown_project_returns_not_found(
    chapter_client: httpx.AsyncClient,
) -> None:
    response = await chapter_client.post(f"/api/v1/projects/{uuid4()}/chapters", json={})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_creates_allocate_contiguous_numbers_per_project(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    project = await create_project(async_session)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as request_session:
            yield request_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        f"/api/v1/projects/{project.id}/chapters", json={"title": f"Chapter {index}"}
                    )
                    for index in range(12)
                )
            )

        assert all(response.status_code == 201 for response in responses)
        assert sorted(response.json()["chapter_number"] for response in responses) == list(range(1, 13))
        async with session_factory() as check_session:
            chapter_numbers = list(
                await check_session.scalars(
                    select(Chapter.chapter_number)
                    .where(Chapter.project_id == project.id)
                    .order_by(Chapter.chapter_number)
                )
            )
        assert chapter_numbers == list(range(1, 13))
    finally:
        await engine.dispose()
