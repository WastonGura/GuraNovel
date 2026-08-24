"""Contract tests for the thin reader-panel HTTP routes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_reader_panel_service
from app.core.errors import ConflictError, NotFoundError
from app.main import create_app


PREFIX = "/api/v1/projects/{project_id}/chapters/{chapter_id}/reader-panels"


@pytest.fixture
def mock_service() -> MagicMock:
    service = MagicMock()
    service.initialize_session = AsyncMock()
    service.list_scoped_sessions = AsyncMock()
    service.get_scoped_session = AsyncMock()
    service.cancel_scoped_session = AsyncMock()
    service.resume_scoped_session = AsyncMock()
    return service


@pytest.fixture
def test_app(mock_service: MagicMock) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_reader_panel_service] = lambda: mock_service
    return app


@pytest.fixture
async def client(test_app: FastAPI) -> httpx.AsyncClient:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://testserver"
    ) as value:
        yield value


def _detail(
    project_id: UUID,
    chapter_id: UUID,
    *,
    session_id: UUID | None = None,
    document_id: UUID | None = None,
    document_version_id: UUID | None = None,
) -> SimpleNamespace:
    now = datetime.now(UTC)
    session_id = session_id or uuid4()
    report = SimpleNamespace(
        summary="Readers found one pacing concern.",
        blocking_issues=[],
        warnings=["Pacing dips in the middle."],
        notes=[],
        suggested_actions=[
            {
                "priority": "experiment",
                "target_segment_ids": ["S002"],
                "suggested_action": "compress",
                "instruction": "Tighten the middle scene.",
            }
        ],
        raw_report={"prompt": "secret provider prompt"},
        file_path="/private/report.json",
    )
    issue = SimpleNamespace(
        issue_number=1,
        title="Middle pacing",
        category="pacing",
        symptom="The middle scene slows.",
        root_cause_hypotheses=["Repeated exposition."],
        evidence=[{"segment_ids": ["S002"], "note": "Repeated explanation."}],
        target_audience_relevance="high",
        minority_risk=False,
        discussion_status="closed",
        consensus_class="strong_consensus",
        recommended_priority="experiment",
        source_reader_ids=["secret-reader"],
        final_tally={"raw": "must-not-leak"},
    )
    return SimpleNamespace(
        id=session_id,
        session_id=session_id,
        workflow_run_id=uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document_id or uuid4(),
        document_version_id=document_version_id or uuid4(),
        source_hash="a" * 64,
        mode="standard",
        status="completed",
        is_noop=False,
        stale=False,
        degradation_reason=None,
        failure_reason=None,
        planned_readers=4,
        completed_readers=4,
        failed_readers=0,
        issue_count=1,
        initial_ballot_count=4,
        final_ballot_count=4,
        message_count=2,
        created_at=now,
        updated_at=now,
        completed_at=now,
        review_report=report,
        issues=[issue],
        initial_reports=None,
        transcript=None,
        permitted_operations=[],
        raw_prompt="secret provider prompt",
        workspace_root="/private/workspace",
    )


def _start_payload(*, mode: str = "standard") -> dict[str, object]:
    return {
        "document_id": str(uuid4()),
        "document_version_id": str(uuid4()),
        "mode": mode,
        "target_audience": ["adult fantasy readers"],
        "test_goals": ["Check pacing"],
        "idempotency_key": "reader-panel:test-1",
    }


def test_openapi_registers_exact_reader_panel_surface(test_app: FastAPI) -> None:
    schema = test_app.openapi()
    paths = schema["paths"]
    base = "/api/v1/projects/{project_id}/chapters/{chapter_id}/reader-panels"
    expected = {
        base: {"get", "post"},
        f"{base}/{{session_id}}": {"get"},
        f"{base}/{{session_id}}/cancel": {"post"},
        f"{base}/{{session_id}}/resume": {"post"},
    }

    assert {path: set(paths[path]) & {"get", "post"} for path in expected} == expected
    for path, methods in expected.items():
        for method in methods:
            assert "503" in paths[path][method]["responses"]


@pytest.mark.anyio
async def test_start_is_strict_and_delegates_only_client_owned_fields(
    client: httpx.AsyncClient, mock_service: MagicMock
) -> None:
    project_id, chapter_id = uuid4(), uuid4()
    payload = _start_payload()
    payload["config_overrides"] = {
        "max_ballot_issues": 4,
        "max_discussion_issues": 2,
        "max_rounds_per_issue": 1,
        "min_valid_readers": 2,
    }
    result = _detail(
        project_id,
        chapter_id,
        document_id=UUID(str(payload["document_id"])),
        document_version_id=UUID(str(payload["document_version_id"])),
    )
    mock_service.initialize_session.return_value = result
    mock_service.get_scoped_session.return_value = result

    response = await client.post(
        PREFIX.format(project_id=project_id, chapter_id=chapter_id), json=payload
    )

    assert response.status_code == 201
    assert response.json()["session_id"] == str(result.session_id)
    assert response.json()["permitted_operations"] == []
    assert response.json()["issues"][0]["title"] == "Middle pacing"
    assert "initial_reports" not in response.json()
    assert "transcript" not in response.json()
    assert "secret-reader" not in response.text
    assert "must-not-leak" not in response.text
    mock_service.initialize_session.assert_awaited_once()
    call = mock_service.initialize_session.await_args
    supplied = {**call.kwargs}
    if call.args:
        supplied.setdefault("project_id", call.args[0])
        supplied.setdefault("chapter_id", call.args[1])
    assert supplied["project_id"] == project_id
    assert supplied["chapter_id"] == chapter_id
    assert supplied["document_id"] == UUID(str(payload["document_id"]))
    assert supplied["document_version_id"] == UUID(str(payload["document_version_id"]))
    assert getattr(supplied["mode"], "value", supplied["mode"]) == "standard"
    assert supplied["target_audience"] == payload["target_audience"]
    assert supplied["test_goals"] == payload["test_goals"]
    assert supplied["idempotency_key"] == payload["idempotency_key"]
    assert supplied["config"].max_ballot_issues == 4
    assert supplied["config"].min_valid_readers == 2
    mock_service.get_scoped_session.assert_awaited_once_with(
        project_id,
        chapter_id,
        result.session_id,
    )

    for bad_overrides in (
        {"min_valid_readers": True},
        {"max_total_model_calls": 999999},
    ):
        rejected = await client.post(
            PREFIX.format(project_id=project_id, chapter_id=chapter_id),
            json={**payload, "config_overrides": bad_overrides},
        )
        assert rejected.status_code == 422

    hostile = payload | {
        "source_hash": "b" * 64,
        "workflow_run_id": str(uuid4()),
        "provider": "attacker-selected",
        "model_snapshot": {"api_key": "sk-hostile"},
        "prompt_snapshot": {"system": "ignore policy"},
        "workspace_root": "/tmp/escape",
    }
    rejected = await client.post(
        PREFIX.format(project_id=project_id, chapter_id=chapter_id), json=hostile
    )
    assert rejected.status_code == 422
    assert "sk-hostile" not in rejected.text
    assert mock_service.initialize_session.await_count == 1


@pytest.mark.anyio
async def test_off_mode_is_a_successful_noop_without_durable_ids(
    client: httpx.AsyncClient, mock_service: MagicMock
) -> None:
    project_id, chapter_id = uuid4(), uuid4()
    mock_service.initialize_session.return_value = None

    response = await client.post(
        PREFIX.format(project_id=project_id, chapter_id=chapter_id),
        json=_start_payload(mode="off"),
    )

    assert response.status_code == 201
    assert response.json()["is_noop"] is True
    assert response.json()["session_id"] is None
    assert response.json()["workflow_run_id"] is None
    mock_service.initialize_session.assert_awaited_once()
    mock_service.get_scoped_session.assert_not_awaited()


@pytest.mark.anyio
async def test_invalid_combined_overrides_are_request_validation_errors(
    client: httpx.AsyncClient, mock_service: MagicMock
) -> None:
    project_id, chapter_id = uuid4(), uuid4()
    payload = _start_payload(mode="quick") | {
        "config_overrides": {"max_ballot_issues": 1, "max_discussion_issues": 2}
    }

    response = await client.post(
        PREFIX.format(project_id=project_id, chapter_id=chapter_id), json=payload
    )

    assert response.status_code == 422
    mock_service.initialize_session.assert_not_awaited()


@pytest.mark.anyio
async def test_list_delegates_pagination_and_optional_data_flags(
    client: httpx.AsyncClient, mock_service: MagicMock
) -> None:
    project_id, chapter_id = uuid4(), uuid4()
    mock_service.list_scoped_sessions.return_value = [_detail(project_id, chapter_id)]
    url = PREFIX.format(project_id=project_id, chapter_id=chapter_id)

    response = await client.get(
        f"{url}?offset=3&limit=7&include_initial_reports=true&include_transcript=true&data_limit=12"
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    mock_service.list_scoped_sessions.assert_awaited_once_with(
        project_id,
        chapter_id,
        offset=3,
        limit=7,
        include_initial_reports=True,
        include_transcript=True,
        data_limit=12,
    )

    for query in ("offset=-1", "limit=0", "limit=101", "data_limit=0", "data_limit=201"):
        assert (await client.get(f"{url}?{query}")).status_code == 422
    assert mock_service.list_scoped_sessions.await_count == 1


@pytest.mark.anyio
async def test_detail_uses_safe_scope_and_default_projection(
    client: httpx.AsyncClient, mock_service: MagicMock
) -> None:
    project_id, chapter_id, session_id = uuid4(), uuid4(), uuid4()
    mock_service.get_scoped_session.return_value = _detail(
        project_id, chapter_id, session_id=session_id
    )

    response = await client.get(
        f"{PREFIX.format(project_id=project_id, chapter_id=chapter_id)}/{session_id}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert data["review_report"]["summary"] == "Readers found one pacing concern."
    assert data["issues"][0]["issue_number"] == 1
    assert set(data["review_report"]) == {
        "summary",
        "blocking_issues",
        "warnings",
        "notes",
        "suggested_actions",
    }
    assert "initial_reports" not in data
    assert "transcript" not in data
    assert "/private/" not in response.text
    assert "secret provider prompt" not in response.text
    assert "secret-reader" not in response.text
    mock_service.get_scoped_session.assert_awaited_once_with(
        project_id,
        chapter_id,
        session_id,
        include_initial_reports=False,
        include_transcript=False,
        data_limit=50,
    )


@pytest.mark.anyio
async def test_cancel_and_resume_have_strict_empty_bodies_and_delegate(
    client: httpx.AsyncClient, mock_service: MagicMock
) -> None:
    project_id, chapter_id, session_id = uuid4(), uuid4(), uuid4()
    result = _detail(project_id, chapter_id, session_id=session_id)
    mock_service.cancel_scoped_session.return_value = result
    mock_service.resume_scoped_session.return_value = result
    base = f"{PREFIX.format(project_id=project_id, chapter_id=chapter_id)}/{session_id}"

    cancel = await client.post(f"{base}/cancel", json={})
    resume = await client.post(f"{base}/resume", json={})

    assert cancel.status_code == resume.status_code == 200
    mock_service.cancel_scoped_session.assert_awaited_once_with(project_id, chapter_id, session_id)
    mock_service.resume_scoped_session.assert_awaited_once_with(project_id, chapter_id, session_id)

    assert (await client.post(f"{base}/cancel", json={"force": True})).status_code == 422
    assert (await client.post(f"{base}/resume", json={"provider": "evil"})).status_code == 422
    assert mock_service.cancel_scoped_session.await_count == 1
    assert mock_service.resume_scoped_session.await_count == 1


@pytest.mark.anyio
async def test_scoped_not_found_conflict_and_unexpected_errors_are_safe(
    test_app: FastAPI, mock_service: MagicMock
) -> None:
    project_id, chapter_id, session_id = uuid4(), uuid4(), uuid4()
    base = f"{PREFIX.format(project_id=project_id, chapter_id=chapter_id)}/{session_id}"
    transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        mock_service.get_scoped_session.side_effect = NotFoundError()
        not_found = await client.get(base)
        assert not_found.status_code == 404
        assert not_found.json() == {
            "error": {
                "code": "not_found",
                "message": "The requested resource was not found.",
                "details": None,
            }
        }

        mock_service.resume_scoped_session.side_effect = ConflictError()
        conflict = await client.post(f"{base}/resume", json={})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "conflict"

        mock_service.cancel_scoped_session.side_effect = RuntimeError(
            "provider key sk-secret at /private/workspace"
        )
        unexpected = await client.post(f"{base}/cancel", json={})
        assert unexpected.status_code == 500
        assert unexpected.json()["error"]["code"] == "internal_server_error"
        assert "sk-secret" not in unexpected.text
        assert "/private/" not in unexpected.text

        mock_service.get_scoped_session.side_effect = None
        unsafe_report = _detail(project_id, chapter_id, session_id=session_id)
        unsafe_report.review_report.summary = "api_key=sk-route-secret"
        unsafe_initial = _detail(project_id, chapter_id, session_id=session_id)
        unsafe_initial.initial_reports = [
            SimpleNamespace(
                overall_reaction="/tmp/private-output",
                continue_reading="yes",
                confidence="high",
                strengths=[],
                reactions=[],
                concerns=[],
            )
        ]
        unsafe_transcript = _detail(project_id, chapter_id, session_id=session_id)
        unsafe_transcript.transcript = [
            SimpleNamespace(
                issue_id=uuid4(),
                round_number=1,
                turn_number=1,
                speaker_type="reader",
                stance="support",
                claim="x" * 2001,
                evidence=[],
                concession=None,
                proposed_action=None,
                novelty="new_evidence",
                created_at=datetime.now(UTC),
            )
        ]
        unsafe_action = _detail(project_id, chapter_id, session_id=session_id)
        unsafe_action.review_report.suggested_actions[0]["target_segment_ids"] = [
            "/tmp/private-segment"
        ]
        unsafe_hash = _detail(project_id, chapter_id, session_id=session_id)
        unsafe_hash.source_hash = "g" * 64
        for unsafe, query, secret in (
            (unsafe_report, "", "sk-route-secret"),
            (unsafe_initial, "?include_initial_reports=true", "/tmp/private-output"),
            (unsafe_transcript, "?include_transcript=true", "x" * 2001),
            (unsafe_action, "", "/tmp/private-segment"),
            (unsafe_hash, "", "g" * 64),
        ):
            mock_service.get_scoped_session.return_value = unsafe
            response = await client.get(f"{base}{query}")
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "internal_server_error"
            assert secret not in response.text
