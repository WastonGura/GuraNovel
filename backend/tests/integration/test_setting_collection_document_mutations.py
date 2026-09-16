from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.main import create_app
from app.models import SettingCollection, User


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def create_test_user(async_session: AsyncSession, prefix: str = "user") -> User:
    user = User(
        username=f"{prefix}-{uuid4().hex[:8]}",
        display_name=f"Test {prefix.capitalize()}",
    )
    async_session.add(user)
    await async_session.commit()
    return user


async def create_setting_collection(
    async_session: AsyncSession, tmp_path: Path, owner_id=None
) -> SettingCollection:
    col = SettingCollection(
        slug=f"col-{uuid4().hex[:8]}",
        title="Test Collection",
        workspace_root=str(tmp_path),
        status="active",
        revision=1,
        owner_id=owner_id,
    )
    async_session.add(col)
    await async_session.commit()
    await async_session.refresh(col)
    return col


@pytest.mark.integration
@pytest.mark.anyio
async def test_patch_and_delete_setting_collection_document(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    owner = await create_test_user(async_session, "owner")
    owner_id = owner.id
    col = await create_setting_collection(async_session, tmp_path, owner_id=owner_id)

    # 1. Create a setting document
    create_res = await client.post(
        f"/api/v1/setting-collections/{col.id}/documents",
        headers={"x-actor-user-id": str(owner_id)},
        json={
            "type": "character_profile",
            "title": "Old Character Name",
            "path": "characters/char1.md",
            "content": "# Character Profile\nOriginal content",
        },
    )
    assert create_res.status_code == 201, create_res.text
    doc_data = create_res.json()
    doc_id = doc_data["id"]
    assert doc_data["title"] == "Old Character Name"

    await async_session.refresh(col)
    rev_after_create = col.revision

    # 2. Patch document title and metadata
    patch_res = await client.patch(
        f"/api/v1/documents/{doc_id}",
        headers={"x-actor-user-id": str(owner_id)},
        json={
            "title": "New Character Name",
            "metadata": {"category": "Protagonist"},
        },
    )
    assert patch_res.status_code == 200, patch_res.text
    patched = patch_res.json()
    assert patched["title"] == "New Character Name"
    assert patched["metadata"]["category"] == "Protagonist"

    await async_session.refresh(col)
    assert col.revision > rev_after_create

    # 3. Forbidden for unauthorized user
    other_user = await create_test_user(async_session, "other")
    unauth_patch = await client.patch(
        f"/api/v1/documents/{doc_id}",
        headers={"x-actor-user-id": str(other_user.id)},
        json={"title": "Hacked Title"},
    )
    assert unauth_patch.status_code == 403

    # 4. Delete document
    del_res = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"x-actor-user-id": str(owner_id)},
    )
    assert del_res.status_code == 204

    # Verify document is gone
    get_res = await client.get(f"/api/v1/documents/{doc_id}")
    assert get_res.status_code == 404


@pytest.mark.integration
@pytest.mark.anyio
async def test_archived_collection_rejects_document_patch_and_delete(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    owner = await create_test_user(async_session, "archivedowner")
    owner_id = owner.id
    col = await create_setting_collection(async_session, tmp_path, owner_id=owner_id)

    create_res = await client.post(
        f"/api/v1/setting-collections/{col.id}/documents",
        headers={"x-actor-user-id": str(owner_id)},
        json={
            "type": "world_overview",
            "title": "Ancient Magic",
            "path": "lore/ancient-magic.md",
            "content": "Secret lore",
        },
    )
    assert create_res.status_code == 201
    doc_id = create_res.json()["id"]

    # Archive the collection
    archive_res = await client.post(
        f"/api/v1/setting-collections/{col.id}/archive",
        headers={"x-actor-user-id": str(owner_id)},
    )
    assert archive_res.status_code == 200

    # Attempt to patch document
    patch_res = await client.patch(
        f"/api/v1/documents/{doc_id}",
        headers={"x-actor-user-id": str(owner_id)},
        json={"title": "Modified Ancient Magic"},
    )
    assert patch_res.status_code == 409
    assert "Archived" in patch_res.json()["error"]["message"]

    # Attempt to delete document
    del_res = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"x-actor-user-id": str(owner_id)},
    )
    assert del_res.status_code == 409
    assert "Archived" in del_res.json()["error"]["message"]

