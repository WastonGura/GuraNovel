"""Fast unit tests for Chapter Production V2 API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import (
    get_chapter_production_v2_service,
    get_db_session,
    get_default_actor_user_id,
)
from app.main import create_app
from app.models import (
    ActionRequest,
    Chapter,
    Project,
    WorkflowRun,
    WorkflowType,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Finalized,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ReviewProviderError,
    ChapterProductionV2Started,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import (
    ChapterProductionState,
    ChapterProductionStatus,
)


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.get = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def mock_service() -> MagicMock:
    service = MagicMock()
    service.start_from_approved_outline = AsyncMock()
    service.resume_drafting = AsyncMock()
    service.resolve_author_action = AsyncMock()
    service.request_user_feedback_revision = AsyncMock()
    service.submit_manual_edit = AsyncMock()
    service.resolve_review_action = AsyncMock()
    service.execute_review_revision = AsyncMock()
    service.execute_current_review = AsyncMock()
    service.finalize_without_reader_panel = AsyncMock()
    service.reconcile_indeterminate = AsyncMock()
    service.load_state = AsyncMock()
    return service


@pytest.fixture
def actor_id() -> UUID:
    return uuid4()


@pytest.fixture
def test_app(mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID) -> FastAPI:
    app = create_app()

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_chapter_production_v2_service] = lambda: mock_service
    app.dependency_overrides[get_default_actor_user_id] = lambda: actor_id
    return app


@pytest.fixture
async def client(test_app: FastAPI) -> httpx.AsyncClient:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://testserver"
    ) as c:
        yield c


def _sample_started(run_id: UUID | None = None) -> ChapterProductionV2Started:
    return ChapterProductionV2Started(
        workflow_run_id=run_id or uuid4(),
        action_request_id=uuid4(),
        outline_document_id=uuid4(),
        outline_version_id=uuid4(),
        draft_document_id=uuid4(),
        draft_version_id=uuid4(),
    )


def _sample_updated(
    run_id: UUID | None = None, action_id: UUID | None = None
) -> ChapterProductionV2Updated:
    return ChapterProductionV2Updated(
        workflow_run_id=run_id or uuid4(),
        draft_document_id=uuid4(),
        draft_version_id=uuid4(),
        action_request_id=action_id,
    )


def _sample_finalized(run_id: UUID | None = None) -> ChapterProductionV2Finalized:
    return ChapterProductionV2Finalized(
        workflow_run_id=run_id or uuid4(),
        final_document_id=uuid4(),
        final_version_id=uuid4(),
    )


def _sample_state(
    run_id: UUID | None = None, chapter_id: UUID | None = None
) -> ChapterProductionState:
    return ChapterProductionState(
        chapter_workflow_run_id=str(run_id or uuid4()),
        chapter_id=str(chapter_id or uuid4()),
        status=ChapterProductionStatus.DRAFTING,
        current_node="drafting",
        awaiting_user=False,
        review_policy_version="chapter-quality-v1",
        chief_editor_required=True,
    )


def _setup_chapter_and_project(
    mock_session: MagicMock,
    project_id: UUID,
    chapter_id: UUID,
    owner_id: UUID | None = None,
    run_id: UUID | None = None,
    action_id: UUID | None = None,
) -> tuple[Project, Chapter, WorkflowRun, ActionRequest]:
    project = Project(
        id=project_id,
        slug=f"proj-{project_id.hex[:8]}",
        title="Project",
        workspace_root="/tmp/workspace",
        owner_id=owner_id,
    )
    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        chapter_number=1,
        title="Chapter 1",
        status="OUTLINE_APPROVED",
    )
    run = WorkflowRun(
        id=run_id or uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="DRAFTING",
        current_node="drafting",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    action = ActionRequest(
        id=action_id or uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=run.id,
        request_type="chapter_author_revision",
        prompt="Review the chapter draft.",
        status="pending",
    )

    async def scalar_mock(query: Any) -> Any:
        query_str = str(query)
        if "FROM chapters" in query_str:
            return chapter
        if "FROM projects" in query_str:
            return project
        if "FROM workflow_runs" in query_str:
            return run
        if "FROM action_requests" in query_str:
            return action
        return None

    mock_session.scalar.side_effect = scalar_mock
    mock_session.get.return_value = project
    return project, chapter, run, action


@pytest.mark.anyio
async def test_start_chapter_production_success(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    _setup_chapter_and_project(mock_session, project_id, chapter_id, owner_id=actor_id)

    started = _sample_started()
    mock_service.start_from_approved_outline.return_value = started

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2",
        json={},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["workflow_run_id"] == str(started.workflow_run_id)
    assert data["action_request_id"] == str(started.action_request_id)
    assert data["outline_document_id"] == str(started.outline_document_id)
    assert data["draft_document_id"] == str(started.draft_document_id)
    mock_service.start_from_approved_outline.assert_awaited_once_with(
        project_id, chapter_id, actor_user_id=actor_id
    )


@pytest.mark.anyio
async def test_list_production_runs_success(
    client: httpx.AsyncClient, mock_session: MagicMock
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    _setup_chapter_and_project(mock_session, project_id, chapter_id)

    run_1 = WorkflowRun(
        id=uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="DRAFTING",
        current_node="drafting",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    run_2 = WorkflowRun(
        id=uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="COMPLETED",
        current_node="completed",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [run_1, run_2]
    mock_session.scalars.return_value = mock_scalars

    response = await client.get(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2?offset=0&limit=20"
    )

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert items[0]["workflow_run_id"] == str(run_1.id)
    assert items[0]["status"] == "DRAFTING"
    assert items[1]["workflow_run_id"] == str(run_2.id)
    assert items[1]["status"] == "COMPLETED"


@pytest.mark.anyio
async def test_list_production_runs_pagination_validation(
    client: httpx.AsyncClient, mock_session: MagicMock
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    _setup_chapter_and_project(mock_session, project_id, chapter_id)

    # Offset < 0
    resp_neg_offset = await client.get(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2?offset=-1&limit=20"
    )
    assert resp_neg_offset.status_code == 422

    # Limit > 100
    resp_huge_limit = await client.get(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2?offset=0&limit=101"
    )
    assert resp_huge_limit.status_code == 422


@pytest.mark.anyio
async def test_get_production_run_state_success(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id
    )

    state = _sample_state(run_id=run_id, chapter_id=chapter_id)
    mock_service.load_state.return_value = state

    response = await client.get(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_workflow_run_id"] == str(run_id)
    assert data["chapter_id"] == str(chapter_id)
    assert data["status"] == "DRAFTING"
    assert data["current_node"] == "drafting"
    assert data["awaiting_user"] is False
    assert data["review_policy_version"] == "chapter-quality-v1"
    assert data["chief_editor_required"] is True
    mock_service.load_state.assert_awaited_once_with(
        project_id, chapter_id, run_id, actor_user_id=actor_id
    )


@pytest.mark.anyio
async def test_resume_drafting_success(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id
    )

    started = _sample_started(run_id=run_id)
    mock_service.resume_drafting.return_value = started

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/resume",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["workflow_run_id"] == str(run_id)
    mock_service.resume_drafting.assert_awaited_once_with(
        project_id, chapter_id, run_id, actor_user_id=actor_id
    )


@pytest.mark.anyio
async def test_resolve_author_action_accept(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    action_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id, action_id=action_id
    )

    updated = _sample_updated(run_id=run_id)
    mock_service.resolve_author_action.return_value = updated

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/actions/{action_id}/resolve",
        json={"decision": "accept"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["workflow_run_id"] == str(run_id)
    mock_service.resolve_author_action.assert_awaited_once_with(
        project_id, chapter_id, run_id, action_id, actor_user_id=actor_id, decision="accept"
    )


@pytest.mark.anyio
async def test_resolve_feedback_revision(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    action_id = uuid4()
    segment_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id, action_id=action_id
    )

    updated = _sample_updated(run_id=run_id)
    mock_service.request_user_feedback_revision.return_value = updated

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/actions/{action_id}/resolve",
        json={
            "decision": "request_feedback_revision",
            "feedback": "Make the scene darker.",
            "target_segment_ids": [str(segment_id)],
        },
    )

    assert response.status_code == 200
    mock_service.request_user_feedback_revision.assert_awaited_once_with(
        project_id,
        chapter_id,
        run_id,
        action_id,
        actor_user_id=actor_id,
        feedback="Make the scene darker.",
        target_segment_ids=[segment_id],
    )


@pytest.mark.anyio
async def test_resolve_submit_manual_edit(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    action_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id, action_id=action_id
    )

    updated = _sample_updated(run_id=run_id)
    mock_service.submit_manual_edit.return_value = updated

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/actions/{action_id}/resolve",
        json={
            "decision": "submit_manual_edit",
            "content": "# New Draft Content\n\nEdited manually.\n",
        },
    )

    assert response.status_code == 200
    mock_service.submit_manual_edit.assert_awaited_once_with(
        project_id,
        chapter_id,
        run_id,
        action_id,
        actor_user_id=actor_id,
        content="# New Draft Content\n\nEdited manually.\n",
    )


@pytest.mark.anyio
async def test_resolve_proceed_with_warnings(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    action_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id, action_id=action_id
    )

    updated = _sample_updated(run_id=run_id)
    mock_service.resolve_review_action.return_value = updated

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/actions/{action_id}/resolve",
        json={"decision": "proceed_with_warnings"},
    )

    assert response.status_code == 200
    mock_service.resolve_review_action.assert_awaited_once_with(
        project_id,
        chapter_id,
        run_id,
        action_id,
        actor_user_id=actor_id,
        decision="accept_warning",
    )


@pytest.mark.anyio
async def test_resolve_request_review_revision(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    action_id = uuid4()
    report_id = uuid4()
    segment_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id, action_id=action_id
    )

    updated = _sample_updated(run_id=run_id)
    mock_service.resolve_review_action.return_value = updated
    mock_service.execute_review_revision.return_value = updated

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/actions/{action_id}/resolve",
        json={
            "decision": "request_review_revision",
            "report_ids": [str(report_id)],
            "target_segment_ids": [str(segment_id)],
        },
    )

    assert response.status_code == 200
    mock_service.resolve_review_action.assert_awaited_once_with(
        project_id,
        chapter_id,
        run_id,
        action_id,
        actor_user_id=actor_id,
        decision="request_revision",
    )
    mock_service.execute_review_revision.assert_awaited_once_with(
        project_id,
        chapter_id,
        run_id,
        actor_user_id=actor_id,
        report_ids=[report_id],
        target_segment_ids=[segment_id],
    )


@pytest.mark.anyio
async def test_trigger_review_success(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id
    )

    updated = _sample_updated(run_id=run_id)
    mock_service.execute_current_review.return_value = updated

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/review",
        json={},
    )

    assert response.status_code == 200
    mock_service.execute_current_review.assert_awaited_once_with(
        project_id, chapter_id, run_id, actor_user_id=actor_id
    )


@pytest.mark.anyio
async def test_finalize_without_reader_panel_success(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id
    )

    finalized = _sample_finalized(run_id=run_id)
    mock_service.finalize_without_reader_panel.return_value = finalized

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/finalize",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["workflow_run_id"] == str(run_id)
    assert data["final_document_id"] == str(finalized.final_document_id)
    assert data["final_version_id"] == str(finalized.final_version_id)
    mock_service.finalize_without_reader_panel.assert_awaited_once_with(
        project_id, chapter_id, run_id, actor_user_id=actor_id
    )


@pytest.mark.anyio
async def test_reconcile_indeterminate_success(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock, actor_id: UUID
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, owner_id=actor_id, run_id=run_id
    )

    state = _sample_state(run_id=run_id, chapter_id=chapter_id)
    mock_service.reconcile_indeterminate.return_value = state

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/reconcile",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_workflow_run_id"] == str(run_id)
    mock_service.reconcile_indeterminate.assert_awaited_once_with(
        project_id, chapter_id, run_id, actor_user_id=actor_id
    )


@pytest.mark.anyio
async def test_extra_fields_forbidden(client: httpx.AsyncClient, mock_session: MagicMock) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    action_id = uuid4()
    _setup_chapter_and_project(
        mock_session, project_id, chapter_id, run_id=run_id, action_id=action_id
    )

    # Extra field on start
    res_start = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2",
        json={"extra_field": "disallowed"},
    )
    assert res_start.status_code == 422

    # Extra field on resolve action
    res_resolve = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/actions/{action_id}/resolve",
        json={"decision": "accept", "extra_field": "disallowed"},
    )
    assert res_resolve.status_code == 422


@pytest.mark.anyio
async def test_cross_project_isolation_chapter_not_found(
    client: httpx.AsyncClient, mock_session: MagicMock
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()

    # Chapter belongs to a different project or does not exist
    mock_session.scalar.return_value = None

    response = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2",
        json={},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_error_mapping_v2_exceptions(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(mock_session, project_id, chapter_id, run_id=run_id)

    # 422 Validation Error
    mock_service.start_from_approved_outline.side_effect = ChapterProductionV2ValidationError()
    res_val = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2",
        json={},
    )
    assert res_val.status_code == 422
    assert res_val.json()["error"]["code"] == "chapter_production_v2_invalid"

    # 409 Reconciliation Required
    mock_service.resume_drafting.side_effect = ChapterProductionV2ReconciliationError()
    res_rec = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/resume",
        json={},
    )
    assert res_rec.status_code == 409
    assert res_rec.json()["error"]["code"] == "chapter_production_v2_reconciliation_required"

    # 503 Provider Failed
    mock_service.execute_current_review.side_effect = ChapterProductionV2ProviderError()
    res_prov = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/review",
        json={},
    )
    assert res_prov.status_code == 503
    assert res_prov.json()["error"]["code"] == "chapter_production_v2_provider_failed"

    # 503 Review Provider Failed
    mock_service.execute_current_review.side_effect = ChapterProductionV2ReviewProviderError()
    res_rev = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/review",
        json={},
    )
    assert res_rev.status_code == 503
    assert res_rev.json()["error"]["code"] == "chapter_production_v2_review_provider_failed"

    # 500 Commit Indeterminate
    mock_service.finalize_without_reader_panel.side_effect = (
        ChapterProductionV2CommitIndeterminateError()
    )
    res_commit = await client.post(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}/finalize",
        json={},
    )
    assert res_commit.status_code == 500
    assert res_commit.json()["error"]["code"] == "chapter_production_v2_commit_indeterminate"


@pytest.mark.anyio
async def test_response_sanitation_no_raw_prompts_or_paths(
    client: httpx.AsyncClient, mock_session: MagicMock, mock_service: MagicMock
) -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    _setup_chapter_and_project(mock_session, project_id, chapter_id, run_id=run_id)

    state = _sample_state(run_id=run_id, chapter_id=chapter_id)
    mock_service.load_state.return_value = state

    response = await client.get(
        f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2/{run_id}"
    )
    assert response.status_code == 200
    content_str = response.text

    # Verify no private/secret leaks
    assert "/tmp/" not in content_str
    assert "sk-" not in content_str
    assert "filesystem" not in content_str.lower()
