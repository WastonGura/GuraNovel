"""Integration tests for Chapter Production V2 HTTP routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    ChapterProductionV2Composition,
    get_chapter_production_v2_composition,
    get_db_session,
)
from app.main import create_app
from app.models import ActionRequest, Chapter, Document, DocumentSource, DocumentType, DocumentVersion, Project, ReviewReport, User
from app.agents import (
    ChiefEditorChapterFinalAgent,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    EditorAgent,
    LoreChapterFinalAgent,
    RevisionAgent,
    WriterAgent,
)
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.document_service import DocumentService
from app.workflows.chapter_production import ChapterProductionStatus


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def v2_client(
    async_session: AsyncSession,
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()
    sessions = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    def override_composition() -> ChapterProductionV2Composition:
        writer_provider = DeterministicChapterWriterProvider()
        review_provider = DeterministicChapterReviewProvider()
        return ChapterProductionV2Composition(
            writer_agent=WriterAgent(writer_provider),
            revision_agent=RevisionAgent(writer_provider),
            editor_agent=EditorAgent(review_provider),
            chief_editor_agent=ChiefEditorChapterFinalAgent(review_provider),
            lore_agent=LoreChapterFinalAgent(review_provider),
            chief_editor_required=False,
            phase_session_source=ChapterPhaseSessionSource(async_session.bind),
        )

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_chapter_production_v2_composition] = override_composition

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _seed_approved_chapter(
    session: AsyncSession, workspace_root: Path
) -> tuple[Project, Chapter, Document, DocumentVersion, User]:
    workspace_root.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"owner-{uuid4().hex[:8]}", display_name="Owner")
    session.add(owner)
    await session.flush()

    project = Project(
        slug=f"proj-v2-routes-{uuid4().hex[:8]}",
        title="Project V2 Routes",
        workspace_root=str(workspace_root),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()

    chapter = Chapter(
        project_id=project.id,
        chapter_number=1,
        title="Chapter 1",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()

    outline_doc = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Chapter 1 Outline",
        path=f"chapters/{chapter.id}-selected-outline.md",
        content="## Chapter 1: The First Dawn\n\nOutline content describing key plot points.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved chapter outline.",
        actor_user_id=owner.id,
    )
    chapter.current_outline_document_id = outline_doc.id

    # Seed required review context documents (style guide and world overview)
    await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=None,
        document_type=DocumentType.STYLE_GUIDE,
        title="Project Style Guide",
        path="style-guide.md",
        content="# Style Guide\n\nMaintain consistent tone and voice.\n",
        source=DocumentSource.USER,
        change_summary="Initial style guide.",
        actor_user_id=owner.id,
    )
    await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=None,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="Project World Overview",
        path="world-overview.md",
        content="# World Overview\n\nA fantastical world of rich lore.\n",
        source=DocumentSource.USER,
        change_summary="Initial world overview.",
        actor_user_id=owner.id,
    )
    await session.commit()
    assert outline_doc.current_version is not None

    return project, chapter, outline_doc, outline_doc.current_version, owner


def _routes_url(project_id: UUID, chapter_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-v2"


@pytest.mark.parametrize("outcome", ["blocking", "warning", "note"])
async def test_selected_findings_revision_and_exact_retry(v2_client, async_session, tmp_path, outcome, monkeypatch):
    from app.llm import ProviderUnavailableError

    captured = []
    profiles = []

    class Writer(DeterministicChapterWriterProvider):
        async def revise_from_review(self, request, profile):
            captured.append(request)
            profiles.append(profile)
            if len(captured) == 1:
                raise ProviderUnavailableError()
            return await super().revise_from_review(request, profile)

    class Reviewer(DeterministicChapterReviewProvider):
        def _report(self, request, **kwargs):
            result = super()._report(request, **kwargs)
            first = result["findings"][0]
            if outcome == "note":
                first.update(severity="note", required=False)
            result["findings"].append({**first, "sequence": 2, "code": "optional_other",
                "severity": "note", "required": False, "rationale": "Unselected problem"})
            return result

    def composition():
        writer = Writer()
        reviewer = Reviewer(outcome="blocking" if outcome == "blocking" else "warning")
        return ChapterProductionV2Composition(writer_agent=WriterAgent(writer),
            revision_agent=RevisionAgent(writer), editor_agent=EditorAgent(reviewer),
            chief_editor_agent=ChiefEditorChapterFinalAgent(reviewer), lore_agent=LoreChapterFinalAgent(reviewer),
            chief_editor_required=False, phase_session_source=ChapterPhaseSessionSource(async_session.bind))

    v2_client._transport.app.dependency_overrides[get_chapter_production_v2_composition] = composition
    project, chapter, outline, version, _ = await _seed_approved_chapter(async_session, tmp_path / outcome)
    approved = await v2_client.post(f"/api/v1/projects/{project.id}/chapters/{chapter.id}/outline/approve",
        json={"document_id": str(outline.id), "expected_current_version_id": str(version.id)})
    assert approved.status_code == 200, approved.text
    base = _routes_url(project.id, chapter.id)
    started = await v2_client.post(base)
    assert started.status_code == 201, started.text
    data = started.json()
    run = f"{base}/{data['workflow_run_id']}"
    assert (await v2_client.post(f"{run}/actions/{data['action_request_id']}/resolve",
        json={"decision": "accept"})).status_code == 200
    assert (await v2_client.post(f"{run}/review")).status_code == 200
    if outcome == "note":
        assert (await v2_client.post(f"{run}/review")).status_code == 200
    state = (await v2_client.get(run)).json()
    reports = [state[key] for key in ("editor_report_id", "chief_editor_report_id", "lore_report_id") if state[key]]
    payload = dict(request_id=str(uuid4()), document_id=state["document_id"],
        version_id=state["document_version_id"], action_request_id=state["action_request_id"], report_ids=reports,
        selected_findings=[dict(report_id=reports[0], sequence=1)])
    # Unknown findings and omitted mandatory findings cannot consume the gate.
    bad = {**payload, "selected_findings": [dict(report_id=reports[0], sequence=99)]}
    assert (await v2_client.post(f"{run}/review-revisions", json=bad)).status_code == 422
    assert (await v2_client.get(run)).json() == state
    if outcome == "blocking":
        bad["selected_findings"][0]["sequence"] = 2
        assert (await v2_client.post(f"{run}/review-revisions", json=bad)).status_code == 422
        assert (await v2_client.get(run)).json() == state
    failed = await v2_client.post(f"{run}/review-revisions", json=payload)
    assert failed.status_code == 503, failed.text
    assert len(captured) == 1
    assert profiles[0].version == "v2" and "selected_findings" in profiles[0].context_policy.optional
    assert [(str(item.report_id), item.finding.sequence) for item in captured[0].selected_findings] == [(reports[0], 1)]
    assert captured[0].selected_findings[0].finding.rationale != "Unselected problem"
    changed = {**payload, "selected_findings": payload["selected_findings"] + [dict(report_id=reports[0], sequence=2)]}
    assert (await v2_client.post(f"{run}/review-revisions", json=changed)).status_code == 409
    assert len(captured) == 1
    if outcome == "note":
        from app.services.review_revision_saga import ReviewRevisionSaga
        from app.services.chapter_production_v2_contracts import ChapterProductionV2ReconciliationError
        original_finalize = ReviewRevisionSaga.finalize

        async def interrupt_finalize(self, identity, **kwargs):
            monkeypatch.setattr(ReviewRevisionSaga, "finalize", original_finalize)
            raise ChapterProductionV2ReconciliationError()

        monkeypatch.setattr(ReviewRevisionSaga, "finalize", interrupt_finalize)
        interrupted = await v2_client.post(f"{run}/review-revisions", json=payload)
        assert interrupted.status_code == 409, interrupted.text
        assert len(captured) == 2
    revised = await v2_client.post(f"{run}/review-revisions", json=payload)
    assert revised.status_code == 200, revised.text
    assert len(captured) == 2 and captured[0] == captured[1]
    assert revised.json()["draft_version_id"] != payload["version_id"]
    current = (await v2_client.get(run)).json()
    assert current["status"] == "EDITOR_REVIEW" and current["editor_report_id"] is None
    replay = await v2_client.post(f"{run}/review-revisions", json=payload)
    assert replay.status_code == 200 and replay.json() == revised.json()
    assert len(captured) == 2


async def test_full_chapter_production_v2_lifecycle_via_http(
    v2_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, outline_doc, outline_ver, owner = await _seed_approved_chapter(
        async_session, tmp_path / "v2_e2e"
    )
    base_url = _routes_url(project.id, chapter.id)

    # Confirmation goes through the same version-checked HTTP boundary as Studio.
    chapter.status = "OUTLINE_DISCUSSION"
    await async_session.commit()
    approval = await v2_client.post(
        f"/api/v1/projects/{project.id}/chapters/{chapter.id}/outline/approve",
        json={"document_id": str(outline_doc.id), "expected_current_version_id": str(outline_ver.id)},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["version_id"] == str(outline_ver.id)

    # 1. Start production from approved outline
    start_resp = await v2_client.post(base_url)
    assert start_resp.status_code == 201, start_resp.text
    started_data = start_resp.json()
    run_id = UUID(started_data["workflow_run_id"])
    author_action_id = UUID(started_data["action_request_id"])
    draft_doc_id = UUID(started_data["draft_document_id"])
    draft_ver_id = UUID(started_data["draft_version_id"])

    assert UUID(started_data["outline_document_id"]) == outline_doc.id
    assert UUID(started_data["outline_version_id"]) == outline_ver.id

    # 2. List runs
    list_resp = await v2_client.get(base_url)
    assert list_resp.status_code == 200, list_resp.text
    runs = list_resp.json()
    assert len(runs) >= 1
    assert any(r["workflow_run_id"] == str(run_id) for r in runs)

    # 3. Get run state
    get_resp = await v2_client.get(f"{base_url}/{run_id}")
    assert get_resp.status_code == 200, get_resp.text
    state_data = get_resp.json()
    assert state_data["chapter_workflow_run_id"] == str(run_id)
    assert state_data["status"] == ChapterProductionStatus.AUTHOR_REVISION.value
    assert state_data["awaiting_user"] is True
    assert state_data["document_id"] == str(draft_doc_id)
    assert state_data["document_version_id"] == str(draft_ver_id)

    # Autosaving repeatedly must preserve the pending author gate and its source.
    draft_url = f"/api/v1/projects/{project.id}/chapters/{chapter.id}/draft/content"
    for content in ("## A scene\n\nFirst edit.", "## A scene\n\nSecond edit.", "## A scene\n\nFinal saved edit."):
        saved = await v2_client.put(draft_url, json={"content": content, "expected_current_version_id": str(draft_ver_id)})
        assert saved.status_code == 200, saved.text
        assert saved.json()["actor_user_id"] == str(owner.id)
        draft_ver_id = UUID(saved.json()["id"])
    pending = (await v2_client.get(f"{base_url}/{run_id}")).json()
    assert pending["status"] == "AUTHOR_REVISION" and pending["action_request_id"] == str(author_action_id)
    assert pending["document_version_id"] == started_data["draft_version_id"]
    action_url = f"{base_url}/{run_id}/actions/{author_action_id}/resolve"
    # The legacy acceptance contract still rejects a multi-version successor.
    assert (await v2_client.post(action_url, json={"decision": "accept"})).status_code == 422
    stale = await v2_client.post(action_url, json={"decision": "accept", "expected_current_version_id": started_data["draft_version_id"]})
    assert stale.status_code == 409, stale.text
    before = await async_session.scalar(select(func.count()).select_from(DocumentVersion))

    # 4. Explicitly accept the latest saved version, without another document write.
    resolve_resp = await v2_client.post(
        action_url,
        json={"decision": "accept", "expected_current_version_id": str(draft_ver_id)},
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    updated_data = resolve_resp.json()
    assert updated_data["workflow_run_id"] == str(run_id)
    assert updated_data["draft_version_id"] == str(draft_ver_id)
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == before
    blocked_save = await v2_client.put(draft_url, json={"content": "Cannot change a reviewing draft", "expected_current_version_id": str(draft_ver_id)})
    assert blocked_save.status_code == 409

    # 5. Trigger reviews until REVISION_READY
    while True:
        get_resp = await v2_client.get(f"{base_url}/{run_id}")
        assert get_resp.status_code == 200
        state_data = get_resp.json()
        if state_data["status"] == ChapterProductionStatus.REVISION_READY.value:
            break
        review_resp = await v2_client.post(f"{base_url}/{run_id}/review")
        assert review_resp.status_code == 200, review_resp.text
        review_updated = review_resp.json()
        review_action_id = review_updated.get("action_request_id")
        if review_action_id is not None:
            proceed_resp = await v2_client.post(
                f"{base_url}/{run_id}/actions/{review_action_id}/resolve",
                json={"decision": "proceed_with_warnings"},
            )
            assert proceed_resp.status_code == 200, proceed_resp.text

    # Public reports retain exact version/evidence identity, never raw operation metadata.
    report_id = state_data["editor_report_id"]
    report_url = f"{base_url}/{run_id}/reports/{report_id}"
    report_response = await v2_client.get(report_url)
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["id"] == report_id and report["workflow_run_id"] == str(run_id)
    assert report["target_version_id"] == str(draft_ver_id)
    assert report["reviewer_role"] == "editor_agent"
    assert set(report) == {"id", "project_id", "chapter_id", "workflow_run_id", "reviewer_role",
                           "review_mode", "target_document_id", "target_version_id", "passed",
                           "summary", "findings", "suggested_actions"}
    for foreign_url in (
        f"{_routes_url(uuid4(), chapter.id)}/{run_id}/reports/{report_id}",
        f"{_routes_url(project.id, uuid4())}/{run_id}/reports/{report_id}",
        f"{base_url}/{uuid4()}/reports/{report_id}",
        f"{base_url}/{run_id}/reports/{uuid4()}",
    ):
        assert (await v2_client.get(foreign_url)).status_code == 404

    # Reuse workflow validation: corrupted evidence must not be exposed as a valid report.
    row = await async_session.get(ReviewReport, UUID(report_id))
    original_metadata = dict(row.raw_report)
    row.raw_report = {**original_metadata, "segment_map_hash": "0" * 64}
    await async_session.commit()
    corrupted = await v2_client.get(report_url)
    assert corrupted.status_code == 409, corrupted.text
    assert "segment_map_hash" not in corrupted.text
    row.raw_report = original_metadata
    await async_session.commit()

    # Optional readers receive the actual V2 snapshot, not a hash placeholder or metadata stub.
    from app.agents.reader_panel_fakes import DeterministicReaderPanelProvider
    captured = []
    original_read = DeterministicReaderPanelProvider.generate_initial_reading
    def capture_read(self, request):
        captured.append(request)
        return original_read(self, request)
    monkeypatch.setattr(DeterministicReaderPanelProvider, "generate_initial_reading", capture_read)
    ready_source = (await v2_client.get(f"{base_url}/{run_id}")).json()
    segments = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id, chapter_id=chapter.id, document_id=draft_doc_id,
        version_id=UUID(ready_source["document_version_id"]))
    reader_base = f"/api/v1/projects/{project.id}/chapters/{chapter.id}/reader-panels"
    reader_start = await v2_client.post(reader_base, json={"document_id": str(draft_doc_id),
        "document_version_id": ready_source["document_version_id"], "mode": "quick",
        "reader_profile_ids": ["studio_plot", "studio_world"], "idempotency_key": "actual-source"})
    assert reader_start.status_code == 201, reader_start.text
    reader_id = reader_start.json()["session_id"]
    for _ in range(6):
        reader_result = await v2_client.post(f"{reader_base}/{reader_id}/resume", json={})
        assert reader_result.status_code == 200, reader_result.text
        if not reader_result.json()["permitted_operations"]:
            break
    assert reader_result.json()["status"] in {"completed", "degraded_completed"}
    assert len(captured) == 2
    expected_segments = {str(item.segment_id): item.content for item in segments.segments}
    assert all(request.manuscript_segments == expected_segments for request in captured)
    assert (await v2_client.get(f"{base_url}/{run_id}")).json() == ready_source

    # 6. Explicit local finalization remains independent of advisory Reader results.
    stale_final = await v2_client.post(f"{base_url}/{run_id}/finalize",
        json={"expected_current_version_id": str(uuid4())})
    assert stale_final.status_code == 409
    before_final = (await v2_client.get(f"{base_url}/{run_id}")).json()
    assert before_final["status"] == "REVISION_READY"
    finalize_resp = await v2_client.post(f"{base_url}/{run_id}/finalize")
    assert finalize_resp.status_code == 200, finalize_resp.text
    finalized_data = finalize_resp.json()
    assert finalized_data["workflow_run_id"] == str(run_id)
    assert finalized_data["final_document_id"] is not None
    assert finalized_data["final_version_id"] is not None
    repeated_final = await v2_client.post(f"{base_url}/{run_id}/finalize",
        json={"expected_current_version_id": before_final["document_version_id"]})
    assert repeated_final.status_code == 200
    assert repeated_final.json() == finalized_data

    # 7. Check final state (completed)
    get_resp = await v2_client.get(f"{base_url}/{run_id}")
    assert get_resp.status_code == 200
    state_data = get_resp.json()
    assert state_data["status"] == ChapterProductionStatus.COMPLETED.value


async def test_extra_forbidden_and_cross_scope_isolation_via_http(
    v2_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, chapter, _, _, _ = await _seed_approved_chapter(
        async_session, tmp_path / "v2_isolation"
    )
    base_url = _routes_url(project.id, chapter.id)

    # 1. Extra field forbidden on start
    bad_start = await v2_client.post(base_url, json={"unexpected_field": "hacked"})
    assert bad_start.status_code == 422, bad_start.text
    assert bad_start.json()["error"]["code"] == "validation_error"

    # 2. Cross-project access returns 404
    foreign_proj_id = uuid4()
    foreign_url = _routes_url(foreign_proj_id, chapter.id)
    not_found_resp = await v2_client.get(foreign_url)
    assert not_found_resp.status_code == 404
    assert not_found_resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("invalid_step", ["agent_edit", "unattributed_edit", "expired_action"])
async def test_saved_version_acceptance_rejects_unproven_ancestry_and_expiry(
    v2_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path, invalid_step: str,
):
    project, chapter, _, _, owner = await _seed_approved_chapter(async_session, tmp_path / invalid_step)
    base = _routes_url(project.id, chapter.id)
    started = (await v2_client.post(base)).json()
    document_id, original_version = UUID(started["draft_document_id"]), UUID(started["draft_version_id"])
    action_id = UUID(started["action_request_id"])
    intermediate = await DocumentService(async_session).write_document(
        document_id=document_id, content="## Scene\n\nIntermediate edit.",
        expected_current_version_id=original_version,
        source=DocumentSource.WRITER_AGENT if invalid_step == "agent_edit" else DocumentSource.USER,
        actor_user_id=None if invalid_step == "unattributed_edit" else owner.id,
    )
    saved_url = f"/api/v1/projects/{project.id}/chapters/{chapter.id}/draft/content"
    saved = await v2_client.put(saved_url, json={"content": "## Scene\n\nLatest user edit.", "expected_current_version_id": str(intermediate.id)})
    assert saved.status_code == 200, saved.text
    if invalid_step == "expired_action":
        action = await async_session.get(ActionRequest, action_id)
        action.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await async_session.commit()
    before = await async_session.scalar(select(func.count()).select_from(DocumentVersion))
    response = await v2_client.post(f"{base}/{started['workflow_run_id']}/actions/{action_id}/resolve",
                                    json={"decision": "accept", "expected_current_version_id": saved.json()["id"]})
    assert response.status_code == 422, response.text
    assert await async_session.scalar(select(func.count()).select_from(DocumentVersion)) == before
    state = (await v2_client.get(f"{base}/{started['workflow_run_id']}")).json()
    assert state["status"] == "AUTHOR_REVISION" and state["action_request_id"] == str(action_id)
