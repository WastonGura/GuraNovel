"""Integration tests for ReaderPanelService against PostgreSQL."""

from __future__ import annotations

import hashlib
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.reader_panel_fakes import (
    DeterministicReaderPanelProvider,
    ReaderPanelFakeScenario,
)
from app.models.core import Chapter, Document, DocumentVersion, Project
from app.models.reader_panel import ReaderInitialReport, ReaderPanelSession
from app.services.reader_panel_service import (
    ReaderPanelNotFoundError,
    ReaderPanelService,
)
from app.workflows.reader_panel import PanelMode, ReaderPanelStatus


@pytest.mark.integration
@pytest.mark.anyio
class TestReaderPanelServiceIntegration:
    async def test_full_initial_reading_lifecycle_postgresql(
        self,
        async_session: AsyncSession,
    ) -> None:
        project_id = uuid4()
        chapter_id = uuid4()
        doc_id = uuid4()
        version_id = uuid4()

        segments = {
            "S001": "The autumn leaves swirled across the courtyards of the Spirit Academy.",
            "S002": "Master Hu gestured to the bronze cauldron emitting azure fumes.",
        }
        content = "\n\n".join(segments.values())
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        project = Project(
            id=project_id,
            slug=f"proj-{project_id.hex[:8]}",
            title="Academy Ascension",
            genre="xianxia",
            workspace_root=f"/tmp/workspaces/{project_id}",
            metadata_={"target_audience": ["cultivation_fans", "young_adult"]},
        )
        async_session.add(project)

        chapter = Chapter(
            id=chapter_id,
            project_id=project_id,
            chapter_number=1,
            title="Chapter 1: Azure Fumes",
        )
        async_session.add(chapter)

        doc = Document(
            id=doc_id,
            project_id=project_id,
            chapter_id=chapter_id,
            type="chapter_draft",
            title="Chapter 1 Draft",
            path="chapters/001.md",
            current_version_id=version_id,
        )
        async_session.add(doc)

        doc_version = DocumentVersion(
            id=version_id,
            document_id=doc_id,
            version_number=1,
            source="writer_agent",
            content_hash=content_hash,
            byte_size=len(content.encode("utf-8")),
            word_count=25,
            file_path="chapters/001_v1.md",
            metadata_={"segments": segments},
        )
        async_session.add(doc_version)
        await async_session.commit()

        # 1. Initialize session in standard mode
        service = ReaderPanelService(async_session)
        init_result = await service.initialize_session(
            project_id=project_id,
            chapter_id=chapter_id,
            mode=PanelMode.STANDARD,
            test_goals=["Check pacing and worldbuilding introduction"],
        )

        assert init_result.is_noop is False
        assert init_result.mode == "standard"
        assert init_result.planned_readers == 4
        assert init_result.session_id is not None
        assert init_result.workflow_run_id is not None

        # Verify DB session row
        session_row = await async_session.get(ReaderPanelSession, init_result.session_id)
        assert session_row is not None
        assert session_row.project_id == project_id
        assert session_row.chapter_id == chapter_id
        assert session_row.document_id == doc_id
        assert session_row.document_version_id == version_id
        assert session_row.source_hash == content_hash
        assert session_row.status == ReaderPanelStatus.INDEPENDENT_READING.value

        # 2. Idempotent initialization check
        dup_result = await service.initialize_session(
            project_id=project_id,
            chapter_id=chapter_id,
            mode=PanelMode.STANDARD,
        )
        assert dup_result.session_id == init_result.session_id

        # 3. Collect cold-read reports
        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.CLEAN)
        report_result = await service.collect_initial_reports(
            session_id=init_result.session_id,
            provider=provider,
        )

        assert report_result.initial_reports_locked is True
        assert report_result.completed_readers == 4
        assert report_result.status == ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value

        # Verify DB reports
        reports = (
            await async_session.execute(
                select(ReaderInitialReport).where(
                    ReaderInitialReport.session_id == init_result.session_id
                )
            )
        ).scalars().all()
        assert len(reports) == 4
        for r in reports:
            assert r.continue_reading == "yes"
            assert r.confidence == "high"
            assert r.locked is True
            assert r.locked_at is not None

    async def test_cross_project_rejection_postgresql(
        self,
        async_session: AsyncSession,
    ) -> None:
        p1_id = uuid4()
        p2_id = uuid4()
        chapter_id = uuid4()

        p1 = Project(
            id=p1_id,
            slug=f"proj-{p1_id.hex[:8]}",
            title="Project 1",
            workspace_root=f"/tmp/workspaces/{p1_id}",
        )
        p2 = Project(
            id=p2_id,
            slug=f"proj-{p2_id.hex[:8]}",
            title="Project 2",
            workspace_root=f"/tmp/workspaces/{p2_id}",
        )
        chapter = Chapter(
            id=chapter_id,
            project_id=p1_id,
            chapter_number=1,
            title="Chapter 1",
        )
        async_session.add_all([p1, p2, chapter])
        await async_session.commit()

        service = ReaderPanelService(async_session)
        # Cross project lookup must fail closed
        with pytest.raises(ReaderPanelNotFoundError):
            await service.initialize_session(
                project_id=p2_id,  # mismatch with chapter.project_id
                chapter_id=chapter_id,
                mode=PanelMode.QUICK,
            )
