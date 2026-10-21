"""Durable trash API guarantees used by both undo and the trash browser."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.main import create_app
from app.models import SettingCollection, User
from app.services.setting_document_trash import set_setting_document_deleted

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def database_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = database_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as value:
        yield value


async def collection(async_session: AsyncSession, root: Path) -> tuple[SettingCollection, dict]:
    root.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"trash-{uuid4().hex}", display_name="Trash author")
    async_session.add(owner)
    await async_session.flush()
    value = SettingCollection(
        slug=f"trash-{uuid4().hex}", title="Trash collection", workspace_root=str(root),
        owner_id=owner.id, status="active", revision=1,
    )
    async_session.add(value)
    await async_session.commit()
    return value, {"x-actor-user-id": str(owner.id)}


async def document(client: httpx.AsyncClient, value: SettingCollection, headers: dict) -> dict:
    response = await client.post(
        f"/api/v1/setting-collections/{value.id}/documents", headers=headers,
        json={"type": "world_overview", "title": "旧街", "path": "world/street.md",
              "content": "原来的旧街，通往 [[港口]]。", "metadata": {"category": "world"}},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_response_loss_retries_keep_deletion_time_identity_versions_and_revision(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
) -> None:
    value, headers = await collection(async_session, tmp_path)
    doc = await document(client, value, headers)
    url = f"/api/v1/documents/{doc['id']}"
    versions = (await client.get(f"{url}/versions")).json()
    await async_session.refresh(value)
    initial_revision = value.revision
    before = datetime.now(UTC)
    assert (await client.delete(url, headers=headers)).status_code == 204
    first = (await client.get(
        f"/api/v1/setting-collections/{value.id}/documents?deleted=true", headers=headers
    )).json()
    assert [entry["id"] for entry in first] == [doc["id"]]
    assert before <= datetime.fromisoformat(first[0]["deleted_at"].replace("Z", "+00:00"))
    assert first[0]["current_version_id"] == doc["current_version_id"]
    assert first[0]["metadata"]["category"] == "world"
    assert (await client.delete(url, headers=headers)).status_code == 204
    retry = (await client.get(
        f"/api/v1/setting-collections/{value.id}/documents?deleted=true", headers=headers
    )).json()
    assert retry[0]["deleted_at"] == first[0]["deleted_at"]
    await async_session.refresh(value)
    assert value.revision == initial_revision + 1
    for _ in range(2):
        restored = await client.post(f"{url}/undelete", headers=headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()["id"] == doc["id"]
        assert restored.json()["current_version_id"] == doc["current_version_id"]
        assert restored.json()["deleted_at"] is None
    await async_session.refresh(value)
    assert value.revision == initial_revision + 2
    assert (await client.get(f"{url}/versions")).json() == versions
    assert (await client.get(f"{url}/content")).json()["content"] == "原来的旧街，通往 [[港口]]。"


async def test_cross_collection_partial_restore_and_archived_trash_remain_truthful(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
) -> None:
    first, first_headers = await collection(async_session, tmp_path / "first")
    second, second_headers = await collection(async_session, tmp_path / "second")
    own = await document(client, first, first_headers)
    other = await document(client, second, second_headers)
    for doc, headers in [(own, first_headers), (other, second_headers)]:
        assert (await client.delete(f"/api/v1/documents/{doc['id']}", headers=headers)).status_code == 204
    own_list = await client.get(
        f"/api/v1/setting-collections/{first.id}/documents?deleted=true", headers=first_headers
    )
    assert [entry["id"] for entry in own_list.json()] == [own["id"]]
    assert (await client.get(
        f"/api/v1/setting-collections/{second.id}/documents?deleted=true", headers=first_headers
    )).status_code == 403
    restored = await client.post(f"/api/v1/documents/{own['id']}/undelete", headers=first_headers)
    rejected = await client.post(f"/api/v1/documents/{other['id']}/undelete", headers=first_headers)
    assert [restored.status_code, rejected.status_code] == [200, 403]
    assert (await client.post(
        f"/api/v1/setting-collections/{second.id}/archive", headers=second_headers
    )).status_code == 200
    archived_list = await client.get(
        f"/api/v1/setting-collections/{second.id}/documents?deleted=true", headers=second_headers
    )
    assert archived_list.status_code == 200
    assert [entry["id"] for entry in archived_list.json()] == [other["id"]]
    assert (await client.post(
        f"/api/v1/documents/{other['id']}/undelete", headers=second_headers
    )).status_code == 409
    assert (await client.get(f"/api/v1/documents/{other['id']}")).status_code == 404


async def test_concurrent_restore_only_restores_once_without_a_new_content_version(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
) -> None:
    value, headers = await collection(async_session, tmp_path)
    doc = await document(client, value, headers)
    url = f"/api/v1/documents/{doc['id']}"
    assert (await client.delete(url, headers=headers)).status_code == 204
    await async_session.refresh(value)
    deleted_revision = value.revision
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def restore() -> tuple[UUID, UUID | None]:
        async with factory() as session:
            result = await set_setting_document_deleted(
                session, UUID(doc["id"]), deleted=False, actor_id=value.owner_id
            )
            return result.id, result.current_version_id

    results = await asyncio.gather(restore(), restore(), restore())
    assert results == [(UUID(doc["id"]), UUID(doc["current_version_id"]))] * 3
    await async_session.refresh(value)
    assert value.revision == deleted_revision + 1
    versions = (await client.get(f"{url}/versions")).json()
    assert len(versions) == 1
    assert versions[0]["id"] == doc["current_version_id"]
