"""PostgreSQL ASGI coverage for the public Reader Panel routes."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session, get_reader_panel_service
from app.main import create_app
from app.models.core import Chapter, Document, DocumentVersion, Project, ReviewReport, WorkflowRun
from app.models.reader_panel import ReaderPanelSession
from app.services.reader_panel_service import ReaderPanelService
from app.workflows.reader_panel import ReaderPanelStatus


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def reader_panel_client(
    async_session: AsyncSession,
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = override_db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _seed_chapter(
    session: AsyncSession, workspace_root: Path
) -> tuple[Project, Chapter, Document, DocumentVersion]:
    project = Project(
        slug=f"reader-panel-route-{uuid4().hex[:8]}",
        title="Reader Panel Route Test",
        workspace_root=str(workspace_root),
        metadata_={"target_audience": ["adult fantasy readers"]},
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1, title="Opening")
    session.add(chapter)
    await session.flush()
    document = Document(
        project_id=project.id,
        chapter_id=chapter.id,
        type="chapter_draft",
        title="Opening draft",
        path="chapters/001.md",
    )
    session.add(document)
    await session.flush()
    content = "The gate opened.\n\nNo one crossed it."
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        source="writer_agent",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        byte_size=len(content.encode()),
        word_count=8,
        file_path="chapters/001_v1.md",
        metadata_={"segments": {"S001": "The gate opened.", "S002": "No one crossed it."}},
    )
    session.add(version)
    await session.flush()
    document.current_version_id = version.id
    await session.commit()
    return project, chapter, document, version


def _url(project_id: UUID, chapter_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/chapters/{chapter_id}/reader-panels"


def _start(document: Document, version: DocumentVersion, key: str, **extra: object) -> dict:
    return {
        "document_id": str(document.id),
        "document_version_id": str(version.id),
        "mode": "quick",
        "idempotency_key": key,
        **extra,
    }


async def test_reader_panel_http_scope_noop_replay_pagination_and_lifecycle(
    reader_panel_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, chapter, document, version = await _seed_chapter(
        async_session, tmp_path / "reader_panel_routes"
    )
    base = _url(project.id, chapter.id)

    off = await reader_panel_client.post(
        base, json=_start(document, version, "off-request", mode="off")
    )
    assert off.status_code == 201, off.text
    assert off.json()["is_noop"] is True
    assert off.json()["session_id"] is None
    assert not (await async_session.scalars(select(ReaderPanelSession))).all()
    assert not (await async_session.scalars(select(WorkflowRun))).all()

    hostile = await reader_panel_client.post(
        base, json=_start(document, version, "hostile", provider="attacker")
    )
    assert hostile.status_code == 422

    payload = _start(document, version, "exact-request")
    started = await reader_panel_client.post(base, json=payload)
    replay = await reader_panel_client.post(base, json=payload)
    assert started.status_code == replay.status_code == 201
    session_id = UUID(started.json()["session_id"])
    assert replay.json()["session_id"] == str(session_id)
    assert len((await async_session.scalars(select(ReaderPanelSession))).all()) == 1

    conflict = await reader_panel_client.post(
        base, json={**payload, "test_goals": ["Different request"]}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "reader_panel_invalid_state"

    second = await reader_panel_client.post(
        base, json=_start(document, version, "second-request", mode="standard")
    )
    assert second.status_code == 201
    listed = await reader_panel_client.get(f"{base}?offset=0&limit=1")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    detail = await reader_panel_client.get(
        f"{base}/{session_id}?include_initial_reports=true&include_transcript=true&data_limit=5"
    )
    assert detail.status_code == 200
    assert detail.json()["initial_reports"] == []
    assert detail.json()["transcript"] == []
    assert "config_snapshot" not in detail.text
    assert "reader_profile_id" not in detail.text

    cross_scope = await reader_panel_client.get(f"{_url(uuid4(), chapter.id)}/{session_id}")
    assert cross_scope.status_code == 404
    wrong_version = await reader_panel_client.post(
        base,
        json={**_start(document, version, "wrong-version"), "document_version_id": str(uuid4())},
    )
    assert wrong_version.status_code == 404

    cancelled = await reader_panel_client.post(f"{base}/{session_id}/cancel", json={})
    cancelled_replay = await reader_panel_client.post(f"{base}/{session_id}/cancel", json={})
    resumed_cancelled = await reader_panel_client.post(f"{base}/{session_id}/resume", json={})
    assert (
        cancelled.status_code
        == cancelled_replay.status_code
        == resumed_cancelled.status_code
        == 200
    )
    assert resumed_cancelled.json()["status"] == ReaderPanelStatus.CANCELLED.value
    assert resumed_cancelled.json()["permitted_operations"] == []


async def test_reader_panel_http_stale_terminal_and_safe_editorial_projection(
    reader_panel_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, chapter, document, version = await _seed_chapter(
        async_session, tmp_path / "reader_panel_route_projection"
    )
    base = _url(project.id, chapter.id)
    started = await reader_panel_client.post(
        base, json=_start(document, version, "projection-request", mode="panel")
    )
    panel = await async_session.get(ReaderPanelSession, UUID(started.json()["session_id"]))
    assert panel is not None

    newer = DocumentVersion(
        document_id=document.id,
        version_number=2,
        source="writer_agent",
        content_hash="b" * 64,
        byte_size=3,
        word_count=1,
        file_path="chapters/001_v2.md",
        metadata_={"segments": {"S001": "New"}},
    )
    async_session.add(newer)
    await async_session.flush()
    document.current_version_id = newer.id
    panel.status = ReaderPanelStatus.DEGRADED_COMPLETED.value
    panel.degradation_reason = "reader_sample_degraded"
    report = ReviewReport(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_run_id=panel.workflow_run_id,
        review_mode="reader_panel",
        reviewer_agent_role="moderator_agent",
        target_document_id=document.id,
        target_version_id=version.id,
        passed=False,
        summary="One pacing experiment is recommended.",
        blocking_issues=[],
        warnings=["Sample degraded."],
        notes=[],
        suggested_actions=[
            {
                "priority": "experiment",
                "target_segment_ids": ["S002"],
                "suggested_action": "compress",
                "instruction": "Tighten S002.",
            }
        ],
        raw_report={"provider_response": "must-not-leak"},
        report_document_id=None,
    )
    async_session.add(report)
    await async_session.flush()
    panel.review_report_id = report.id
    await async_session.commit()

    response = await reader_panel_client.get(f"{base}/{panel.id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == ReaderPanelStatus.DEGRADED_COMPLETED.value
    assert data["stale"] is True
    assert data["degradation_reason"] == "reader_sample_degraded"
    assert data["document_version_id"] == str(version.id)
    assert data["review_report"]["summary"] == "One pacing experiment is recommended."
    assert data["review_report"]["suggested_actions"][0]["suggested_action"] == "compress"
    assert "raw_report" not in response.text
    assert "must-not-leak" not in response.text


async def test_concurrent_same_key_start_uses_one_canonical_session(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, chapter, document, version = await _seed_chapter(
        async_session, tmp_path / "reader_panel_concurrent_start"
    )
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    app: FastAPI = create_app()
    barrier = asyncio.Barrier(2)
    seen_sessions: set[int] = set()

    class BarrierReaderPanelService(ReaderPanelService):
        async def initialize_session(self, **kwargs):
            await asyncio.wait_for(barrier.wait(), timeout=5)
            return await super().initialize_session(**kwargs)

    async def independent_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def barrier_service(
        session: AsyncSession = Depends(get_db_session),
    ) -> ReaderPanelService:
        seen_sessions.add(id(session))
        return BarrierReaderPanelService(session)

    app.dependency_overrides[get_db_session] = independent_session
    app.dependency_overrides[get_reader_panel_service] = barrier_service
    payload = _start(document, version, "concurrent-exact-request")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first, second = await asyncio.gather(
            client.post(_url(project.id, chapter.id), json=payload),
            client.post(_url(project.id, chapter.id), json=payload),
        )

    assert first.status_code == second.status_code == 201
    assert first.json()["session_id"] == second.json()["session_id"]
    assert len(seen_sessions) == 2
    await async_session.rollback()
    sessions = (await async_session.scalars(select(ReaderPanelSession))).all()
    workflows = (await async_session.scalars(select(WorkflowRun))).all()
    assert len(sessions) == len(workflows) == 1
