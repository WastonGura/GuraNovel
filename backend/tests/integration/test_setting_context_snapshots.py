"""Integration tests for setting context snapshot resolution, verification, and evolution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.errors import WorkflowStateError
from app.main import create_app
from app.models import User
from app.services.setting_context_resolver import (
    SettingContextResolver,
    SettingDocumentSnapshot,
)


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def create_test_user(async_session: AsyncSession, prefix: str = "author") -> User:
    user = User(
        username=f"{prefix}-{uuid4().hex[:8]}",
        display_name=f"Test {prefix.capitalize()}",
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest.mark.integration
@pytest.mark.anyio
async def test_setting_context_snapshot_lifecycle_and_evolution(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "lorekeeper")
    headers = {"x-actor-user-id": str(user.id)}

    # 1. Create Setting Collection
    sc_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={
            "title": "Aethelgard Cosmos",
            "description": "Shared high-fantasy universe",
        },
    )
    assert sc_resp.status_code == 201
    sc_id = UUID(sc_resp.json()["id"])
    assert sc_resp.json()["revision"] == 1

    # 2. Add World Overview document (bumps revision to 2)
    doc1_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "world_overview",
            "title": "Cosmology and Realms",
            "path": "world/overview.md",
            "content": "# Aethelgard Realms\n\nThe realm consists of the Upper and Lower spheres.",
        },
    )
    assert doc1_resp.status_code == 201
    doc1_id = UUID(doc1_resp.json()["id"])
    doc1_v1_id = UUID(doc1_resp.json()["current_version_id"])

    # 3. Add Power System document (bumps revision to 3)
    doc2_resp = await client.post(
        f"/api/v1/setting-collections/{sc_id}/documents",
        headers=headers,
        json={
            "type": "power_system",
            "title": "Arcane Circles",
            "path": "powers/circles.md",
            "content": "# Arcane Circles\n\nMages master up to nine circles of elemental power.",
        },
    )
    assert doc2_resp.status_code == 201
    doc2_id = UUID(doc2_resp.json()["id"])
    doc2_v1_id = UUID(doc2_resp.json()["current_version_id"])

    # Verify collection revision is 3
    col_resp = await client.get(f"/api/v1/setting-collections/{sc_id}", headers=headers)
    assert col_resp.status_code == 200
    assert col_resp.json()["revision"] == 3

    # 4. Create a novel Project bound to this Setting Collection
    proj_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"aethelgard-saga-{uuid4().hex[:6]}",
            "title": "Chronicles of Aethelgard",
            "genre": "fantasy",
            "setting_collection_id": str(sc_id),
        },
    )
    assert proj_resp.status_code == 201
    proj_id = UUID(proj_resp.json()["id"])
    assert proj_resp.json()["setting_collection_id"] == str(sc_id)

    # 5. Resolve Setting Context for Project
    resolver = SettingContextResolver(async_session)
    bundle_rev3 = await resolver.resolve_for_project(proj_id)

    assert bundle_rev3.setting_collection_id == sc_id
    assert bundle_rev3.collection_revision == 3
    assert len(bundle_rev3.documents) == 2
    assert len(bundle_rev3.bundle_hash) == 64

    # Verify writer contexts and review contexts
    writer_contexts = bundle_rev3.as_writer_contexts(proj_id)
    assert len(writer_contexts) == 2
    assert {c.document_id for c in writer_contexts} == {doc1_id, doc2_id}

    # Verify evidence representation
    evidence = bundle_rev3.to_evidence()
    assert evidence["setting_collection_id"] == str(sc_id)
    assert evidence["collection_revision"] == 3
    assert evidence["document_count"] == 2

    # 6. Verify snapshot bundle validation succeeds
    verified = await resolver.verify_snapshot_bundle(proj_id, bundle_rev3)
    assert verified.bundle_hash == bundle_rev3.bundle_hash

    # 7. Author modifies World Overview document to create Revision 4
    v2_resp = await client.put(
        f"/api/v1/documents/{doc1_id}/content",
        headers=headers,
        json={
            "content": "# Aethelgard Multiverse\n\nExpanded cosmology with infinite planes.",
            "expected_current_version_id": str(doc1_v1_id),
            "change_summary": "Expanded cosmology",
        },
    )
    assert v2_resp.status_code == 200
    doc1_v2_id = UUID(v2_resp.json()["id"])

    # Verify collection revision is now 4
    col_resp2 = await client.get(f"/api/v1/setting-collections/{sc_id}", headers=headers)
    assert col_resp2.json()["revision"] == 4

    # 8. Crucial check: Older task snapshots (rev 3) MUST NOT be rejected!
    # Replay/retry of tasks using bundle_rev3 must continue to verify successfully.
    old_task_verified = await resolver.verify_snapshot_bundle(proj_id, bundle_rev3)
    assert old_task_verified.collection_revision == 3
    assert old_task_verified.documents[0].version_id in {doc1_v1_id, doc2_v1_id}

    # Reconstruct from evidence must also succeed and yield rev 3
    restored_bundle = await resolver.load_bundle_from_evidence(proj_id, evidence)
    assert restored_bundle.collection_revision == 3
    assert restored_bundle.bundle_hash == bundle_rev3.bundle_hash

    # 9. New tasks on the project resolve the evolved context (rev 4)
    bundle_rev4 = await resolver.resolve_for_project(proj_id)
    assert bundle_rev4.collection_revision == 4
    assert bundle_rev4.bundle_hash != bundle_rev3.bundle_hash

    # Check that the new version is in bundle_rev4
    world_doc_snap = [d for d in bundle_rev4.documents if d.document_id == doc1_id][0]
    assert world_doc_snap.version_id == doc1_v2_id
    assert "infinite planes" in world_doc_snap.content

    # 10. Tampering checks:
    # Tampering with snapshot content must fail snapshot construction
    with pytest.raises(ValueError, match="content_hash does not match"):
        SettingDocumentSnapshot(
            setting_collection_id=sc_id,
            collection_revision=3,
            document_id=doc1_id,
            version_id=doc1_v1_id,
            document_type="world_overview",
            title="Cosmology",
            path="world/overview.md",
            content="Malicious injected lore",
            content_hash=world_doc_snap.content_hash,  # mismatched!
        )

    # Tampering with dictionary snapshot content submitted to verify_snapshot_bundle
    tampered_payload = bundle_rev3.to_dict()
    tampered_payload["documents"][0]["content"] = "Malicious injected content"
    with pytest.raises(WorkflowStateError):
        await resolver.verify_snapshot_bundle(proj_id, tampered_payload)

    # Tampering with evidence payload (e.g. non-existent version)
    tampered_evidence = bundle_rev3.to_evidence()
    tampered_evidence["documents"][0]["version_id"] = str(uuid4())
    with pytest.raises(WorkflowStateError):
        await resolver.load_bundle_from_evidence(proj_id, tampered_evidence)


@pytest.mark.integration
@pytest.mark.anyio
async def test_cross_collection_snapshot_rejection(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "creator")
    headers = {"x-actor-user-id": str(user.id)}

    # Create two setting collections
    sc1_resp = await client.post(
        "/api/v1/setting-collections", headers=headers, json={"title": "Universe A"}
    )
    sc1_id = UUID(sc1_resp.json()["id"])

    sc2_resp = await client.post(
        "/api/v1/setting-collections", headers=headers, json={"title": "Universe B"}
    )
    sc2_id = UUID(sc2_resp.json()["id"])

    # Add document to collection 2
    doc_resp = await client.post(
        f"/api/v1/setting-collections/{sc2_id}/documents",
        headers=headers,
        json={
            "type": "factions",
            "title": "Factions B",
            "path": "factions/factions.md",
            "content": "Guilds of Universe B",
        },
    )
    assert UUID(doc_resp.json()["id"])

    # Create novel project bound to Collection 1
    proj_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"universe-a-novel-{uuid4().hex[:6]}",
            "title": "Novel In Universe A",
            "setting_collection_id": str(sc1_id),
        },
    )
    proj_id = UUID(proj_resp.json()["id"])

    # Resolve bundle from Collection 2
    resolver = SettingContextResolver(async_session)
    bundle_col2 = await resolver.resolve_for_collection(sc2_id)

    # Trying to verify bundle from Collection 2 against Project bound to Collection 1 MUST FAIL
    with pytest.raises(WorkflowStateError, match="does not match project setting_collection_id"):
        await resolver.verify_snapshot_bundle(proj_id, bundle_col2)
