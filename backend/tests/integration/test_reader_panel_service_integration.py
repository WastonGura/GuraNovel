"""Integration tests for ReaderPanelService against PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
from threading import Event
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.reader_panel_contracts import (
    ExtractedIssueItem,
    ModeratorDiscussionSummaryOutput,
    ModeratorIssueExtractionOutput,
    ReaderBallotOutput,
    ReaderDiscussionTurnOutput,
    ReaderFinalBallotOutput,
)
from app.agents.reader_panel_fakes import (
    DeterministicReaderPanelProvider,
    ReaderPanelFakeScenario,
)
from app.models.core import (
    ActionRequest,
    Chapter,
    Document,
    DocumentVersion,
    Project,
    ReviewReport,
    WorkflowEvent,
    WorkflowRun,
)
from app.models.reader_panel import (
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
    ReaderPanelInvocation,
    ReaderPanelMessage,
    ReaderPanelSession,
    ReaderRun,
)
from app.services.reader_panel_service import (
    ReaderPanelNotFoundError,
    ReaderPanelService,
)
from app.workflows.reader_panel import PanelMode, ReaderPanelStatus


class PostgreSQLBallotProvider:
    def __init__(self) -> None:
        self.extraction_calls = 0
        self.ballot_calls = 0

    def extract_issues(self, request) -> ModeratorIssueExtractionOutput:
        self.extraction_calls += 1
        source_profile = next(iter(request.reader_initial_reports.values()))["reader_profile_id"]
        return ModeratorIssueExtractionOutput(
            issues=[
                ExtractedIssueItem(
                    issue_number=7,
                    title="Abrupt introduction",
                    category="pacing",
                    symptom="The cauldron appears before its importance is established.",
                    root_cause_hypotheses=["Setup is compressed"],
                    evidence=[{"segment_ids": ["S002"], "note": "Cauldron introduction."}],
                    source_reader_ids=[source_profile],
                ),
                ExtractedIssueItem(
                    issue_number=9,
                    title="Thin academy context",
                    category="worldbuilding",
                    symptom="The academy context arrives without orientation.",
                    root_cause_hypotheses=["Setup is compressed"],
                    evidence=[{"segment_ids": ["S001"], "note": "Academy introduction."}],
                    source_reader_ids=[source_profile],
                ),
            ]
        )

    def generate_blind_ballot(self, request) -> ReaderBallotOutput:
        self.ballot_calls += 1
        segment_id = request.issue.evidence[0].segment_ids[0]
        return ReaderBallotOutput(
            issue_number=request.issue.issue_number,
            severity="minor",
            suggested_action="clarify",
            confidence="high",
            evidence=[{"segment_ids": [segment_id], "note": "Bound segment."}],
            reason="A short setup would orient the reader.",
        )


class PostgreSQLDiscussionProvider:
    def __init__(self) -> None:
        self.turn_calls = 0
        self.summary_calls = 0
        self.final_calls = 0

    def generate_discussion_turn(self, request) -> ReaderDiscussionTurnOutput:
        self.turn_calls += 1
        segment_id = next(iter(request.manuscript_segments))
        return ReaderDiscussionTurnOutput(
            stance="support",
            claim="One causal beat would clarify the transition.",
            evidence=[{"segment_ids": [segment_id], "note": "Bound issue evidence."}],
            proposed_action="Clarify the transition.",
            novelty="new_interpretation",
        )

    def summarize_discussion(self, request) -> ModeratorDiscussionSummaryOutput:
        self.summary_calls += 1
        return ModeratorDiscussionSummaryOutput(
            round_summary="Readers support a small clarification.",
            remaining_disagreements=[],
            suggested_focus="Add one causal beat.",
            is_consensus_reached=True,
        )

    def generate_final_ballot(self, request) -> ReaderFinalBallotOutput:
        self.final_calls += 1
        segment_id = next(iter(request.manuscript_segments))
        return ReaderFinalBallotOutput(
            issue_number=request.issue.issue_number,
            severity="minor",
            suggested_action="clarify",
            confidence="high",
            evidence=[{"segment_ids": [segment_id], "note": "Bound issue evidence."}],
            position_changed=True,
            change_reason="Discussion narrowed the change.",
        )


class PostgreSQLReportProvider(DeterministicReaderPanelProvider):
    def __init__(self) -> None:
        super().__init__(scenario=ReaderPanelFakeScenario.CLEAN)
        self.calls = 0

    def synthesize_report(self, request):
        self.calls += 1
        return super().synthesize_report(request)


@pytest.mark.integration
@pytest.mark.anyio
class TestReaderPanelServiceIntegration:
    async def test_concurrent_resume_and_cancel_preserve_committed_history(
        self,
        async_session: AsyncSession,
    ) -> None:
        project_id = uuid4()
        chapter_id = uuid4()
        document_id = uuid4()
        version_id = uuid4()
        content = "A version-bound opening."
        project = Project(
            id=project_id,
            slug=f"recovery-{project_id.hex[:8]}",
            title="Recovery",
            genre="fantasy",
            workspace_root=f"/tmp/workspaces/{project_id}",
        )
        chapter = Chapter(
            id=chapter_id,
            project_id=project_id,
            chapter_number=1,
            title="Opening",
        )
        document = Document(
            id=document_id,
            project_id=project_id,
            chapter_id=chapter_id,
            type="chapter_draft",
            title="Opening",
            path="chapters/001.md",
            current_version_id=None,
        )
        async_session.add_all([project, chapter, document])
        await async_session.flush()
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=1,
            source="writer_agent",
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            byte_size=len(content.encode()),
            word_count=4,
            file_path="chapters/001_v1.md",
            metadata_={"segments": {"S001": content}},
        )
        async_session.add(version)
        await async_session.flush()
        document.current_version_id = version_id
        await async_session.commit()

        initialized = await ReaderPanelService(async_session).initialize_session(
            project_id=project_id,
            chapter_id=chapter_id,
            mode=PanelMode.QUICK,
        )
        engine = async_session.bind
        assert engine is not None
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        started = Event()
        release = Event()

        class BlockingProvider(DeterministicReaderPanelProvider):
            def generate_initial_reading(self, request):
                started.set()
                assert release.wait(timeout=10)
                return super().generate_initial_reading(request)

        provider = BlockingProvider(scenario=ReaderPanelFakeScenario.CLEAN)

        async def resume():
            async with session_factory() as db:
                return await ReaderPanelService(db).resume_session(
                    session_id=initialized.session_id,
                    provider=provider,
                )

        async def cancel():
            async with session_factory() as db:
                return await ReaderPanelService(db).cancel_session(
                    session_id=initialized.session_id,
                )

        resume_task = asyncio.create_task(resume())
        assert await asyncio.to_thread(started.wait, 10)
        try:
            cancel_result = await cancel()
        finally:
            release.set()
        resume_result = await asyncio.gather(resume_task, return_exceptions=True)
        assert cancel_result.status == ReaderPanelStatus.CANCELLED.value
        assert len(resume_result) == 1
        durable = await async_session.get(
            ReaderPanelSession, initialized.session_id, populate_existing=True
        )
        assert durable is not None
        assert durable.status == ReaderPanelStatus.CANCELLED.value
        assert durable.current_step == ReaderPanelStatus.CANCELLED.value
        assert durable.completed_at is not None
        workflow = await async_session.get(
            WorkflowRun, initialized.workflow_run_id, populate_existing=True
        )
        assert workflow is not None
        assert workflow.status == ReaderPanelStatus.CANCELLED.value
        assert workflow.current_node == ReaderPanelStatus.CANCELLED.value
        assert workflow.next_node is None
        assert workflow.awaiting_user is False
        assert workflow.completed_at == durable.completed_at
        runs = (
            (
                await async_session.execute(
                    select(ReaderRun).where(ReaderRun.session_id == initialized.session_id)
                )
            )
            .scalars()
            .all()
        )
        reports = (
            (
                await async_session.execute(
                    select(ReaderInitialReport).where(
                        ReaderInitialReport.session_id == initialized.session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        attempts = (
            (
                await async_session.execute(
                    select(ReaderPanelInvocation).where(
                        ReaderPanelInvocation.session_id == initialized.session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) == 2
        assert reports == []
        assert len({report.reader_run_id for report in reports}) == len(reports)
        assert len({(item.phase, item.work_key, item.attempt) for item in attempts}) == len(
            attempts
        )
        assert attempts
        assert all(item.status != "started" for item in attempts)
        assert all(item.status == "cancelled" for item in attempts)
        events = (
            (
                await async_session.execute(
                    select(WorkflowEvent).where(
                        WorkflowEvent.workflow_run_id == initialized.workflow_run_id,
                        WorkflowEvent.event_type == "reader_panel.cancelled",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1

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
            current_version_id=None,
        )
        async_session.add(doc)
        await async_session.flush()

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
        await async_session.flush()

        doc.current_version_id = version_id
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
            (
                await async_session.execute(
                    select(ReaderInitialReport).where(
                        ReaderInitialReport.session_id == init_result.session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(reports) == 4
        for r in reports:
            assert r.continue_reading == "yes"
            assert r.confidence == "high"
            assert r.locked is True
            assert r.locked_at is not None

        # 4. Two independent sessions race safely: provider calls may repeat, rows may not.
        ballot_provider = PostgreSQLBallotProvider()
        engine = async_session.bind
        assert engine is not None
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def collect_concurrently():
            async with session_factory() as concurrent_session:
                return await ReaderPanelService(concurrent_session).resume_session(
                    session_id=init_result.session_id,
                    provider=ballot_provider,
                )

        concurrent_results = await asyncio.gather(
            collect_concurrently(),
            collect_concurrently(),
        )
        assert all(result.issue_count == 2 for result in concurrent_results)
        assert all(result.initial_ballot_count == 8 for result in concurrent_results)
        assert all(result.initial_ballots_locked for result in concurrent_results)
        assert all(
            result.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
            for result in concurrent_results
        )

        issues = (
            (
                await async_session.execute(
                    select(ReaderPanelIssue).where(
                        ReaderPanelIssue.session_id == init_result.session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        ballots = (
            (
                await async_session.execute(
                    select(ReaderPanelBallot).where(
                        ReaderPanelBallot.session_id == init_result.session_id,
                        ReaderPanelBallot.phase == "initial",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(issues) == 2
        assert all(len(issue.source_reader_ids) == 1 for issue in issues)
        assert all(
            issue.source_reader_ids[0] in {str(report.id) for report in reports} for issue in issues
        )
        assert len(ballots) == 8
        assert len({(b.reader_run_id, b.issue_id, b.phase) for b in ballots}) == 8
        assert all(b.session_id == init_result.session_id for b in ballots)
        events = (
            (
                await async_session.execute(
                    select(WorkflowEvent).where(
                        WorkflowEvent.workflow_run_id == init_result.workflow_run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 3
        assert {event.event_type for event in events} == {
            "reader_panel.issue_extraction_started",
            "reader_panel.issues_extracted",
            "reader_panel.initial_ballots_locked",
        }

        # PostgreSQL unique constraints and service replay both prevent duplicates.
        calls_before_replay = (
            ballot_provider.extraction_calls,
            ballot_provider.ballot_calls,
        )
        replay_result = await service.collect_initial_ballots(
            session_id=init_result.session_id,
            provider=ballot_provider,
        )
        assert replay_result.initial_ballot_count == 8
        assert (
            ballot_provider.extraction_calls,
            ballot_provider.ballot_calls,
        ) == calls_before_replay
        replay_ballots = (
            (
                await async_session.execute(
                    select(ReaderPanelBallot).where(
                        ReaderPanelBallot.session_id == init_result.session_id,
                        ReaderPanelBallot.phase == "initial",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(replay_ballots) == 8
        invocation_rows = (
            (
                await async_session.execute(
                    select(ReaderPanelInvocation).where(
                        ReaderPanelInvocation.session_id == init_result.session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        successful_keys = [
            (row.phase, row.work_key) for row in invocation_rows if row.status == "succeeded"
        ]
        assert len(successful_keys) == len(set(successful_keys))
        assert all(
            not hasattr(row, field)
            for row in invocation_rows
            for field in ("prompt", "response", "content", "endpoint", "credential", "path")
        )

        # 5. Discussion/final ballot recovery is also safe across two sessions.
        session_for_agenda = await async_session.get(
            ReaderPanelSession, init_result.session_id, populate_existing=True
        )
        assert session_for_agenda is not None
        config_snapshot = dict(session_for_agenda.config_snapshot)
        config_snapshot["max_discussion_issues"] = 1
        session_for_agenda.config_snapshot = config_snapshot
        await async_session.commit()
        discussion_provider = PostgreSQLDiscussionProvider()

        async def discuss_concurrently():
            async with session_factory() as concurrent_session:
                return await ReaderPanelService(concurrent_session).resume_session(
                    session_id=init_result.session_id,
                    provider=discussion_provider,
                )

        discussion_results = await asyncio.gather(
            discuss_concurrently(),
            discuss_concurrently(),
        )
        assert all(result.final_ballots_locked for result in discussion_results)
        assert all(result.final_ballot_count == 8 for result in discussion_results)

        messages = (
            (
                await async_session.execute(
                    select(ReaderPanelMessage).where(
                        ReaderPanelMessage.session_id == init_result.session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        final_ballots = (
            (
                await async_session.execute(
                    select(ReaderPanelBallot).where(
                        ReaderPanelBallot.session_id == init_result.session_id,
                        ReaderPanelBallot.phase == "final",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(messages) == 5
        assert len({(m.issue_id, m.round_number, m.turn_number) for m in messages}) == 5
        assert len(final_ballots) == 8
        assert len({(b.reader_run_id, b.issue_id, b.phase) for b in final_ballots}) == 8
        session_after_discussion = await async_session.get(
            ReaderPanelSession, init_result.session_id, populate_existing=True
        )
        assert session_after_discussion is not None
        first_locked_at = session_after_discussion.final_ballots_locked_at
        calls_before_final_replay = (
            discussion_provider.turn_calls,
            discussion_provider.summary_calls,
            discussion_provider.final_calls,
        )
        final_replay = await service.run_discussion_and_final_ballots(
            session_id=init_result.session_id,
            provider=discussion_provider,
        )
        assert final_replay.final_ballots_locked is True
        assert (
            discussion_provider.turn_calls,
            discussion_provider.summary_calls,
            discussion_provider.final_calls,
        ) == calls_before_final_replay
        replay_session = await async_session.get(
            ReaderPanelSession, init_result.session_id, populate_existing=True
        )
        assert replay_session is not None
        assert replay_session.final_ballots_locked_at == first_locked_at
        lifecycle_events = (
            (
                await async_session.execute(
                    select(WorkflowEvent).where(
                        WorkflowEvent.workflow_run_id == init_result.workflow_run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        event_types = [event.event_type for event in lifecycle_events]
        assert event_types.count("reader_panel.discussion_started") == 1
        assert event_types.count("reader_panel.discussion_round_completed") == 1
        assert event_types.count("reader_panel.discussion_completed") == 1
        assert event_types.count("reader_panel.final_ballots_locked") == 1

        # 6. Report generation remains bound to the reviewed version, even after
        # the chapter advances, and concurrent replay persists one canonical report.
        newer_version_id = uuid4()
        newer_content = f"{content}\n\nA later edit that must not be reviewed implicitly."
        newer_version = DocumentVersion(
            id=newer_version_id,
            document_id=doc_id,
            version_number=2,
            source="writer_agent",
            content_hash=hashlib.sha256(newer_content.encode("utf-8")).hexdigest(),
            byte_size=len(newer_content.encode("utf-8")),
            word_count=35,
            file_path="chapters/001_v2.md",
            metadata_={"segments": {**segments, "S003": "A later edit."}},
        )
        async_session.add(newer_version)
        await async_session.flush()
        doc.current_version_id = newer_version_id
        await async_session.commit()
        assert await service.reconcile_stale_status(session_id=init_result.session_id) is True

        document_count_before = len((await async_session.execute(select(Document))).scalars().all())
        version_count_before = len(
            (await async_session.execute(select(DocumentVersion))).scalars().all()
        )
        action_count_before = len(
            (await async_session.execute(select(ActionRequest))).scalars().all()
        )
        report_provider = PostgreSQLReportProvider()

        async def report_concurrently():
            async with session_factory() as concurrent_session:
                return await ReaderPanelService(concurrent_session).resume_session(
                    session_id=init_result.session_id,
                    provider=report_provider,
                )

        report_results = await asyncio.gather(
            report_concurrently(),
            report_concurrently(),
        )
        assert len(report_results) == 2
        assert all(result.review_report_id is not None for result in report_results)
        assert len({result.review_report_id for result in report_results}) == 1
        assert all(result.status == ReaderPanelStatus.COMPLETED.value for result in report_results)
        reports = (
            (
                await async_session.execute(
                    select(ReviewReport).where(
                        ReviewReport.workflow_run_id == init_result.workflow_run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(reports) == 1
        report = reports[0]
        assert report.id == report_results[0].review_report_id
        assert report.target_document_id == doc_id
        assert report.target_version_id == version_id
        assert report.raw_report["source_hash"] == content_hash
        assert report.raw_report["stale"] is True
        assert report.raw_report["automatic_application_allowed"] is False
        assert [item["issue_number"] for item in report.raw_report["issues"]] == [1, 2]
        assert report.passed is False
        assert report.report_document_id is None
        assert len((await async_session.execute(select(Document))).scalars().all()) == (
            document_count_before
        )
        assert (
            len((await async_session.execute(select(DocumentVersion))).scalars().all())
            == version_count_before
        )
        assert len((await async_session.execute(select(ActionRequest))).scalars().all()) == (
            action_count_before
        )
        completed_session = await async_session.get(
            ReaderPanelSession,
            init_result.session_id,
            populate_existing=True,
        )
        assert completed_session is not None
        completed_at = completed_session.completed_at
        completed_workflow = await async_session.get(
            WorkflowRun,
            init_result.workflow_run_id,
            populate_existing=True,
        )
        assert completed_workflow is not None
        assert completed_workflow.status == ReaderPanelStatus.COMPLETED.value
        assert completed_workflow.current_node == "completed"
        assert completed_workflow.next_node is None
        assert completed_workflow.awaiting_user is False
        assert completed_workflow.completed_at == completed_at
        assert all(
            result.status == completed_session.status == completed_workflow.status
            for result in report_results
        )
        assert report_provider.calls in {1, 2}
        report_calls_before_replay = report_provider.calls
        replay_report = await service.generate_editor_handoff_report(
            session_id=init_result.session_id,
            provider=report_provider,
        )
        assert replay_report.review_report_id == report.id
        assert report_provider.calls == report_calls_before_replay
        replayed_session = await async_session.get(
            ReaderPanelSession,
            init_result.session_id,
            populate_existing=True,
        )
        assert replayed_session is not None
        assert replayed_session.completed_at == completed_at
        replayed_workflow = await async_session.get(
            WorkflowRun,
            init_result.workflow_run_id,
            populate_existing=True,
        )
        assert replayed_workflow is not None
        assert replayed_workflow.completed_at == completed_at
        report_events = (
            (
                await async_session.execute(
                    select(WorkflowEvent).where(
                        WorkflowEvent.workflow_run_id == init_result.workflow_run_id,
                        WorkflowEvent.event_type == "reader_panel.report_completed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(report_events) == 1

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
