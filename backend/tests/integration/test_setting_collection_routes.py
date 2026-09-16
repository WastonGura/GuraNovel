from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.main import create_app
from app.models import User


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


@pytest.mark.integration
@pytest.mark.anyio
async def test_setting_collection_crud_and_archive(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "creator")
    headers = {"x-actor-user-id": str(user.id)}

    # 1. Create setting collection
    create_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={
            "title": "Aethelgard World Lore",
            "description": "Comprehensive fantasy setting collection",
        },
    )
    assert create_resp.status_code == 201
    collection = create_resp.json()
    collection_id = collection["id"]
    assert collection["title"] == "Aethelgard World Lore"
    assert collection["description"] == "Comprehensive fantasy setting collection"
    assert collection["status"] == "active"
    assert collection["revision"] == 1
    assert collection["owner_id"] == str(user.id)
    assert "slug" in collection
    assert "workspace_root" in collection

    # 2. Get setting collection
    get_resp = await client.get(f"/api/v1/setting-collections/{collection_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == collection_id

    # 3. List setting collections
    list_resp = await client.get("/api/v1/setting-collections", headers=headers)
    assert list_resp.status_code == 200
    collections = list_resp.json()
    assert len(collections) == 1
    assert collections[0]["id"] == collection_id

    # 4. Update setting collection
    update_resp = await client.patch(
        f"/api/v1/setting-collections/{collection_id}",
        headers=headers,
        json={"title": "Aethelgard Lore (Revised)", "description": "Updated lore"},
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["title"] == "Aethelgard Lore (Revised)"
    assert updated["description"] == "Updated lore"

    # 5. Archive setting collection
    archive_resp = await client.post(
        f"/api/v1/setting-collections/{collection_id}/archive",
        headers=headers,
    )
    assert archive_resp.status_code == 200
    archived = archive_resp.json()
    assert archived["status"] == "archived"

    # 6. Filter by status
    active_resp = await client.get("/api/v1/setting-collections?status=active", headers=headers)
    assert active_resp.status_code == 200
    assert len(active_resp.json()) == 0

    archived_resp = await client.get("/api/v1/setting-collections?status=archived", headers=headers)
    assert archived_resp.status_code == 200
    assert len(archived_resp.json()) == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_novels_sharing_setting_collection(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "novelist")
    headers = {"x-actor-user-id": str(user.id)}

    # Create shared setting collection
    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Shared Lore Cosmos"},
    )
    assert sc_resp.status_code == 201
    sc_id = sc_resp.json()["id"]

    # Create Novel 1 bound to setting collection
    n1_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"novel-one-{uuid4().hex[:6]}",
            "title": "Novel Book One",
            "setting_collection_id": sc_id,
        },
    )
    assert n1_resp.status_code == 201
    n1_data = n1_resp.json()
    assert n1_data["setting_collection_id"] == sc_id

    # Create Novel 2 bound to same setting collection
    n2_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"novel-two-{uuid4().hex[:6]}",
            "title": "Novel Book Two",
            "setting_collection_id": sc_id,
        },
    )
    assert n2_resp.status_code == 201
    n2_data = n2_resp.json()
    assert n2_data["setting_collection_id"] == sc_id

    # Verify both projects are listed under the setting collection
    projects_resp = await client.get(
        f"/api/v1/setting-collections/{sc_id}/projects",
        headers=headers,
    )
    assert projects_resp.status_code == 200
    bound_projects = projects_resp.json()
    assert len(bound_projects) == 2
    bound_ids = {p["id"] for p in bound_projects}
    assert n1_data["id"] in bound_ids
    assert n2_data["id"] in bound_ids

    # Unbinding Novel 1 to None must return 409 Conflict (novels require a setting collection)
    null_patch_resp = await client.patch(
        f"/api/v1/projects/{n1_data['id']}",
        headers=headers,
        json={"setting_collection_id": None},
    )
    assert null_patch_resp.status_code == 409

    # Rebind Novel 1 to a new setting collection via PATCH /projects/{id}
    sc2_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Second Cosmos"},
    )
    assert sc2_resp.status_code == 201
    sc2_id = sc2_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/projects/{n1_data['id']}",
        headers=headers,
        json={"setting_collection_id": sc2_id},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["setting_collection_id"] == sc2_id

    # Verify only Novel 2 is now bound to sc_id
    projects_after = await client.get(
        f"/api/v1/setting-collections/{sc_id}/projects",
        headers=headers,
    )
    assert len(projects_after.json()) == 1
    assert projects_after.json()[0]["id"] == n2_data["id"]

    # Verify Novel 1 is now bound to sc2_id
    sc2_projects = await client.get(
        f"/api/v1/setting-collections/{sc2_id}/projects",
        headers=headers,
    )
    assert len(sc2_projects.json()) == 1
    assert sc2_projects.json()[0]["id"] == n1_data["id"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_setting_document_versioning_immutability_and_restore(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "lorekeeper")
    headers = {"x-actor-user-id": str(user.id)}

    # Create setting collection
    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Versioned Universe"},
    )
    sc_id = sc_resp.json()["id"]
    assert sc_resp.json()["revision"] == 1

    # 1. Create a versioned setting document
    doc_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "world_overview",
            "title": "Cosmology & Realms",
            "path": "world/overview.md",
            "content": "# Original Realms\n\nThe Nine Realms are divided into 3 tiers.",
        },
    )
    assert doc_resp.status_code == 201
    doc_data = doc_resp.json()
    doc_id = doc_data["id"]
    v1_id = doc_data["current_version_id"]
    assert doc_data["setting_collection_id"] == sc_id
    assert doc_data["type"] == "world_overview"

    # Revision should increment to 2
    sc_check = await client.get(f"/api/v1/setting-collections/{sc_id}", headers=headers)
    assert sc_check.json()["revision"] == 2

    # 2. Read document current content
    content_resp = await client.get(f"/api/v1/documents/{doc_id}/content", headers=headers)
    assert content_resp.status_code == 200
    assert content_resp.json()["content"] == "# Original Realms\n\nThe Nine Realms are divided into 3 tiers."
    assert content_resp.json()["version_id"] == v1_id

    # 3. Write version 2
    write_resp = await client.put(
        f"/api/v1/documents/{doc_id}/content",
        headers=headers,
        json={
            "content": "# Updated Realms\n\nThe Ten Realms are divided into 4 tiers.",
            "expected_current_version_id": v1_id,
            "change_summary": "Added tenth realm",
        },
    )
    assert write_resp.status_code == 200
    v2_id = write_resp.json()["id"]
    assert v2_id != v1_id

    # Revision should increment to 3
    sc_check = await client.get(f"/api/v1/setting-collections/{sc_id}", headers=headers)
    assert sc_check.json()["revision"] == 3

    # Verify version history has 2 versions
    versions_resp = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=headers)
    assert versions_resp.status_code == 200
    versions = versions_resp.json()
    assert len(versions) == 2
    assert versions[0]["version_number"] == 1
    assert versions[1]["version_number"] == 2

    # Verify historical version 1 is immutable and can be read independently
    v1_read = await client.get(f"/api/v1/documents/{doc_id}/versions/{v1_id}/content", headers=headers)
    assert v1_read.status_code == 200
    assert v1_read.json()["content"] == "# Original Realms\n\nThe Nine Realms are divided into 3 tiers."

    # 4. Restore version 1 -> creates version 3
    restore_resp = await client.post(
        f"/api/v1/documents/{doc_id}/versions/{v1_id}/restore",
        headers=headers,
        json={
            "expected_current_version_id": v2_id,
        },
    )
    assert restore_resp.status_code == 200
    v3_id = restore_resp.json()["id"]
    assert v3_id not in (v1_id, v2_id)

    # Revision should increment to 4
    sc_check = await client.get(f"/api/v1/setting-collections/{sc_id}", headers=headers)
    assert sc_check.json()["revision"] == 4

    # Verify current content now matches original version 1 content
    current_resp = await client.get(f"/api/v1/documents/{doc_id}/content", headers=headers)
    assert current_resp.json()["content"] == "# Original Realms\n\nThe Nine Realms are divided into 3 tiers."
    assert current_resp.json()["version_id"] == v3_id

    # Verify version list has 3 versions
    v_list = await client.get(f"/api/v1/documents/{doc_id}/versions", headers=headers)
    assert len(v_list.json()) == 3


@pytest.mark.integration
@pytest.mark.anyio
async def test_optimistic_concurrency_conflict(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "concurrency")
    headers = {"x-actor-user-id": str(user.id)}

    # Create collection and document
    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Concurrency Test"},
    )
    sc_id = sc_resp.json()["id"]

    doc_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "character_profile",
            "title": "Hero Profile",
            "path": "characters/hero.md",
            "content": "Hero Level 1",
        },
    )
    doc_id = doc_resp.json()["id"]
    v1_id = doc_resp.json()["current_version_id"]

    # First update succeeds
    await client.put(
        f"/api/v1/documents/{doc_id}/content",
        headers=headers,
        json={
            "content": "Hero Level 2",
            "expected_current_version_id": v1_id,
        },
    )

    # Second update using stale v1_id must return 409 Conflict
    conflict_resp = await client.put(
        f"/api/v1/documents/{doc_id}/content",
        headers=headers,
        json={
            "content": "Hero Level 99 - Concurrent Overwrite",
            "expected_current_version_id": v1_id,
        },
    )
    assert conflict_resp.status_code == 409
    error_payload = conflict_resp.json()
    assert "error" in error_payload
    assert error_payload["error"]["code"] in ("conflict", "document_version_conflict")

    # Verify content was NOT overwritten
    current = await client.get(f"/api/v1/documents/{doc_id}/content", headers=headers)
    assert current.json()["content"] == "Hero Level 2"


@pytest.mark.integration
@pytest.mark.anyio
async def test_archived_setting_collection_rejects_modifications(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "archiver")
    headers = {"x-actor-user-id": str(user.id)}

    # Create collection and document before archive
    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Archived Universe"},
    )
    sc_id = sc_resp.json()["id"]

    doc_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "power_system",
            "title": "Magic System",
            "path": "magic/rules.md",
            "content": "Mana rules",
        },
    )
    doc_id = doc_resp.json()["id"]
    v1_id = doc_resp.json()["current_version_id"]

    # Archive collection
    await client.post(f"/api/v1/setting-collections/{sc_id}/archive", headers=headers)

    # 1. Attempt to create new document in archived collection -> 409 Conflict
    create_fail = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "factions",
            "title": "Guilds",
            "path": "factions/guilds.md",
            "content": "Guild rules",
        },
    )
    assert create_fail.status_code == 409
    assert create_fail.json()["error"]["code"] == "conflict"

    # 2. Attempt to write to existing document in archived collection -> 409 Conflict
    write_fail = await client.put(
        f"/api/v1/documents/{doc_id}/content",
        headers=headers,
        json={
            "content": "Forbidden edit",
            "expected_current_version_id": v1_id,
        },
    )
    assert write_fail.status_code == 409
    assert write_fail.json()["error"]["code"] == "conflict"

    # 3. Attempt to restore -> 409 Conflict
    restore_fail = await client.post(
        f"/api/v1/documents/{doc_id}/versions/{v1_id}/restore",
        headers=headers,
        json={
            "expected_current_version_id": v1_id,
        },
    )
    assert restore_fail.status_code == 409
    assert restore_fail.json()["error"]["code"] == "conflict"

    # Documents in archived collections CAN still be read
    read_resp = await client.get(f"/api/v1/documents/{doc_id}/content", headers=headers)
    assert read_resp.status_code == 200
    assert read_resp.json()["content"] == "Mana rules"


@pytest.mark.integration
@pytest.mark.anyio
async def test_cross_user_forbidden_access(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    owner = await create_test_user(async_session, "owner")
    attacker = await create_test_user(async_session, "attacker")

    owner_headers = {"x-actor-user-id": str(owner.id)}
    attacker_headers = {"x-actor-user-id": str(attacker.id)}

    # Owner creates collection and document
    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=owner_headers,
        json={"title": "Private Collection"},
    )
    sc_id = sc_resp.json()["id"]

    doc_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=owner_headers,
        json={
            "type": "geography",
            "title": "Secret Realm",
            "path": "geo/secret.md",
            "content": "Classified map",
        },
    )
    doc_id = doc_resp.json()["id"]
    v1_id = doc_resp.json()["current_version_id"]

    # Attacker tries to update collection metadata -> 403 Forbidden
    update_fail = await client.patch(
        f"/api/v1/setting-collections/{sc_id}",
        headers=attacker_headers,
        json={"title": "Hacked Title"},
    )
    assert update_fail.status_code == 403

    # Attacker tries to archive collection -> 403 Forbidden
    archive_fail = await client.post(
        f"/api/v1/setting-collections/{sc_id}/archive",
        headers=attacker_headers,
    )
    assert archive_fail.status_code == 403

    # Attacker tries to create document under collection -> 403 Forbidden
    create_doc_fail = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=attacker_headers,
        json={
            "type": "history",
            "title": "Injected History",
            "path": "history/injected.md",
            "content": "Malicious content",
        },
    )
    assert create_doc_fail.status_code == 403

    # Attacker tries to modify existing document -> 403 Forbidden
    write_fail = await client.put(
        f"/api/v1/documents/{doc_id}/content",
        headers=attacker_headers,
        json={
            "content": "Hacked content",
            "expected_current_version_id": v1_id,
        },
    )
    assert write_fail.status_code == 403


@pytest.mark.integration
@pytest.mark.anyio
async def test_validation_constraints(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "validator")
    headers = {"x-actor-user-id": str(user.id)}

    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Validation Test"},
    )
    sc_id = sc_resp.json()["id"]

    # 1. Invalid document type for setting collection (e.g. chapter_draft)
    invalid_type = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "chapter_draft",
            "title": "Invalid Draft",
            "path": "drafts/draft1.md",
            "content": "Invalid",
        },
    )
    assert invalid_type.status_code in (409, 422)

    # 2. Duplicate path in same setting collection
    await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "glossary",
            "title": "Glossary",
            "path": "glossary/terms.md",
            "content": "Terms 1",
        },
    )
    dup_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "glossary",
            "title": "Glossary Dup",
            "path": "glossary/terms.md",
            "content": "Terms 2",
        },
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "conflict"
