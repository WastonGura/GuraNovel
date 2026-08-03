"""Issue #96 HTTP boundary regressions for project-maintenance workflows."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import (
    ArchivistAgent,
    ChiefEditorAgent,
    ConsistencyReviewOutcome,
    DeterministicMaintenanceProvider,
    LoreAgent,
    PlotArchitectAgent,
    WorldbuildingAgent,
)
from app.api.deps import get_db_session, get_project_maintenance_composition
from app.main import create_app
from app.models import (
    ActionRequest,
    Document,
    DocumentSource,
    DocumentType,
    MaintenanceAffectedItem,
    MaintenanceChange,
)
from app.services import DocumentService, ProjectService
from app.services.project_maintenance_service import ProjectMaintenanceComposition
from app.workspace import ProjectWorkspace


def _composition(
    provider: DeterministicMaintenanceProvider | None = None,
) -> ProjectMaintenanceComposition:
    selected = provider or DeterministicMaintenanceProvider()
    return ProjectMaintenanceComposition(
        LoreAgent(selected),
        ChiefEditorAgent(selected),
        PlotArchitectAgent(selected),
        WorldbuildingAgent(selected),
        ArchivistAgent(selected),
    )


class _BlockingPlanProvider(DeterministicMaintenanceProvider):
    async def analyze_maintenance_impact(self, request, profile):
        output = await super().analyze_maintenance_impact(request, profile)
        reference = output["affected_items"][0]["stable_reference"]
        output["safe_to_change"] = False
        output["warnings"] = [
            {
                "warning_id": str(uuid4()),
                "severity": "blocking",
                "code": "unsafe_maintenance_plan",
                "message": "The requested change needs a revised plan.",
                "affected_item_references": [reference],
            }
        ]
        return output


class _UnsafeReasonProvider(DeterministicMaintenanceProvider):
    async def analyze_maintenance_impact(self, request, profile):
        output = await super().analyze_maintenance_impact(request, profile)
        output["affected_items"][0]["reason"] = (
            "C:\\private\\novel.md token=sk-not-a-real-key"
        )
        return output


class _UnsafeStableReferenceProvider(DeterministicMaintenanceProvider):
    async def analyze_maintenance_impact(self, request, profile):
        output = await super().analyze_maintenance_impact(request, profile)
        unsafe = f"{output['affected_items'][0]['item_type']}/sk-abcdefghijk"
        output["affected_items"][0]["stable_reference"] = unsafe
        output["required_rewrites"][0]["affected_item_reference"] = unsafe
        return output


@pytest.fixture
async def maintenance_http(
    async_session: AsyncSession,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    app = create_app()

    async def db():
        yield async_session

    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_project_maintenance_composition] = lambda: _composition()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, app


async def _project_with_document(
    session: AsyncSession, root: Path, suffix: str
):
    project = await ProjectService(session, ProjectWorkspace(root)).create_project(
        slug=f"maintenance-route-{suffix}-{uuid4().hex[:8]}",
        title="Maintenance route",
    )
    document = await DocumentService(session).create_document(
        project_id=project.id,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="World rules",
        path="world/overview.md",
        content="# World\n\nThe old rule.",
        source=DocumentSource.USER,
        change_summary="Seed maintenance route context.",
    )
    return project, document


def _url(project_id) -> str:
    return f"/api/v1/projects/{project_id}/maintenance"


def _assert_public_run_shape(body: dict) -> None:
    assert set(body) == {
        "id",
        "maintenance_change_id",
        "type",
        "status",
        "current_node",
        "next_node",
        "awaiting_user",
        "title",
        "created_at",
        "updated_at",
        "completed_at",
        "affected_items",
        "revision_plan",
        "consistency_review",
        "applied_document_version_ids",
        "pending_action",
    }
    assert all(
        private not in str(body)
        for private in (
            "metadata",
            "prompt",
            "provider_review_id",
            "change_set_id",
            "snapshot_path",
            "workspace_root",
            "raw_report",
            "change_request",
        )
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_start_read_list_approve_and_replay(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    first, document = await _project_with_document(async_session, tmp_path / "first", "first")
    second, _ = await _project_with_document(async_session, tmp_path / "second", "second")
    first_id, second_id, document_id = first.id, second.id, document.id
    title = "Retcon the world rule"
    change_request = "Adjust the rule without rewriting history."

    started = await client.post(
        f"{_url(first_id)}/start",
        json={
            "title": title,
            "change_request": change_request,
            "scope_hints": ["world"],
        },
    )

    assert started.status_code == 201
    body = started.json()
    _assert_public_run_shape(body)
    assert body["status"] == "USER_CONFIRMATION"
    assert body["title"] == title
    assert "change_request" not in body
    assert body["pending_action"]["allowed_decisions"] == ["approve", "revise", "cancel"]
    assert set(body["pending_action"]) == {
        "id",
        "type",
        "status",
        "confirmation_kind",
        "review_outcome",
        "allowed_decisions",
    }
    assert len(body["affected_items"]) == 2
    assert all(
        set(item)
        == {
            "id",
            "position",
            "type",
            "stable_reference",
            "impact_level",
            "reason",
            "document_id",
            "chapter_id",
        }
        for item in body["affected_items"]
    )
    plan = body["revision_plan"]
    assert set(plan) == {
        "id",
        "document_id",
        "version_id",
        "review_outcome",
        "summary",
        "operations",
    }
    assert plan["operations"] and all(
        set(operation)
        == {
            "id",
            "sequence",
            "operation",
            "document_id",
            "expected_version_id",
            "affected_item_ids",
            "instruction",
        }
        for operation in plan["operations"]
    )

    read = await client.get(f"{_url(first_id)}/{body['id']}")
    assert read.status_code == 200 and read.json() == body
    assert (await client.get(f"{_url(second_id)}/{body['id']}")).status_code == 404

    listing = await client.get(_url(first_id), params={"offset": 0, "limit": 1})
    assert listing.status_code == 200 and listing.json() == [body]

    resolved = await client.post(
        f"{_url(first_id)}/{body['id']}/actions/{body['pending_action']['id']}/resolve",
        json={"decision": "approve"},
    )
    assert resolved.status_code == 200
    updated = resolved.json()
    _assert_public_run_shape(updated)
    assert updated["status"] == "PROJECT_UPDATED"
    assert updated["pending_action"] is None
    assert updated["consistency_review"] == {
        "id": updated["consistency_review"]["id"],
        "outcome": "clean",
        "findings": [],
    }
    assert updated["applied_document_version_ids"]
    stored = await async_session.scalar(select(Document).where(Document.id == document_id))
    assert stored is not None and str(stored.current_version_id) in updated[
        "applied_document_version_ids"
    ]

    replay = await client.post(
        f"{_url(first_id)}/{body['id']}/actions/{body['pending_action']['id']}/resolve",
        json={"decision": "approve"},
    )
    assert replay.status_code == 409


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_list_is_newest_first_and_bounded(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    project, _ = await _project_with_document(async_session, tmp_path, "list")
    project_id = project.id
    first = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "First", "change_request": "Apply the first safe change."},
        )
    ).json()
    await client.post(
        f"{_url(project_id)}/{first['id']}/actions/{first['pending_action']['id']}/resolve",
        json={"decision": "approve"},
    )
    second = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "Second", "change_request": "Apply the second safe change."},
        )
    ).json()

    newest = await client.get(_url(project_id), params={"limit": 1, "offset": 0})
    older = await client.get(_url(project_id), params={"limit": 1, "offset": 1})
    assert newest.status_code == older.status_code == 200
    assert [item["id"] for item in newest.json()] == [second["id"]]
    assert [item["id"] for item in older.json()] == [first["id"]]
    assert (await client.get(_url(project_id), params={"limit": 101})).status_code == 422
    assert (await client.get(_url(project_id), params={"offset": -1})).status_code == 422


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_revise_creates_a_fresh_gate_then_cancel_terminates(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    project, _ = await _project_with_document(async_session, tmp_path, "revise-cancel")
    project_id = project.id
    started = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "Revise", "change_request": "Prepare a safer revision plan."},
        )
    ).json()

    revised = await client.post(
        f"{_url(project_id)}/{started['id']}/actions/"
        f"{started['pending_action']['id']}/resolve",
        json={"decision": "revise"},
    )
    assert revised.status_code == 200
    revised_body = revised.json()
    assert revised_body["status"] == "USER_CONFIRMATION"
    assert revised_body["pending_action"]["id"] != started["pending_action"]["id"]
    assert revised_body["revision_plan"]["document_id"] == started["revision_plan"][
        "document_id"
    ]
    assert revised_body["revision_plan"]["version_id"] != started["revision_plan"][
        "version_id"
    ]

    cancelled = await client.post(
        f"{_url(project_id)}/{started['id']}/actions/"
        f"{revised_body['pending_action']['id']}/resolve",
        json={"decision": "cancel"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["pending_action"] is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_validation_and_cross_scope_fail_closed_without_echo(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    first, _ = await _project_with_document(async_session, tmp_path / "first", "validate-one")
    second, _ = await _project_with_document(async_session, tmp_path / "second", "validate-two")
    first_id, second_id = first.id, second.id
    secret = "private-provider-setting"
    invalid = await client.post(
        f"{_url(first_id)}/start",
        json={
            "title": "Invalid",
            "change_request": "Do not accept extra authority.",
            "provider": secret,
            "document_id": str(uuid4()),
            "workspace_path": "C:/private/novel.md",
        },
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text and "C:/private/novel.md" not in invalid.text

    started = (
        await client.post(
            f"{_url(first_id)}/start",
            json={"title": "Scoped", "change_request": "Keep exact project scope."},
        )
    ).json()
    action_id = started["pending_action"]["id"]
    before = await async_session.get(ActionRequest, action_id)
    assert before is not None and before.status == "pending"
    foreign = await client.post(
        f"{_url(second_id)}/{started['id']}/actions/{action_id}/resolve",
        json={"decision": "approve"},
    )
    assert foreign.status_code == 404
    after = await async_session.get(ActionRequest, action_id)
    assert after is not None and after.status == "pending"
    assert (
        await client.post(
            f"{_url(first_id)}/{started['id']}/actions/{uuid4()}/resolve",
            json={"decision": "approve"},
        )
    ).status_code == 404


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_blocking_gate_cannot_be_force_approved(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, app = maintenance_http
    app.dependency_overrides[get_project_maintenance_composition] = lambda: _composition(
        _BlockingPlanProvider()
    )
    project, _ = await _project_with_document(async_session, tmp_path, "blocking")
    project_id = project.id
    started = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "Blocked", "change_request": "Request an unsafe direct change."},
        )
    ).json()
    action = started["pending_action"]
    assert action["review_outcome"] == "blocking"
    assert action["allowed_decisions"] == ["revise", "cancel"]

    forced = await client.post(
        f"{_url(project_id)}/{started['id']}/actions/{action['id']}/resolve",
        json={"decision": "approve"},
    )
    assert forced.status_code == 409
    stored = await async_session.get(ActionRequest, action["id"])
    assert stored is not None and stored.status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_unsafe_provider_reason_fails_before_public_projection(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, app = maintenance_http
    app.dependency_overrides[get_project_maintenance_composition] = lambda: _composition(
        _UnsafeReasonProvider()
    )
    project, _ = await _project_with_document(async_session, tmp_path, "unsafe-provider")
    project_id = project.id
    unsafe = "C:\\private\\novel.md token=sk-not-a-real-key"

    response = await client.post(
        f"{_url(project_id)}/start",
        json={"title": "Unsafe", "change_request": "Reject unsafe provider text."},
    )

    assert response.status_code == 422
    assert unsafe not in response.text and "sk-not-a-real-key" not in response.text
    assert await async_session.scalar(
        select(MaintenanceChange.id).where(MaintenanceChange.project_id == project_id)
    ) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_unsafe_provider_stable_reference_fails_before_persistence(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, app = maintenance_http
    app.dependency_overrides[get_project_maintenance_composition] = lambda: _composition(
        _UnsafeStableReferenceProvider()
    )
    project, _ = await _project_with_document(async_session, tmp_path, "unsafe-reference")
    project_id = project.id
    unsafe = "sk-abcdefghijk"

    response = await client.post(
        f"{_url(project_id)}/start",
        json={"title": "Unsafe", "change_request": "Reject unsafe provider references."},
    )

    assert response.status_code == 422
    assert unsafe not in response.text
    assert await async_session.scalar(
        select(MaintenanceChange.id).where(MaintenanceChange.project_id == project_id)
    ) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_corrupt_public_reason_fails_closed_without_echo(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    project, _ = await _project_with_document(async_session, tmp_path, "corrupt-reason")
    project_id = project.id
    started = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "Corrupt reason", "change_request": "Create a safe gate."},
        )
    ).json()
    unsafe = "https://private.example/novel token=sk-not-a-real-key"
    item = await async_session.scalar(
        select(MaintenanceAffectedItem).where(
            MaintenanceAffectedItem.maintenance_change_id
            == started["maintenance_change_id"]
        )
    )
    assert item is not None
    item.reason = unsafe
    await async_session.commit()

    response = await client.get(f"{_url(project_id)}/{started['id']}")
    assert response.status_code == 409
    assert unsafe not in response.text and "sk-not-a-real-key" not in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_corrupt_public_stable_reference_fails_closed_without_echo(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    project, _ = await _project_with_document(async_session, tmp_path, "corrupt-reference")
    project_id = project.id
    started = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "Corrupt reference", "change_request": "Create a safe gate."},
        )
    ).json()
    unsafe = "world/overview.md"
    item = await async_session.scalar(
        select(MaintenanceAffectedItem).where(
            MaintenanceAffectedItem.maintenance_change_id
            == started["maintenance_change_id"]
        )
    )
    assert item is not None
    item.stable_reference = unsafe
    await async_session.commit()

    response = await client.get(f"{_url(project_id)}/{started['id']}")
    assert response.status_code == 409
    assert unsafe not in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_corrupt_cross_project_plan_binding_fails_closed(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, _ = maintenance_http
    first, _ = await _project_with_document(async_session, tmp_path / "first", "plan-one")
    _second, foreign_document = await _project_with_document(
        async_session, tmp_path / "second", "plan-two"
    )
    first_id, foreign_document_id = first.id, foreign_document.id
    started = (
        await client.post(
            f"{_url(first_id)}/start",
            json={"title": "Plan binding", "change_request": "Create a bound plan."},
        )
    ).json()
    change = await async_session.get(MaintenanceChange, started["maintenance_change_id"])
    assert change is not None
    change.revision_plan_document_id = foreign_document_id
    await async_session.commit()

    response = await client.get(f"{_url(first_id)}/{started['id']}")
    assert response.status_code == 409
    assert str(foreign_document_id) not in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_warning_finding_is_validated_and_accept_warning_completes(
    maintenance_http, async_session: AsyncSession, tmp_path: Path
) -> None:
    client, app = maintenance_http
    app.dependency_overrides[get_project_maintenance_composition] = lambda: _composition(
        DeterministicMaintenanceProvider(ConsistencyReviewOutcome.WARNING)
    )
    project, _ = await _project_with_document(async_session, tmp_path, "warning")
    project_id = project.id
    started = (
        await client.post(
            f"{_url(project_id)}/start",
            json={"title": "Warning", "change_request": "Apply a reviewable safe change."},
        )
    ).json()
    warning = (
        await client.post(
            f"{_url(project_id)}/{started['id']}/actions/"
            f"{started['pending_action']['id']}/resolve",
            json={"decision": "approve"},
        )
    ).json()
    assert warning["status"] == "USER_CONFIRMATION"
    assert warning["pending_action"]["allowed_decisions"] == ["accept_warning", "revise"]
    review = warning["consistency_review"]
    assert review["outcome"] == "warning" and len(review["findings"]) == 1
    assert set(review["findings"][0]) == {
        "id",
        "sequence",
        "code",
        "severity",
        "blocking",
        "affected_documents",
        "suggested_corrective_action",
    }

    accepted = await client.post(
        f"{_url(project_id)}/{started['id']}/actions/"
        f"{warning['pending_action']['id']}/resolve",
        json={"decision": "accept_warning"},
    )
    assert accepted.status_code == 200 and accepted.json()["status"] == "PROJECT_UPDATED"
