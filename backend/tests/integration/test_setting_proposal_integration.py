"""Integration tests for SettingChangeProposal HTTP routes, OCC validation, cross-novel impact, and snapshot immutability."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.setting_proposal_contracts import (
    SettingChangeProposal,
    SettingProposalStatus,
)
from app.api.deps import get_db_session
from app.main import create_app
from app.models import (
    User,
)
from app.services.setting_context_resolver import SettingContextResolver


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


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
async def test_apply_proposal_create_new_document(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "creator")
    headers = {"x-actor-user-id": str(user.id)}

    create_col_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "World of Eora", "description": "Lore repository"},
    )
    assert create_col_resp.status_code == 201
    col = create_col_resp.json()
    col_id = col["id"]
    assert col["revision"] == 1

    prop_id = str(uuid4())
    apply_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/proposals/apply",
        headers=headers,
        json={
            "id": prop_id,
            "setting_collection_id": col_id,
            "target_document_id": None,
            "base_version_id": None,
            "title": "Engwithan Ruins",
            "category": "world",
            "proposed_content": "Ancient animancy structures scattering the Eastern Reach.",
            "reason": "Discovered in Chapter 1 intro.",
            "source_task": {
                "agent_role": "lore_agent",
                "novel_title": "Pillars of Eternity",
            },
        },
    )
    assert apply_resp.status_code == 200
    doc_data = apply_resp.json()
    assert doc_data["title"] == "Engwithan Ruins"
    assert doc_data["current_version_id"] is not None

    col_check = await client.get(f"/api/v1/setting-collections/{col_id}", headers=headers)
    assert col_check.status_code == 200
    assert col_check.json()["revision"] == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_apply_proposal_update_existing_document_occ_success_and_conflict(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "author")
    headers = {"x-actor-user-id": str(user.id)}

    create_col_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Cyberpunk 2099", "description": "Futuristic neon city"},
    )
    col_id = create_col_resp.json()["id"]

    create_doc_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/documents",
        headers=headers,
        json={
            "type": "character_profile",
            "title": "Agent Neo",
            "path": "setting/agent-neo.md",
            "content": "A rebellious hacker in District 7.",
        },
    )
    assert create_doc_resp.status_code == 201
    doc = create_doc_resp.json()
    doc_id = doc["id"]
    initial_version_id = doc["current_version_id"]
    assert initial_version_id is not None

    col_check = await client.get(f"/api/v1/setting-collections/{col_id}", headers=headers)
    assert col_check.json()["revision"] == 2

    # 1. OCC Success with matching base_version_id
    apply_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/proposals/apply",
        headers=headers,
        json={
            "id": str(uuid4()),
            "setting_collection_id": col_id,
            "target_document_id": doc_id,
            "base_version_id": initial_version_id,
            "title": "Agent Neo",
            "category": "setting",
            "proposed_content": "A rebellious hacker who recently acquired an encrypted cyberdeck.",
            "reason": "Reflecting equipment upgrade in Chapter 2.",
            "source_task": {"agent_role": "writer_agent"},
        },
    )
    assert apply_resp.status_code == 200
    updated_doc = apply_resp.json()
    new_version_id = updated_doc["current_version_id"]
    assert new_version_id != initial_version_id

    col_check = await client.get(f"/api/v1/setting-collections/{col_id}", headers=headers)
    assert col_check.json()["revision"] == 3

    # 2. OCC Conflict (409) with stale base_version_id
    stale_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/proposals/apply",
        headers=headers,
        json={
            "id": str(uuid4()),
            "setting_collection_id": col_id,
            "target_document_id": doc_id,
            "base_version_id": initial_version_id,
            "title": "Agent Neo",
            "category": "setting",
            "proposed_content": "A rogue AI impersonating Agent Neo.",
            "reason": "Alternate branch attempt.",
            "source_task": {"agent_role": "editor_agent"},
        },
    )
    assert stale_resp.status_code == 409

    doc_content_resp = await client.get(f"/api/v1/documents/{doc_id}/content")
    assert doc_content_resp.status_code == 200
    assert "encrypted cyberdeck" in doc_content_resp.json()["content"]

    col_check2 = await client.get(f"/api/v1/setting-collections/{col_id}", headers=headers)
    assert col_check2.json()["revision"] == 3


@pytest.mark.integration
@pytest.mark.anyio
async def test_rejection_does_not_mutate_setting_documents_or_revision(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "author")
    headers = {"x-actor-user-id": str(user.id)}

    create_col_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Solar Kingdom", "description": "Kingdom lore"},
    )
    col_id = create_col_resp.json()["id"]

    create_doc_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/documents",
        headers=headers,
        json={
            "type": "world_overview",
            "title": "Sun Spire",
            "path": "world/sun-spire.md",
            "content": "The central palace bathed in golden solar radiance.",
        },
    )
    doc_id = create_doc_resp.json()["id"]
    rev_before = (await client.get(f"/api/v1/setting-collections/{col_id}", headers=headers)).json()["revision"]

    rejected_proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=UUID(col_id),
        target_document_id=UUID(doc_id),
        base_version_id=UUID(create_doc_resp.json()["current_version_id"]),
        title="Sun Spire",
        category="world",
        proposed_content="The spire was destroyed by solar flare.",
        reason="Too drastic for current plot.",
        status=SettingProposalStatus.REJECTED,
    )
    assert rejected_proposal.status == SettingProposalStatus.REJECTED

    rev_after = (await client.get(f"/api/v1/setting-collections/{col_id}", headers=headers)).json()["revision"]
    assert rev_after == rev_before

    doc_content = (await client.get(f"/api/v1/documents/{doc_id}/content")).json()["content"]
    assert "bathed in golden solar radiance" in doc_content


@pytest.mark.integration
@pytest.mark.anyio
async def test_cross_novel_impact_visibility(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "lorekeeper")
    headers = {"x-actor-user-id": str(user.id)}

    col_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Shared Multiverse", "description": "Common multiverse lore"},
    )
    col_id = col_resp.json()["id"]

    proj_a_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"novel-alpha-{uuid4().hex[:6]}",
            "title": "Novel Alpha: Awakening",
            "setting_collection_id": col_id,
        },
    )
    assert proj_a_resp.status_code == 201

    proj_b_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"novel-beta-{uuid4().hex[:6]}",
            "title": "Novel Beta: Retribution",
            "setting_collection_id": col_id,
        },
    )
    assert proj_b_resp.status_code == 201

    projects_resp = await client.get(
        f"/api/v1/setting-collections/{col_id}/projects",
        headers=headers,
    )
    assert projects_resp.status_code == 200
    project_list = projects_resp.json()
    titles = [p["title"] for p in project_list]
    assert "Novel Alpha: Awakening" in titles
    assert "Novel Beta: Retribution" in titles
    assert len(project_list) == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_task_snapshot_immutability_and_evolution(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    user = await create_test_user(async_session, "snapshot_author")
    headers = {"x-actor-user-id": str(user.id)}

    col_resp = await client.post(
        "/api/v1/setting-collections",
        headers=headers,
        json={"title": "Timeline Archives", "description": "Temporal constants"},
    )
    col_id = UUID(col_resp.json()["id"])

    doc_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/documents",
        headers=headers,
        json={
            "type": "world_overview",
            "title": "Time Paradox Rule",
            "path": "world/paradox-rule.md",
            "content": "Rule 1: Never cross your own temporal wake.",
        },
    )
    doc_id = UUID(doc_resp.json()["id"])
    initial_ver_id = UUID(doc_resp.json()["current_version_id"])

    proj_resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "slug": f"chronicles-time-{uuid4().hex[:6]}",
            "title": "Chronicles of Time",
            "setting_collection_id": str(col_id),
        },
    )
    project_id = UUID(proj_resp.json()["id"])

    resolver = SettingContextResolver(async_session)
    bundle_task1 = await resolver.resolve_for_project(
        project_id=project_id,
    )
    assert bundle_task1.collection_revision == 2
    assert len(bundle_task1.documents) == 1
    snap_task1 = bundle_task1.documents[0]
    assert snap_task1.version_id == initial_ver_id
    assert snap_task1.content == "Rule 1: Never cross your own temporal wake."

    apply_resp = await client.post(
        f"/api/v1/setting-collections/{col_id}/proposals/apply",
        headers=headers,
        json={
            "id": str(uuid4()),
            "setting_collection_id": str(col_id),
            "target_document_id": str(doc_id),
            "base_version_id": str(initial_ver_id),
            "title": "Time Paradox Rule",
            "category": "world",
            "proposed_content": "Rule 1: Never cross your own temporal wake, unless protected by Chrono Aegis.",
            "reason": "Chrono Aegis invented.",
        },
    )
    assert apply_resp.status_code == 200
    v2_id = UUID(apply_resp.json()["current_version_id"])
    assert v2_id != initial_ver_id

    # Invariant: Task 1 retains original snapshot
    assert bundle_task1.collection_revision == 2
    assert bundle_task1.documents[0].version_id == initial_ver_id
    assert bundle_task1.documents[0].content == "Rule 1: Never cross your own temporal wake."

    # New resolution picks up latest revision
    bundle_task2 = await resolver.resolve_for_project(
        project_id=project_id,
    )
    assert bundle_task2.collection_revision == 3
    assert len(bundle_task2.documents) == 1
    snap_task2 = bundle_task2.documents[0]
    assert snap_task2.version_id == v2_id
    assert snap_task2.content == "Rule 1: Never cross your own temporal wake, unless protected by Chrono Aegis."
