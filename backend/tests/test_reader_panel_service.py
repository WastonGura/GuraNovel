"""Unit tests for ReaderPanelService initialization, cold-reading collection, and version locking."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any
from uuid import UUID, uuid4
import pytest

from app.agents.reader_panel_fakes import (
    DeterministicReaderPanelProvider,
    ReaderPanelFakeScenario,
)
from app.agents.reader_panel_contracts import (
    EvidenceRef,
    ExtractedIssueItem,
    ModeratorDiscussionSummaryOutput,
    ModeratorIssueExtractionOutput,
    ReaderBallotOutput,
    ReaderDiscussionTurnOutput,
    ReaderFinalBallotOutput,
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
    ReaderPanelMessage,
    ReaderPanelSession,
    ReaderRun,
)
from app.services.reader_panel_service import (
    ReaderPanelInvalidStateError,
    ReaderPanelNotFoundError,
    ReaderPanelQuorumError,
    ReaderPanelService,
)
from app.workflows.reader_panel import PanelMode, ReaderPanelStatus


class FakeExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> FakeExecuteResult:
        return self

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any | None:
        return self._items[0] if self._items else None


class FakeAsyncSession:
    """Lightweight in-memory AsyncSession for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self.storage: dict[type, dict[Any, Any]] = defaultdict(dict)
        self.transaction_active = False
        self.get_options: list[tuple[type, dict[str, Any]]] = []
        self.execute_options: list[tuple[str, dict[str, Any]]] = []

    async def get(self, model_cls: type, pk: Any, **kwargs: Any) -> Any | None:
        self.transaction_active = True
        self.get_options.append((model_cls, kwargs))
        return self.storage[model_cls].get(pk)

    def add(self, obj: Any) -> None:
        self.transaction_active = True
        if not hasattr(obj, "id") or obj.id is None:
            obj.id = uuid4()
        self.storage[type(obj)][obj.id] = obj

    async def flush(self) -> None:
        self.transaction_active = True

    async def commit(self) -> None:
        self.transaction_active = False

    def in_transaction(self) -> bool:
        return self.transaction_active

    async def execute(self, stmt: Any) -> FakeExecuteResult:
        self.transaction_active = True
        # Simple evaluation based on statement target table
        stmt_str = str(stmt)
        self.execute_options.append((stmt_str, dict(stmt.get_execution_options())))
        if "reader_panel_sessions" in stmt_str:
            sessions = list(self.storage[ReaderPanelSession].values())
            return FakeExecuteResult(sessions)
        elif "reader_runs" in stmt_str:
            runs = list(self.storage[ReaderRun].values())
            return FakeExecuteResult(runs)
        elif "reader_initial_reports" in stmt_str:
            reports = list(self.storage[ReaderInitialReport].values())
            return FakeExecuteResult(reports)
        elif "reader_panel_issues" in stmt_str:
            return FakeExecuteResult(list(self.storage[ReaderPanelIssue].values()))
        elif "reader_panel_ballots" in stmt_str:
            return FakeExecuteResult(list(self.storage[ReaderPanelBallot].values()))
        elif "reader_panel_messages" in stmt_str:
            return FakeExecuteResult(list(self.storage[ReaderPanelMessage].values()))
        elif "documents" in stmt_str:
            docs = list(self.storage[Document].values())
            return FakeExecuteResult(docs)
        elif "workflow_runs" in stmt_str:
            wfs = list(self.storage[WorkflowRun].values())
            return FakeExecuteResult(wfs)
        elif "workflow_events" in stmt_str:
            return FakeExecuteResult(list(self.storage[WorkflowEvent].values()))
        elif "review_reports" in stmt_str:
            return FakeExecuteResult(list(self.storage[ReviewReport].values()))
        return FakeExecuteResult([])


@pytest.fixture
def fake_db_session() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def test_project_id() -> UUID:
    return uuid4()


@pytest.fixture
def test_chapter_id() -> UUID:
    return uuid4()


@pytest.fixture
def test_document_id() -> UUID:
    return uuid4()


@pytest.fixture
def test_version_id() -> UUID:
    return uuid4()


@pytest.fixture
def sample_segments() -> dict[str, str]:
    return {
        "S001": "The rain beat steadily against the high windows of the ancestral manor.",
        "S002": "Elder Lin opened the ancient scroll, revealing glowing runes of celestial power.",
        "S003": "Lin Yan tightened his grip on his wooden sword, his heart racing.",
    }


@pytest.fixture
def sample_content(sample_segments: dict[str, str]) -> str:
    return "\n\n".join(sample_segments.values())


@pytest.fixture
def sample_hash(sample_content: str) -> str:
    return hashlib.sha256(sample_content.encode("utf-8")).hexdigest()


def setup_test_entities(
    db: FakeAsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content_hash: str,
    segments: dict[str, str],
) -> tuple[Project, Chapter, Document, DocumentVersion]:
    project = Project(
        id=project_id,
        slug="test-project",
        title="Test Project",
        genre="progression_fantasy",
        workspace_root="/tmp/test",
        metadata_={
            "target_audience": ["young_adult", "fantasy_readers"],
            "logline": "A test story of ascension.",
        },
    )
    db.add(project)

    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        chapter_number=1,
        title="Chapter 1: The Storm",
    )
    db.add(chapter)

    doc = Document(
        id=document_id,
        project_id=project_id,
        chapter_id=chapter_id,
        type="chapter_draft",
        title="Chapter 1 Draft",
        path="chapters/001.md",
        current_version_id=version_id,
    )
    db.add(doc)

    doc_version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        source="writer_agent",
        content_hash=content_hash,
        byte_size=len("".join(segments.values()).encode("utf-8")),
        word_count=50,
        file_path="chapters/001_v1.md",
        metadata_={"segments": segments},
    )
    db.add(doc_version)
    return project, chapter, doc, doc_version


@pytest.mark.anyio
class TestReaderPanelServiceInitialization:
    async def test_mode_off_returns_noop_without_db_side_effects(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        service = ReaderPanelService(fake_db_session)  # type: ignore
        result = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.OFF,
        )

        assert result.is_noop is True
        assert result.mode == "off"
        assert result.session_id is None
        assert result.workflow_run_id is None
        assert len(fake_db_session.storage[ReaderPanelSession]) == 0
        assert len(fake_db_session.storage[WorkflowRun]) == 0

    async def test_mode_quick_initialization_creates_bound_session_and_runs(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        service = ReaderPanelService(fake_db_session)  # type: ignore
        result = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.QUICK,
            test_goals=["Assess opening scene tension"],
        )

        assert result.is_noop is False
        assert result.mode == "quick"
        assert result.session_id is not None
        assert result.workflow_run_id is not None
        assert result.planned_readers == 2
        assert result.status == ReaderPanelStatus.INDEPENDENT_READING.value

        # Verify persisted ReaderPanelSession
        db_session_obj = await fake_db_session.get(ReaderPanelSession, result.session_id)
        assert db_session_obj is not None
        assert db_session_obj.project_id == test_project_id
        assert db_session_obj.chapter_id == test_chapter_id
        assert db_session_obj.document_id == test_document_id
        assert db_session_obj.document_version_id == test_version_id
        assert db_session_obj.source_hash == sample_hash
        assert db_session_obj.test_goals == ["Assess opening scene tension"]

        # Verify ReaderRun records
        reader_runs = list(fake_db_session.storage[ReaderRun].values())
        assert len(reader_runs) == 2
        profiles = {r.reader_profile_id for r in reader_runs}
        assert profiles == {"general_immersive", "low_patience"}
        for r in reader_runs:
            assert r.status == "pending"

    async def test_initialization_fails_when_project_not_found(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
    ) -> None:
        service = ReaderPanelService(fake_db_session)  # type: ignore
        with pytest.raises(ReaderPanelNotFoundError):
            await service.initialize_session(
                project_id=test_project_id,
                chapter_id=test_chapter_id,
                mode=PanelMode.STANDARD,
            )


@pytest.mark.anyio
class TestReaderPanelColdReadingCollection:
    async def test_collect_initial_reports_locks_valid_quorum(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        service = ReaderPanelService(fake_db_session)  # type: ignore
        init_res = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.QUICK,
        )

        # Wire relationship back-references on fake session object
        panel_session = await fake_db_session.get(ReaderPanelSession, init_res.session_id)
        panel_session.reader_runs = list(fake_db_session.storage[ReaderRun].values())
        panel_session.document = await fake_db_session.get(Document, test_document_id)

        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.CLEAN)
        report_res = await service.collect_initial_reports(
            session_id=init_res.session_id,
            provider=provider,
        )

        assert report_res.status == ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value
        assert report_res.initial_reports_locked is True
        assert report_res.completed_readers == 2
        assert len(report_res.reports) == 2

        reports_in_db = list(fake_db_session.storage[ReaderInitialReport].values())
        assert len(reports_in_db) == 2
        for rep in reports_in_db:
            assert rep.continue_reading == "yes"
            assert rep.confidence == "high"
            assert isinstance(rep.strengths, list)
            assert isinstance(rep.concerns, list)

    async def test_degraded_quorum_when_partial_readers_fail(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        service = ReaderPanelService(fake_db_session)  # type: ignore
        init_res = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.STANDARD,  # Standard: 4 planned, min_valid = 3
        )

        panel_session = await fake_db_session.get(ReaderPanelSession, init_res.session_id)
        panel_session.reader_runs = list(fake_db_session.storage[ReaderRun].values())
        panel_session.document = await fake_db_session.get(Document, test_document_id)

        # Provider that fails for one specific reader profile
        class PartialFailureProvider(DeterministicReaderPanelProvider):
            def generate_initial_reading(self, request):
                if request.reader_profile_id == "character_emotion":
                    raise RuntimeError("Simulated transient reader network error")
                return super().generate_initial_reading(request)

        provider = PartialFailureProvider(scenario=ReaderPanelFakeScenario.CLEAN)
        report_res = await service.collect_initial_reports(
            session_id=init_res.session_id,
            provider=provider,
        )

        assert report_res.status == ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value
        assert report_res.initial_reports_locked is True
        assert report_res.completed_readers == 3
        assert report_res.degradation_reason is not None

        runs = list(fake_db_session.storage[ReaderRun].values())
        failed_runs = [r for r in runs if r.status == "failed"]
        assert len(failed_runs) == 1
        assert failed_runs[0].reader_profile_id == "character_emotion"

    async def test_quorum_failure_when_below_min_valid_readers(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        service = ReaderPanelService(fake_db_session)  # type: ignore
        init_res = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.QUICK,  # Quick: 2 planned, min_valid = 2
        )

        panel_session = await fake_db_session.get(ReaderPanelSession, init_res.session_id)
        panel_session.reader_runs = list(fake_db_session.storage[ReaderRun].values())
        panel_session.document = await fake_db_session.get(Document, test_document_id)

        provider = DeterministicReaderPanelProvider(
            scenario=ReaderPanelFakeScenario.MALFORMED_OUTPUT
        )
        with pytest.raises(ReaderPanelQuorumError):
            await service.collect_initial_reports(
                session_id=init_res.session_id,
                provider=provider,
            )

        db_session_obj = await fake_db_session.get(ReaderPanelSession, init_res.session_id)
        assert db_session_obj.status == ReaderPanelStatus.FAILED.value
        assert db_session_obj.failure_reason is not None

    async def test_staleness_reconciled_when_chapter_version_changes(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        service = ReaderPanelService(fake_db_session)  # type: ignore
        init_res = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.QUICK,
        )

        panel_session = await fake_db_session.get(ReaderPanelSession, init_res.session_id)
        doc = await fake_db_session.get(Document, test_document_id)
        panel_session.document = doc

        # Author mutates chapter to version 2
        v2_id = uuid4()
        v2 = DocumentVersion(
            id=v2_id,
            document_id=test_document_id,
            version_number=2,
            source="manual_edit",
            content_hash=hashlib.sha256(b"mutated text").hexdigest(),
            byte_size=12,
            word_count=2,
            file_path="chapters/001_v2.md",
            metadata_={"segments": {"S001": "mutated text"}},
        )
        fake_db_session.add(v2)
        doc.current_version_id = v2_id

        # Check staleness reconciliation
        is_stale = await service.reconcile_stale_status(session_id=init_res.session_id)
        assert is_stale is True

        db_session_obj = await fake_db_session.get(ReaderPanelSession, init_res.session_id)
        assert db_session_obj.stale is True


class RecordingBallotProvider:
    def __init__(
        self,
        issues: list[ExtractedIssueItem],
        db: FakeAsyncSession | None = None,
    ) -> None:
        self.issues = issues
        self.db = db
        self.moderator_calls = 0
        self.moderator_requests: list[Any] = []
        self.ballot_requests: list[Any] = []

    def extract_issues(self, request: Any) -> ModeratorIssueExtractionOutput:
        assert self.db is None or not self.db.in_transaction()
        self.moderator_calls += 1
        self.moderator_requests.append(request)
        return ModeratorIssueExtractionOutput(issues=self.issues)

    def generate_blind_ballot(self, request: Any) -> ReaderBallotOutput:
        assert self.db is None or not self.db.in_transaction()
        self.ballot_requests.append(request)
        return ReaderBallotOutput(
            issue_number=request.issue.issue_number,
            severity="significant",
            suggested_action="clarify",
            confidence="high",
            evidence=[{"segment_ids": ["S001"], "note": "Opening evidence."}],
            reason="The opening needs a clearer causal link.",
        )


async def setup_locked_panel(
    db: FakeAsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content_hash: str,
    segments: dict[str, str],
) -> tuple[ReaderPanelService, ReaderPanelSession]:
    setup_test_entities(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document_id,
        version_id=version_id,
        content_hash=content_hash,
        segments=segments,
    )
    service = ReaderPanelService(db)  # type: ignore[arg-type]
    initialized = await service.initialize_session(
        project_id=project_id,
        chapter_id=chapter_id,
        mode=PanelMode.QUICK,
    )
    panel_session = await db.get(ReaderPanelSession, initialized.session_id)
    panel_session.reader_runs = list(db.storage[ReaderRun].values())
    panel_session.document = await db.get(Document, document_id)
    await service.collect_initial_reports(
        session_id=panel_session.id,
        provider=DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.CLEAN),
    )
    panel_session.initial_reports = list(db.storage[ReaderInitialReport].values())
    return service, panel_session


@pytest.mark.anyio
class TestReaderPanelInitialBallots:
    async def test_requires_locked_reports_before_any_provider_or_write(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        setup_test_entities(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        service = ReaderPanelService(fake_db_session)  # type: ignore[arg-type]
        initialized = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.QUICK,
        )
        panel_session = await fake_db_session.get(ReaderPanelSession, initialized.session_id)
        panel_session.reader_runs = list(fake_db_session.storage[ReaderRun].values())
        provider = RecordingBallotProvider([])

        with pytest.raises(ReaderPanelInvalidStateError):
            await service.collect_initial_ballots(session_id=panel_session.id, provider=provider)

        assert provider.moderator_calls == 0
        assert provider.ballot_requests == []
        assert fake_db_session.storage[ReaderPanelIssue] == {}
        assert fake_db_session.storage[ReaderPanelBallot] == {}

    async def test_extracts_dedupes_and_collects_isolated_blind_ballots(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        source_run = panel_session.reader_runs[0]
        source_profile = source_run.reader_profile_id
        issues = [
            ExtractedIssueItem(
                issue_number=4,
                title="  Unclear opening  ",
                category="clarity",
                symptom="The opening link is unclear.",
                root_cause_hypotheses=["Missing transition"],
                evidence=[{"segment_ids": ["S001"], "note": "Opening."}],
                source_reader_ids=[
                    source_profile,
                    str(source_run.id),
                    str(source_run.initial_report.id),
                ],
                target_audience_relevance="high",
            ),
            ExtractedIssueItem(
                issue_number=9,
                title="unclear opening",
                category="CLARITY",
                symptom="The opening link is unclear.",
                root_cause_hypotheses=["Missing transition"],
                evidence=[{"segment_ids": ["S001", "S001"], "note": "Same location."}],
                source_reader_ids=[source_profile],
                target_audience_relevance="high",
            ),
        ]
        provider = RecordingBallotProvider(issues, fake_db_session)

        result = await service.collect_initial_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        persisted_issues = list(fake_db_session.storage[ReaderPanelIssue].values())
        ballots = list(fake_db_session.storage[ReaderPanelBallot].values())
        assert result.issue_count == 1
        assert result.initial_ballot_count == 2
        assert result.initial_ballots_locked is True
        assert result.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
        assert len(persisted_issues) == 1
        assert persisted_issues[0].issue_number == 1
        assert persisted_issues[0].title == "Unclear opening"
        source_report_id = panel_session.reader_runs[0].initial_report.id
        assert set(provider.moderator_requests[0].reader_initial_reports) == {
            str(run.initial_report.id) for run in panel_session.reader_runs
        }
        assert persisted_issues[0].source_reader_ids == [str(source_report_id)]
        assert len(ballots) == 2
        assert {b.reader_run_id for b in ballots} == {r.id for r in panel_session.reader_runs}
        assert all(b.phase == "initial" for b in ballots)
        assert all(b.severity == "significant" for b in ballots)
        assert all(b.suggested_action == "clarify" for b in ballots)
        assert all(b.confidence == "high" for b in ballots)
        assert len(provider.ballot_requests) == 2  # Moderator is not a voting reader.
        for request in provider.ballot_requests:
            payload = request.model_dump(mode="json")
            assert request.issue.source_reader_ids == []
            assert "reader_initial_reports" not in payload
            assert "provenance" not in str(payload).lower()
            assert "vote" not in str(payload).lower()
            assert "moderator" not in str(payload).lower()
        event_types = [
            event.event_type for event in fake_db_session.storage[WorkflowEvent].values()
        ]
        assert event_types == [
            "reader_panel.issue_extraction_started",
            "reader_panel.issues_extracted",
            "reader_panel.initial_ballots_locked",
        ]
        allowed_payload_keys = {"session_id", "status", "issue_count", "ballot_count"}
        assert all(
            set(event.payload).issubset(allowed_payload_keys)
            and event.message is None
            and event.actor_id is None
            and event.event_sequence is None
            for event in fake_db_session.storage[WorkflowEvent].values()
        )


@pytest.mark.anyio
class TestReaderPanelDiscussionBoundsAndAgenda:
    async def test_empty_agenda_skips_providers_and_locks_empty_final_phase(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        panel_session.config_snapshot["max_discussion_issues"] = 0
        provider = RecordingDiscussionProvider(fake_db_session)

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.status == ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value
        assert result.discussed_issue_count == 0
        assert result.final_ballot_count == 2
        assert provider.turn_requests == []
        assert provider.summary_requests == []
        assert len(provider.final_requests) == 2
        assert all(
            issue.discussion_status == "skipped"
            for issue in fake_db_session.storage[ReaderPanelIssue].values()
        )

    async def test_malformed_discussion_turn_retries_once_and_remains_incomplete(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        class MalformedDiscussionProvider(RecordingDiscussionProvider):
            def generate_discussion_turn(self, request: Any) -> Any:
                assert not self.db.in_transaction()
                self.turn_requests.append(request)
                return object()

        provider = MalformedDiscussionProvider(fake_db_session)
        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert len(provider.turn_requests) == 2
        assert result.status == ReaderPanelStatus.DISCUSSING.value
        assert result.discussion_message_count == 0
        assert result.final_ballot_count == 0
        assert panel_session.final_ballots_locked_at is None

    async def test_agenda_prefers_server_owned_initial_severity(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        extracted = [
            ExtractedIssueItem(
                issue_number=number,
                title=f"Issue {number}",
                category="clarity",
                symptom=f"Symptom {number}",
                root_cause_hypotheses=["Cause"],
                evidence=[{"segment_ids": [f"S00{number}"], "note": "Bound."}],
                source_reader_ids=[panel_session.reader_runs[0].reader_profile_id],
                minority_risk=number == 2,
            )
            for number in (1, 2)
        ]

        class PriorityBallotProvider(RecordingBallotProvider):
            def generate_blind_ballot(self, request: Any) -> ReaderBallotOutput:
                self.ballot_requests.append(request)
                return ReaderBallotOutput(
                    issue_number=request.issue.issue_number,
                    severity=("critical" if request.issue.issue_number == 1 else "minor"),
                    suggested_action="clarify",
                    confidence="high",
                    evidence=[
                        {
                            "segment_ids": [f"S00{request.issue.issue_number}"],
                            "note": "Bound.",
                        }
                    ],
                    reason="Priority evidence.",
                )

        await service.collect_initial_ballots(
            session_id=panel_session.id,
            provider=PriorityBallotProvider(extracted),
        )
        panel_session.issues = list(fake_db_session.storage[ReaderPanelIssue].values())
        panel_session.ballots = list(fake_db_session.storage[ReaderPanelBallot].values())
        panel_session.messages = []
        panel_session.config_snapshot.update(
            {
                "max_discussion_issues": 1,
                "max_rounds_per_issue": 1,
                "max_total_model_calls": 20,
                "max_input_tokens_per_call": 10_000,
                "max_execution_seconds": 30,
            }
        )
        provider = RecordingDiscussionProvider(fake_db_session)

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.final_ballots_locked is True
        assert {request.issue.issue_number for request in provider.turn_requests} == {2}
        assert {request.issue.issue_number for request in provider.final_requests} == {1, 2}
        assert (
            len(
                [
                    ballot
                    for ballot in fake_db_session.storage[ReaderPanelBallot].values()
                    if ballot.phase == "final"
                ]
            )
            == 4
        )
        discussed_issue_ids = {
            message.issue_id for message in fake_db_session.storage[ReaderPanelMessage].values()
        }
        assert discussed_issue_ids == {
            next(
                issue.id
                for issue in fake_db_session.storage[ReaderPanelIssue].values()
                if issue.issue_number == 2
            )
        }
        statuses = {
            issue.issue_number: issue.discussion_status
            for issue in fake_db_session.storage[ReaderPanelIssue].values()
        }
        assert statuses == {1: "skipped", 2: "closed"}


class RecordingDiscussionProvider:
    def __init__(self, db: FakeAsyncSession, *, malformed_final_profile: str | None = None) -> None:
        self.db = db
        self.malformed_final_profile = malformed_final_profile
        self.turn_requests: list[Any] = []
        self.summary_requests: list[Any] = []
        self.final_requests: list[Any] = []

    def generate_discussion_turn(self, request: Any) -> ReaderDiscussionTurnOutput:
        assert not self.db.in_transaction()
        self.turn_requests.append(request)
        segment_id = next(iter(request.manuscript_segments))
        return ReaderDiscussionTurnOutput(
            stance="support",
            claim="The transition needs one concrete causal beat.",
            evidence=[{"segment_ids": [segment_id], "note": "Opening transition."}],
            concession=None,
            proposed_action="Clarify the causal transition.",
            novelty="new_interpretation",
        )

    def summarize_discussion(self, request: Any) -> ModeratorDiscussionSummaryOutput:
        assert not self.db.in_transaction()
        self.summary_requests.append(request)
        return ModeratorDiscussionSummaryOutput(
            round_summary="Readers agree that one causal beat would clarify the opening.",
            remaining_disagreements=[],
            suggested_focus="Add one causal beat.",
            is_consensus_reached=True,
        )

    def generate_final_ballot(self, request: Any) -> Any:
        assert not self.db.in_transaction()
        self.final_requests.append(request)
        if request.reader_profile_id == self.malformed_final_profile:
            return object()
        segment_id = next(iter(request.manuscript_segments))
        return ReaderFinalBallotOutput(
            issue_number=request.issue.issue_number,
            severity="minor",
            suggested_action="clarify",
            confidence="high",
            evidence=[{"segment_ids": [segment_id], "note": "Bound opening evidence."}],
            position_changed=True,
            change_reason="The discussion narrowed the fix.",
            remaining_disagreement=None,
        )


class RecordingSynthesisProvider(DeterministicReaderPanelProvider):
    def __init__(self, db: FakeAsyncSession) -> None:
        super().__init__(scenario=ReaderPanelFakeScenario.CLEAN)
        self.db = db
        self.requests: list[Any] = []

    def synthesize_report(self, request: Any) -> Any:
        assert not self.db.in_transaction()
        self.requests.append(request)
        return super().synthesize_report(request)


async def setup_initial_ballots_locked(
    db: FakeAsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content_hash: str,
    segments: dict[str, str],
) -> tuple[ReaderPanelService, ReaderPanelSession]:
    service, panel_session = await setup_locked_panel(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document_id,
        version_id=version_id,
        content_hash=content_hash,
        segments=segments,
    )
    await service.collect_initial_ballots(
        session_id=panel_session.id,
        provider=RecordingBallotProvider(
            [
                ExtractedIssueItem(
                    issue_number=1,
                    title="Unclear opening",
                    category="clarity",
                    symptom="The opening link is unclear.",
                    root_cause_hypotheses=["Missing transition"],
                    evidence=[{"segment_ids": ["S001"], "note": "Opening."}],
                    source_reader_ids=[panel_session.reader_runs[0].reader_profile_id],
                )
            ]
        ),
    )
    panel_session.issues = list(db.storage[ReaderPanelIssue].values())
    panel_session.ballots = list(db.storage[ReaderPanelBallot].values())
    panel_session.messages = []
    panel_session.config_snapshot.update(
        {
            "max_discussion_issues": 1,
            "max_rounds_per_issue": 2,
            "max_total_model_calls": 20,
            "max_input_tokens_per_call": 10_000,
            "max_execution_seconds": 30,
        }
    )
    return service, panel_session


@pytest.mark.anyio
class TestReaderPanelDiscussionAndFinalBallots:
    async def test_discusses_issue_in_isolation_and_locks_immutable_final_ballots(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        provider = RecordingDiscussionProvider(fake_db_session)

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        messages = list(fake_db_session.storage[ReaderPanelMessage].values())
        ballots = list(fake_db_session.storage[ReaderPanelBallot].values())
        initial_ballots = [ballot for ballot in ballots if ballot.phase == "initial"]
        final_ballots = [ballot for ballot in ballots if ballot.phase == "final"]
        assert result.status == ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value
        assert result.discussion_message_count == 3
        assert result.final_ballot_count == 2
        assert result.final_ballots_locked is True
        assert len(messages) == 3
        assert [(m.round_number, m.turn_number) for m in messages] == [(1, 1), (1, 2), (1, 3)]
        assert [m.speaker_type for m in messages] == ["reader", "reader", "moderator"]
        assert messages[-1].reader_run_id is None
        assert messages[-1].stance is None
        assert messages[-1].novelty == "procedural"
        assert len(initial_ballots) == 2
        assert len(final_ballots) == 2
        assert {ballot.reader_run_id for ballot in final_ballots} == {
            run.id for run in panel_session.reader_runs
        }
        assert all(ballot.position_changed is True for ballot in final_ballots)
        assert all(
            request.manuscript_segments == {"S001": sample_segments["S001"]}
            for request in provider.turn_requests
        )
        assert all(
            request.manuscript_segments == {"S001": sample_segments["S001"]}
            for request in provider.final_requests
        )
        for request in provider.turn_requests:
            payload = request.model_dump(mode="json")
            other_profiles = {
                run.reader_profile_id
                for run in panel_session.reader_runs
                if run.reader_profile_id != request.reader_profile_id
            }
            assert not any(profile in str(payload) for profile in other_profiles)
            assert request.prior_ballot is not None
        event_types = [
            event.event_type for event in fake_db_session.storage[WorkflowEvent].values()
        ]
        assert event_types[-4:] == [
            "reader_panel.discussion_started",
            "reader_panel.discussion_round_completed",
            "reader_panel.discussion_completed",
            "reader_panel.final_ballots_locked",
        ]
        locked_at = panel_session.final_ballots_locked_at
        calls = (
            len(provider.turn_requests),
            len(provider.summary_requests),
            len(provider.final_requests),
        )
        event_count = len(fake_db_session.storage[WorkflowEvent])

        replay = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert replay.final_ballots_locked is True
        assert panel_session.final_ballots_locked_at == locked_at
        assert len(fake_db_session.storage[ReaderPanelMessage]) == 3
        assert len(fake_db_session.storage[ReaderPanelBallot]) == 4
        assert calls == (
            len(provider.turn_requests),
            len(provider.summary_requests),
            len(provider.final_requests),
        )
        assert len(fake_db_session.storage[WorkflowEvent]) == event_count

    async def test_cancelled_entry_has_zero_provider_or_write_side_effects(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        panel_session.status = ReaderPanelStatus.CANCELLED.value
        provider = RecordingDiscussionProvider(fake_db_session)
        before = (
            len(fake_db_session.storage[ReaderPanelMessage]),
            len(fake_db_session.storage[ReaderPanelBallot]),
            len(fake_db_session.storage[WorkflowEvent]),
        )

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.status == ReaderPanelStatus.CANCELLED.value
        assert provider.turn_requests == []
        assert provider.summary_requests == []
        assert provider.final_requests == []
        assert before == (
            len(fake_db_session.storage[ReaderPanelMessage]),
            len(fake_db_session.storage[ReaderPanelBallot]),
            len(fake_db_session.storage[WorkflowEvent]),
        )

    async def test_malformed_final_ballot_remains_missing_and_phase_unlocked(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        missing_profile = panel_session.reader_runs[0].reader_profile_id
        provider = RecordingDiscussionProvider(
            fake_db_session,
            malformed_final_profile=missing_profile,
        )

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        final_ballots = [
            ballot
            for ballot in fake_db_session.storage[ReaderPanelBallot].values()
            if ballot.phase == "final"
        ]
        assert len(provider.final_requests) == 3  # two attempts for one reader, one success
        assert len(final_ballots) == 1
        assert result.status == ReaderPanelStatus.FINAL_BALLOTING.value
        assert result.final_ballots_locked is False
        assert panel_session.final_ballots_locked_at is None

    async def test_retries_invalid_outputs_once_and_keeps_missing_ballot_unlocked(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        source_profile = panel_session.reader_runs[0].reader_profile_id
        issue = ExtractedIssueItem(
            issue_number=1,
            title="Unclear opening",
            category="clarity",
            symptom="The opening link is unclear.",
            root_cause_hypotheses=["Missing transition"],
            evidence=[{"segment_ids": ["S001"], "note": "Opening."}],
            source_reader_ids=[source_profile],
        )

        class RetryProvider(RecordingBallotProvider):
            def __init__(self) -> None:
                super().__init__([issue])
                self.extract_attempts = 0
                self.ballot_attempts: dict[str, int] = defaultdict(int)

            def extract_issues(self, request: Any) -> Any:
                self.extract_attempts += 1
                if self.extract_attempts == 1:
                    return {"issues": [issue.model_dump()]}
                return super().extract_issues(request)

            def generate_blind_ballot(self, request: Any) -> Any:
                profile = request.reader_profile_id
                self.ballot_attempts[profile] += 1
                if profile == panel_session.reader_runs[0].reader_profile_id:
                    return ReaderBallotOutput(
                        issue_number=request.issue.issue_number,
                        severity="minor",
                        suggested_action="keep",
                        confidence="medium",
                        evidence=[],
                        reason="Invalid evidence.",
                    )
                if self.ballot_attempts[profile] == 1:
                    return object()
                return super().generate_blind_ballot(request)

        provider = RetryProvider()
        result = await service.collect_initial_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert provider.extract_attempts == 2
        assert provider.ballot_attempts[panel_session.reader_runs[0].reader_profile_id] == 2
        assert provider.ballot_attempts[panel_session.reader_runs[1].reader_profile_id] == 2
        assert result.initial_ballot_count == 1
        assert result.initial_ballots_locked is False
        assert result.status == ReaderPanelStatus.INITIAL_BALLOTING.value
        assert len(fake_db_session.storage[ReaderPanelBallot]) == 1
        assert panel_session.initial_ballots_locked_at is None
        assert panel_session.degradation_reason == "One or more initial ballots were invalid."

    async def test_rejects_moderator_limit_and_foreign_evidence_without_leaking_errors(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        panel_session.config_snapshot["max_ballot_issues"] = 1
        source_profile = panel_session.reader_runs[0].reader_profile_id

        class InvalidModeratorProvider:
            def __init__(self) -> None:
                self.calls = 0

            def extract_issues(self, request: Any) -> ModeratorIssueExtractionOutput:
                self.calls += 1
                segment_id = "S999" if self.calls == 1 else "S001"
                return ModeratorIssueExtractionOutput(
                    issues=[
                        ExtractedIssueItem(
                            issue_number=number,
                            title=f"Issue {number}",
                            category="clarity",
                            symptom=f"Symptom {number}",
                            root_cause_hypotheses=["Cause"],
                            evidence=[{"segment_ids": [segment_id], "note": "/tmp/secret-token"}],
                            source_reader_ids=[source_profile],
                        )
                        for number in (1, 2)
                    ]
                )

        provider = InvalidModeratorProvider()
        with pytest.raises(ReaderPanelInvalidStateError):
            await service.collect_initial_ballots(session_id=panel_session.id, provider=provider)

        assert provider.calls == 2
        assert panel_session.status == ReaderPanelStatus.FAILED.value
        assert panel_session.failure_reason == (
            "Issue extraction produced invalid structured output."
        )
        assert "/tmp" not in panel_session.failure_reason
        assert "token" not in panel_session.failure_reason.lower()
        assert fake_db_session.storage[ReaderPanelIssue] == {}
        assert fake_db_session.storage[ReaderPanelBallot] == {}

    async def test_rejects_empty_evidence_and_explicit_reader_provenance(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        run = panel_session.reader_runs[0]

        class ProvenanceProvider(RecordingBallotProvider):
            def extract_issues(self, request: Any) -> ModeratorIssueExtractionOutput:
                self.moderator_calls += 1
                return ModeratorIssueExtractionOutput(
                    issues=[
                        ExtractedIssueItem(
                            issue_number=1,
                            title=(
                                "No evidence"
                                if self.moderator_calls == 1
                                else f"Reported by {run.id}"
                            ),
                            category="clarity",
                            symptom="The opening is unclear.",
                            root_cause_hypotheses=["Missing transition"],
                            evidence=(
                                []
                                if self.moderator_calls == 1
                                else [{"segment_ids": ["S001"], "note": "Opening."}]
                            ),
                            source_reader_ids=[run.reader_profile_id],
                        )
                    ]
                )

        provider = ProvenanceProvider([])
        with pytest.raises(ReaderPanelInvalidStateError):
            await service.collect_initial_ballots(session_id=panel_session.id, provider=provider)

        assert provider.moderator_calls == 2
        assert fake_db_session.storage[ReaderPanelIssue] == {}
        assert [e.event_type for e in fake_db_session.storage[WorkflowEvent].values()] == [
            "reader_panel.issue_extraction_started",
            "reader_panel.failed",
        ]
        failure = list(fake_db_session.storage[WorkflowEvent].values())[-1]
        assert failure.payload == {
            "session_id": str(panel_session.id),
            "status": ReaderPanelStatus.FAILED.value,
            "reason_code": "invalid_issue_extraction",
        }

    async def test_replay_does_not_duplicate_issues_or_ballots(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        provider = RecordingBallotProvider(
            [
                ExtractedIssueItem(
                    issue_number=1,
                    title="Unclear opening",
                    category="clarity",
                    symptom="The opening link is unclear.",
                    root_cause_hypotheses=["Missing transition"],
                    evidence=[{"segment_ids": ["S001"], "note": "Opening."}],
                    source_reader_ids=[panel_session.reader_runs[0].reader_profile_id],
                )
            ]
        )
        first = await service.collect_initial_ballots(
            session_id=panel_session.id, provider=provider
        )
        calls_after_first = (provider.moderator_calls, len(provider.ballot_requests))
        locked_at = panel_session.initial_ballots_locked_at
        event_count = len(fake_db_session.storage[WorkflowEvent])
        second = await service.collect_initial_ballots(
            session_id=panel_session.id, provider=provider
        )

        assert first.initial_ballots_locked is True
        assert second.initial_ballots_locked is True
        assert (provider.moderator_calls, len(provider.ballot_requests)) == calls_after_first
        assert panel_session.initial_ballots_locked_at == locked_at
        assert len(fake_db_session.storage[WorkflowEvent]) == event_count
        assert len(fake_db_session.storage[ReaderPanelIssue]) == 1
        assert len(fake_db_session.storage[ReaderPanelBallot]) == 2

    async def test_preserves_exact_case_colliding_profile_sources(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        upper, lower = panel_session.reader_runs
        upper.reader_profile_id = "Critic"
        lower.reader_profile_id = "critic"
        provider = RecordingBallotProvider(
            [
                ExtractedIssueItem(
                    issue_number=1,
                    title="Upper perspective",
                    category="clarity",
                    symptom="The opening is unclear.",
                    root_cause_hypotheses=["Missing transition"],
                    evidence=[{"segment_ids": ["S001"], "note": "Opening."}],
                    source_reader_ids=["Critic"],
                ),
                ExtractedIssueItem(
                    issue_number=2,
                    title="Lower perspective",
                    category="pacing",
                    symptom="The opening moves slowly.",
                    root_cause_hypotheses=["Long setup"],
                    evidence=[{"segment_ids": ["S002"], "note": "Setup."}],
                    source_reader_ids=["critic"],
                ),
            ]
        )

        result = await service.collect_initial_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.issue_count == 2
        sources_by_title = {
            issue.title: issue.source_reader_ids
            for issue in fake_db_session.storage[ReaderPanelIssue].values()
        }
        assert sources_by_title == {
            "Upper perspective": [str(upper.initial_report.id)],
            "Lower perspective": [str(lower.initial_report.id)],
        }

    async def test_profile_provenance_requires_identifier_boundary(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_locked_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        panel_session.reader_runs[0].reader_profile_id = "Critic"
        provider = RecordingBallotProvider(
            [
                ExtractedIssueItem(
                    issue_number=1,
                    title="Critical pacing",
                    category="pacing",
                    symptom="The opening is slow.",
                    root_cause_hypotheses=["Long setup"],
                    evidence=[{"segment_ids": ["S001"], "note": "Critical beat."}],
                    source_reader_ids=["Critic"],
                )
            ]
        )

        result = await service.collect_initial_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.initial_ballots_locked is True


@pytest.mark.anyio
class TestReaderPanelDiscussionContextValidation:
    async def test_round_two_only_receives_previous_summary_and_current_round_turns(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        class TwoRoundProvider(RecordingDiscussionProvider):
            def summarize_discussion(self, request: Any) -> ModeratorDiscussionSummaryOutput:
                self.summary_requests.append(request)
                return ModeratorDiscussionSummaryOutput(
                    round_summary=f"Bounded summary round {request.round_number}.",
                    remaining_disagreements=["One disagreement remains."],
                    suggested_focus="Review the causal beat.",
                    is_consensus_reached=request.round_number == 2,
                )

        provider = TwoRoundProvider(fake_db_session)
        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.final_ballots_locked is True
        round_two = [request for request in provider.turn_requests if request.round_number == 2]
        assert len(round_two) == 2
        assert [message["speaker_type"] for message in round_two[0].prior_messages] == ["moderator"]
        assert [message["speaker_type"] for message in round_two[1].prior_messages] == [
            "moderator",
            "reader",
        ]
        assert all(
            message["round_number"] in {1, 2}
            for request in round_two
            for message in request.prior_messages
        )
        assert all(
            "reader_run_id" not in message and "reader_profile_id" not in message
            for request in round_two
            for message in request.prior_messages
        )

    async def test_explicit_unknown_references_and_empty_evidence_are_repaired(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        foreign_uuid = str(uuid4())

        class InvalidReferenceProvider(RecordingDiscussionProvider):
            def __init__(self, db: FakeAsyncSession) -> None:
                super().__init__(db)
                self.turn_attempts = 0
                self.summary_attempts = 0

            def generate_discussion_turn(self, request: Any) -> ReaderDiscussionTurnOutput:
                self.turn_requests.append(request)
                self.turn_attempts += 1
                if self.turn_attempts == 1:
                    return ReaderDiscussionTurnOutput(
                        stance="support",
                        claim="See [S999] and ISSUE-999.",
                        evidence=[],
                        novelty="new_evidence",
                    )
                return super().generate_discussion_turn(request)

            def summarize_discussion(self, request: Any) -> ModeratorDiscussionSummaryOutput:
                self.summary_requests.append(request)
                self.summary_attempts += 1
                if self.summary_attempts == 1:
                    return ModeratorDiscussionSummaryOutput(
                        round_summary=f"Unknown ISSUE-999 reader {foreign_uuid}.",
                        remaining_disagreements=["See S999."],
                        suggested_focus="ROUND-99",
                        is_consensus_reached=True,
                    )
                return super().summarize_discussion(request)

        provider = InvalidReferenceProvider(fake_db_session)
        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.final_ballots_locked is True
        assert provider.turn_attempts >= 2
        assert provider.summary_attempts == 2
        persisted_text = " ".join(
            message.claim for message in fake_db_session.storage[ReaderPanelMessage].values()
        )
        assert "S999" not in persisted_text
        assert "ISSUE-999" not in persisted_text
        assert foreign_uuid not in persisted_text


@pytest.mark.anyio
class TestReaderPanelDiscussionRemediation:
    async def test_invalid_initial_snapshot_fails_before_any_mutation(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        initial = next(
            ballot
            for ballot in fake_db_session.storage[ReaderPanelBallot].values()
            if ballot.phase == "initial"
        )
        initial.evidence = []
        provider = RecordingDiscussionProvider(fake_db_session)
        before = (
            panel_session.status,
            len(fake_db_session.storage[WorkflowEvent]),
            len(fake_db_session.storage[ReaderPanelMessage]),
            len(fake_db_session.storage[ReaderPanelBallot]),
        )

        with pytest.raises(ReaderPanelInvalidStateError):
            await service.run_discussion_and_final_ballots(
                session_id=panel_session.id,
                provider=provider,
            )

        assert provider.turn_requests == []
        assert provider.summary_requests == []
        assert provider.final_requests == []
        assert before == (
            panel_session.status,
            len(fake_db_session.storage[WorkflowEvent]),
            len(fake_db_session.storage[ReaderPanelMessage]),
            len(fake_db_session.storage[ReaderPanelBallot]),
        )

    async def test_version_bound_initial_evidence_outside_issue_remains_valid(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        initial = next(
            ballot
            for ballot in fake_db_session.storage[ReaderPanelBallot].values()
            if ballot.phase == "initial"
        )
        initial.evidence = [{"segment_ids": ["S002"], "note": "Version-bound support."}]
        profile = next(
            run.reader_profile_id
            for run in panel_session.reader_runs
            if run.id == initial.reader_run_id
        )
        provider = RecordingDiscussionProvider(fake_db_session)

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.final_ballots_locked is True
        request = next(item for item in provider.turn_requests if item.reader_profile_id == profile)
        assert request.prior_ballot["evidence"] == []

    async def test_zero_round_limit_skips_discussion_but_collects_all_finals(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        panel_session.config_snapshot["max_rounds_per_issue"] = 0
        provider = RecordingDiscussionProvider(fake_db_session)

        result = await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert result.final_ballots_locked is True
        assert provider.turn_requests == []
        assert provider.summary_requests == []
        assert len(provider.final_requests) == 2
        assert len(fake_db_session.storage[ReaderPanelMessage]) == 0
        assert {issue.discussion_status for issue in panel_session.issues} == {"skipped"}

    async def test_cjk_request_exhausts_conservative_input_budget_before_provider(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        version = await fake_db_session.get(DocumentVersion, panel_session.document_version_id)
        assert version is not None
        version.metadata_["segments"]["S001"] = "界" * 600
        panel_session.config_snapshot["max_input_tokens_per_call"] = 1_000
        provider = RecordingDiscussionProvider(fake_db_session)

        await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert provider.turn_requests == []
        assert provider.summary_requests == []
        assert provider.final_requests == []

    async def test_bracketed_custom_segment_reference_is_repaired(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        version = await fake_db_session.get(DocumentVersion, panel_session.document_version_id)
        assert version is not None
        version.metadata_["segments"] = {"intro_01": "A bound introduction."}
        issue = panel_session.issues[0]
        issue.evidence = [{"segment_ids": ["intro_01"], "note": "Bound."}]
        for ballot in panel_session.ballots:
            ballot.evidence = [{"segment_ids": ["intro_01"], "note": "Bound."}]

        class CustomSegmentProvider(RecordingDiscussionProvider):
            def __init__(self, db: FakeAsyncSession) -> None:
                super().__init__(db)
                self.attempts = 0

            def generate_discussion_turn(self, request: Any) -> ReaderDiscussionTurnOutput:
                self.turn_requests.append(request)
                self.attempts += 1
                if self.attempts == 1:
                    return ReaderDiscussionTurnOutput(
                        stance="support",
                        claim="Compare [other_99].",
                        evidence=[{"segment_ids": ["intro_01"], "note": "Bound."}],
                    )
                return ReaderDiscussionTurnOutput(
                    stance="support",
                    claim="The bound introduction needs a causal beat.",
                    evidence=[{"segment_ids": ["intro_01"], "note": "Bound."}],
                )

        provider = CustomSegmentProvider(fake_db_session)
        await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert provider.attempts >= 2
        assert all(
            "other_99" not in message.claim
            for message in fake_db_session.storage[ReaderPanelMessage].values()
        )

    @pytest.mark.parametrize("identity_kind", ["profile", "embedded_uuid"])
    async def test_reader_output_cannot_disclose_own_identity(
        self,
        identity_kind: str,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        class OwnIdentityProvider(RecordingDiscussionProvider):
            def __init__(self, db: FakeAsyncSession) -> None:
                super().__init__(db)
                self.rejected_text: str | None = None

            def generate_discussion_turn(self, request: Any) -> ReaderDiscussionTurnOutput:
                self.turn_requests.append(request)
                matching_run = next(
                    run
                    for run in panel_session.reader_runs
                    if run.reader_profile_id == request.reader_profile_id
                )
                if self.rejected_text is None:
                    self.rejected_text = (
                        request.reader_profile_id
                        if identity_kind == "profile"
                        else f"reader_{matching_run.id}"
                    )
                    return ReaderDiscussionTurnOutput(
                        stance="support",
                        claim=f"Identity {self.rejected_text} supports this.",
                        evidence=[{"segment_ids": ["S001"], "note": "Bound."}],
                    )
                return super().generate_discussion_turn(request)

        provider = OwnIdentityProvider(fake_db_session)
        await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert len(provider.turn_requests) >= 2
        assert provider.rejected_text is not None
        persisted = " ".join(
            message.claim for message in fake_db_session.storage[ReaderPanelMessage].values()
        )
        assert provider.rejected_text not in persisted
        assert all(
            provider.rejected_text not in str(request.prior_messages)
            for request in provider.turn_requests[1:]
        )

    async def test_concurrent_close_discards_stale_moderator_summary(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        class ConcurrentCloseProvider(RecordingDiscussionProvider):
            def summarize_discussion(self, request: Any) -> ModeratorDiscussionSummaryOutput:
                self.summary_requests.append(request)
                panel_session.issues[0].discussion_status = "closed"
                return super().summarize_discussion(request)

        provider = ConcurrentCloseProvider(fake_db_session)
        await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert not any(
            message.speaker_type == "moderator"
            for message in fake_db_session.storage[ReaderPanelMessage].values()
        )
        assert not any(
            event.event_type == "reader_panel.discussion_round_completed"
            for event in fake_db_session.storage[WorkflowEvent].values()
        )

    async def test_stale_reader_run_discards_turn_and_final_outputs(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        stale_run = panel_session.reader_runs[0]

        class StaleRunProvider(RecordingDiscussionProvider):
            def generate_discussion_turn(self, request: Any) -> ReaderDiscussionTurnOutput:
                output = super().generate_discussion_turn(request)
                if request.reader_profile_id == stale_run.reader_profile_id:
                    stale_run.status = "failed"
                return output

        provider = StaleRunProvider(fake_db_session)
        await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=provider,
        )

        assert not any(
            message.reader_run_id == stale_run.id
            for message in fake_db_session.storage[ReaderPanelMessage].values()
        )
        assert not any(
            ballot.reader_run_id == stale_run.id and ballot.phase == "final"
            for ballot in fake_db_session.storage[ReaderPanelBallot].values()
        )


async def setup_final_ballots_locked(
    db: FakeAsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content_hash: str,
    segments: dict[str, str],
) -> tuple[ReaderPanelService, ReaderPanelSession]:
    service, panel_session = await setup_initial_ballots_locked(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document_id,
        version_id=version_id,
        content_hash=content_hash,
        segments=segments,
    )
    await service.run_discussion_and_final_ballots(
        session_id=panel_session.id,
        provider=RecordingDiscussionProvider(db),
    )
    return service, panel_session


async def setup_two_issue_final_panel(
    db: FakeAsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content_hash: str,
    segments: dict[str, str],
    shared_binding: bool,
) -> tuple[ReaderPanelService, ReaderPanelSession]:
    service, panel_session = await setup_locked_panel(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document_id,
        version_id=version_id,
        content_hash=content_hash,
        segments=segments,
    )
    source_profile = panel_session.reader_runs[0].reader_profile_id
    extracted = [
        ExtractedIssueItem(
            issue_number=number,
            title="Shared finding" if shared_binding else f"Finding {number}",
            category=f"category_{number}",
            symptom=f"Symptom {number}",
            root_cause_hypotheses=[f"Cause {number}"],
            evidence=[
                {
                    "segment_ids": ["S001" if shared_binding else f"S00{number}"],
                    "note": f"Evidence {number}",
                }
            ],
            source_reader_ids=[source_profile],
        )
        for number in (1, 2)
    ]
    await service.collect_initial_ballots(
        session_id=panel_session.id,
        provider=RecordingBallotProvider(extracted),
    )
    panel_session.issues = list(db.storage[ReaderPanelIssue].values())
    panel_session.ballots = list(db.storage[ReaderPanelBallot].values())
    panel_session.messages = []
    panel_session.config_snapshot.update(
        {
            "max_discussion_issues": 2,
            "max_rounds_per_issue": 1,
            "max_total_model_calls": 30,
            "max_input_tokens_per_call": 10_000,
            "max_execution_seconds": 30,
        }
    )
    await service.run_discussion_and_final_ballots(
        session_id=panel_session.id,
        provider=RecordingDiscussionProvider(db),
    )
    return service, panel_session


@pytest.mark.anyio
class TestReaderPanelEditorHandoffReport:
    async def test_generates_version_bound_non_approval_report_idempotently(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_initial_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        await service.run_discussion_and_final_ballots(
            session_id=panel_session.id,
            provider=RecordingDiscussionProvider(fake_db_session),
        )

        provider = RecordingSynthesisProvider(fake_db_session)
        fake_db_session.get_options.clear()
        fake_db_session.execute_options.clear()
        first = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=provider,
        )
        completed_at = panel_session.completed_at
        second = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=provider,
        )

        assert first.review_report_id is not None
        assert second.review_report_id == first.review_report_id
        assert panel_session.review_report_id == first.review_report_id
        assert completed_at is not None
        assert panel_session.completed_at == completed_at
        assert panel_session.status == ReaderPanelStatus.COMPLETED.value
        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert list(request.initial_reports) == ["reader_1", "reader_2"]
        report_ids = {
            str(report.id) for report in fake_db_session.storage[ReaderInitialReport].values()
        }
        assert report_ids.isdisjoint(request.initial_reports)
        for table in (
            "reader_runs",
            "reader_initial_reports",
            "reader_panel_issues",
            "reader_panel_ballots",
        ):
            matching = [
                options
                for statement, options in fake_db_session.execute_options
                if table in statement
            ]
            assert matching and all(options.get("populate_existing") for options in matching)
        for model in (Document, DocumentVersion, WorkflowRun, ReviewReport):
            matching = [
                options
                for called_model, options in fake_db_session.get_options
                if called_model is model
            ]
            assert matching and all(options.get("populate_existing") for options in matching)
        workflow_run = fake_db_session.storage[WorkflowRun][panel_session.workflow_run_id]
        assert workflow_run.status == ReaderPanelStatus.COMPLETED.value
        assert workflow_run.current_node == "completed"
        assert workflow_run.next_node is None
        assert workflow_run.awaiting_user is False
        assert workflow_run.completed_at == completed_at
        reports = list(fake_db_session.storage[ReviewReport].values())
        assert len(reports) == 1
        report = reports[0]
        assert report.passed is False
        assert report.project_id == test_project_id
        assert report.chapter_id == test_chapter_id
        assert report.workflow_run_id == panel_session.workflow_run_id
        assert report.target_document_id == test_document_id
        assert report.target_version_id == test_version_id
        assert report.report_document_id is None
        assert report.raw_report["source_hash"] == sample_hash
        assert report.raw_report["automatic_application_allowed"] is False
        assert report.raw_report["provider_usage"] == {
            "report_synthesis_calls": 1,
            "total_panel_calls": None,
            "input_tokens": None,
            "output_tokens": None,
        }
        assert [item["issue_number"] for item in report.raw_report["issues"]] == [1]
        serialized_report = str(report.raw_report)
        assert all(
            str(run.id) not in serialized_report and run.reader_profile_id not in serialized_report
            for run in panel_session.reader_runs
        )
        assert len(fake_db_session.storage[Document]) == 1
        assert len(fake_db_session.storage[DocumentVersion]) == 1
        assert fake_db_session.storage[ActionRequest] == {}
        event = next(
            item
            for item in fake_db_session.storage[WorkflowEvent].values()
            if item.event_type == "reader_panel.report_completed"
        )
        assert set(event.payload) == {
            "session_id",
            "review_report_id",
            "status",
            "issue_count",
            "valid_reader_count",
            "failed_reader_count",
        }
        resumed = await service.initialize_session(
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            mode=PanelMode.QUICK,
        )
        assert resumed.review_report_id == report.id

    async def test_polarized_minority_and_target_audience_tally_are_server_owned(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        non_target = next(run for run in panel_session.reader_runs if not run.is_target_audience)
        risk_ballot = next(
            ballot
            for ballot in fake_db_session.storage[ReaderPanelBallot].values()
            if ballot.phase == "final" and ballot.reader_run_id == non_target.id
        )
        risk_ballot.severity = "critical"
        risk_ballot.suggested_action = "rewrite_local"
        risk_ballot.confidence = "high"
        risk_ballot.remaining_disagreement = "The high-risk reader still sees a fatal reveal."
        panel_session.stale = True

        result = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=RecordingSynthesisProvider(fake_db_session),
        )

        issue = next(iter(fake_db_session.storage[ReaderPanelIssue].values()))
        assert result.status == ReaderPanelStatus.COMPLETED.value
        assert issue.consensus_class == "polarized"
        assert issue.recommended_priority == "must_fix"
        assert issue.final_tally == {
            "raw_distribution": {
                "none": 0,
                "minor": 1,
                "significant": 0,
                "critical": 1,
                "abstain": 0,
            },
            "target_audience_distribution": {
                "none": 0,
                "minor": 1,
                "significant": 0,
                "critical": 0,
                "abstain": 0,
            },
            "valid_votes": 2,
            "total_votes": 2,
            "target_audience_votes": 1,
            "risk_flags": ["minority_high_risk"],
        }
        report = next(iter(fake_db_session.storage[ReviewReport].values()))
        assert report.raw_report["stale"] is True
        assert report.raw_report["automatic_application_allowed"] is False
        assert report.raw_report["minority_issue_numbers"] == [1]
        assert report.raw_report["issues"][0]["remaining_disagreements"] == [
            "The high-risk reader still sees a fatal reveal."
        ]
        assert any("stale" in warning.lower() for warning in report.warnings)

    @pytest.mark.parametrize(
        "corruption",
        [
            "mode",
            "role",
            "artifact",
            "schema",
            "source_document",
            "source_version",
            "source_hash",
            "automatic_application",
        ],
    )
    async def test_replay_rejects_corrupt_canonical_report_without_provider_call(
        self,
        corruption: str,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=RecordingSynthesisProvider(fake_db_session),
        )
        report = next(iter(fake_db_session.storage[ReviewReport].values()))
        if corruption == "mode":
            report.review_mode = "single_agent"
        elif corruption == "role":
            report.reviewer_agent_role = "reader_agent"
        elif corruption == "artifact":
            report.report_document_id = uuid4()
        else:
            key = {
                "schema": "schema_version",
                "source_document": "source_document_id",
                "source_version": "source_version_id",
                "source_hash": "source_hash",
                "automatic_application": "automatic_application_allowed",
            }[corruption]
            raw_report = dict(report.raw_report)
            raw_report[key] = True if corruption == "automatic_application" else "tampered"
            report.raw_report = raw_report
        provider = RecordingSynthesisProvider(fake_db_session)

        with pytest.raises(ReaderPanelInvalidStateError):
            await service.generate_editor_handoff_report(
                session_id=panel_session.id,
                provider=provider,
            )

        assert provider.requests == []

    async def test_failed_sample_is_explicitly_degraded_not_full_panel(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        fake_db_session.add(
            ReaderRun(
                id=uuid4(),
                session_id=panel_session.id,
                reader_profile_id="failed_reader",
                status="failed",
                is_target_audience=False,
                retry_count=2,
            )
        )
        await fake_db_session.commit()

        result = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=RecordingSynthesisProvider(fake_db_session),
        )

        assert result.status == ReaderPanelStatus.DEGRADED_COMPLETED.value
        report = next(iter(fake_db_session.storage[ReviewReport].values()))
        assert report.raw_report["sample"] == {
            "requested": 3,
            "valid": 2,
            "failed": 1,
            "complete": False,
            "degradation_reason": "1 reader(s) unavailable.",
        }
        assert any("not a full panel" in warning for warning in report.warnings)
        workflow_run = fake_db_session.storage[WorkflowRun][panel_session.workflow_run_id]
        assert workflow_run.status == ReaderPanelStatus.DEGRADED_COMPLETED.value
        assert workflow_run.completed_at == panel_session.completed_at

    async def test_all_abstentions_are_inconclusive_with_zero_valid_votes(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        for ballot in fake_db_session.storage[ReaderPanelBallot].values():
            if ballot.phase == "final":
                ballot.severity = "abstain"
                ballot.suggested_action = "manual_review"

        await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=RecordingSynthesisProvider(fake_db_session),
        )

        issue = next(iter(fake_db_session.storage[ReaderPanelIssue].values()))
        assert issue.consensus_class == "inconclusive"
        assert issue.recommended_priority == "manual_review"
        assert issue.final_tally["valid_votes"] == 0
        assert issue.final_tally["total_votes"] == 2

    async def test_shared_recommendation_bindings_use_canonical_issue_order(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_two_issue_final_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
            shared_binding=True,
        )

        class NaturalWordingProvider(RecordingSynthesisProvider):
            def synthesize_report(self, request: Any) -> Any:
                output = super().synthesize_report(request)
                recommendations = [
                    recommendation.model_copy(
                        update={"instruction": "Keep the hook, clarify the shared finding."}
                    )
                    for recommendation in output.actionable_recommendations
                ]
                return output.model_copy(update={"actionable_recommendations": recommendations})

        result = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=NaturalWordingProvider(fake_db_session),
        )

        assert result.status == ReaderPanelStatus.COMPLETED.value
        report = next(iter(fake_db_session.storage[ReviewReport].values()))
        assert len(report.suggested_actions) == 2
        assert [action["target_segment_ids"] for action in report.suggested_actions] == [
            ["S001"],
            ["S001"],
        ]
        assert [action["suggested_action"] for action in report.suggested_actions] == [
            "clarify",
            "clarify",
        ]

    async def test_recommendation_instruction_cannot_reference_another_issue(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_two_issue_final_panel(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
            shared_binding=False,
        )

        class CrossIssueProvider(RecordingSynthesisProvider):
            def synthesize_report(self, request: Any) -> Any:
                output = super().synthesize_report(request)
                recommendations = list(output.actionable_recommendations)
                recommendations[0] = recommendations[0].model_copy(
                    update={"instruction": "Address Finding 1 after ISSUE-2."}
                )
                return output.model_copy(update={"actionable_recommendations": recommendations})

        provider = CrossIssueProvider(fake_db_session)
        with pytest.raises(ReaderPanelInvalidStateError):
            await service.generate_editor_handoff_report(
                session_id=panel_session.id,
                provider=provider,
            )

        assert len(provider.requests) == 1
        assert fake_db_session.storage[ReviewReport] == {}

    async def test_custom_bracketed_segment_reference_is_allowed_when_bound(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        version = fake_db_session.storage[DocumentVersion][test_version_id]
        version.metadata_ = {"segments": {"intro_01": "A bound introduction."}}
        for issue in fake_db_session.storage[ReaderPanelIssue].values():
            issue.evidence = [{"segment_ids": ["intro_01"], "note": "Bound."}]
        for ballot in fake_db_session.storage[ReaderPanelBallot].values():
            ballot.evidence = [{"segment_ids": ["intro_01"], "note": "Bound."}]

        class CustomSegmentProvider(RecordingSynthesisProvider):
            def synthesize_report(self, request: Any) -> Any:
                output = super().synthesize_report(request)
                finding = output.key_findings[0].model_copy(update={"summary": "See [intro_01]."})
                return output.model_copy(update={"key_findings": [finding]})

        result = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=CustomSegmentProvider(fake_db_session),
        )

        assert result.status == ReaderPanelStatus.COMPLETED.value

    @pytest.mark.parametrize(
        "corruption",
        ["missing_final", "duplicate_final", "foreign_issue", "unbound_evidence"],
    )
    async def test_invalid_persisted_sample_fails_closed_before_synthesis(
        self,
        corruption: str,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        final = next(
            ballot
            for ballot in fake_db_session.storage[ReaderPanelBallot].values()
            if ballot.phase == "final"
        )
        if corruption == "missing_final":
            del fake_db_session.storage[ReaderPanelBallot][final.id]
        elif corruption == "duplicate_final":
            fake_db_session.add(
                ReaderPanelBallot(
                    id=uuid4(),
                    session_id=final.session_id,
                    reader_run_id=final.reader_run_id,
                    issue_id=final.issue_id,
                    phase=final.phase,
                    severity=final.severity,
                    suggested_action=final.suggested_action,
                    confidence=final.confidence,
                    evidence=final.evidence,
                    position_changed=final.position_changed,
                    change_reason=final.change_reason,
                    remaining_disagreement=final.remaining_disagreement,
                )
            )
        elif corruption == "foreign_issue":
            final.issue_id = uuid4()
        else:
            final.evidence = [{"segment_ids": ["S999"], "note": "Unbound."}]
        await fake_db_session.commit()
        provider = RecordingSynthesisProvider(fake_db_session)

        with pytest.raises(ReaderPanelInvalidStateError):
            await service.generate_editor_handoff_report(
                session_id=panel_session.id,
                provider=provider,
            )

        assert provider.requests == []
        assert fake_db_session.storage[ReviewReport] == {}
        assert panel_session.review_report_id is None

    @pytest.mark.parametrize(
        "tampering",
        [
            "omission",
            "addition",
            "classification",
            "priority",
            "duplicate_recommendation",
            "reference",
            "summary_reference",
            "bracket_reference",
            "secret",
        ],
    )
    async def test_rejects_moderator_tampering_without_persisting_output(
        self,
        tampering: str,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        class TamperingProvider(RecordingSynthesisProvider):
            def synthesize_report(self, request: Any) -> Any:
                output = super().synthesize_report(request)
                finding = output.key_findings[0]
                if tampering == "omission":
                    return output.model_copy(update={"key_findings": []})
                if tampering == "addition":
                    injected = finding.model_copy(
                        update={"issue_number": 99, "title": "Injected issue"}
                    )
                    return output.model_copy(update={"key_findings": [finding, injected]})
                if tampering == "classification":
                    changed = finding.model_copy(
                        update={
                            "consensus_class": type(finding.consensus_class)("strong_consensus")
                        }
                    )
                    return output.model_copy(update={"key_findings": [changed]})
                if tampering == "priority":
                    recommendation = output.actionable_recommendations[0]
                    changed = recommendation.model_copy(
                        update={"priority": type(recommendation.priority)("must_fix")}
                    )
                    return output.model_copy(update={"actionable_recommendations": [changed]})
                if tampering == "duplicate_recommendation":
                    recommendation = output.actionable_recommendations[0]
                    return output.model_copy(
                        update={
                            "actionable_recommendations": [
                                recommendation,
                                recommendation,
                            ]
                        }
                    )
                if tampering == "reference":
                    changed = finding.model_copy(
                        update={
                            "evidence": [EvidenceRef(segment_ids=["S999"], note="Foreign segment.")]
                        }
                    )
                    return output.model_copy(update={"key_findings": [changed]})
                if tampering == "summary_reference":
                    changed = finding.model_copy(update={"summary": "See ISSUE-99 at S999."})
                    return output.model_copy(update={"key_findings": [changed]})
                if tampering == "bracket_reference":
                    changed = finding.model_copy(update={"summary": "See [foreign_01]."})
                    return output.model_copy(update={"key_findings": [changed]})
                return output.model_copy(
                    update={"executive_summary": "Bearer token=super-secret-value"}
                )

        provider = TamperingProvider(fake_db_session)
        with pytest.raises(ReaderPanelInvalidStateError):
            await service.generate_editor_handoff_report(
                session_id=panel_session.id,
                provider=provider,
            )

        assert len(provider.requests) == 1
        assert fake_db_session.storage[ReviewReport] == {}
        assert panel_session.review_report_id is None
        assert panel_session.status == ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value

    async def test_discards_synthesis_when_locked_ballot_changes_during_provider_call(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )

        class MutatingProvider(RecordingSynthesisProvider):
            def synthesize_report(self, request: Any) -> Any:
                output = super().synthesize_report(request)
                final = next(
                    ballot
                    for ballot in self.db.storage[ReaderPanelBallot].values()
                    if ballot.phase == "final"
                )
                final.severity = "significant"
                return output

        provider = MutatingProvider(fake_db_session)
        with pytest.raises(ReaderPanelInvalidStateError):
            await service.generate_editor_handoff_report(
                session_id=panel_session.id,
                provider=provider,
            )

        assert len(provider.requests) == 1
        assert fake_db_session.storage[ReviewReport] == {}
        assert panel_session.status == ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value

    async def test_version_advance_during_synthesis_completes_stale_on_bound_version(
        self,
        fake_db_session: FakeAsyncSession,
        test_project_id: UUID,
        test_chapter_id: UUID,
        test_document_id: UUID,
        test_version_id: UUID,
        sample_hash: str,
        sample_segments: dict[str, str],
    ) -> None:
        service, panel_session = await setup_final_ballots_locked(
            fake_db_session,
            project_id=test_project_id,
            chapter_id=test_chapter_id,
            document_id=test_document_id,
            version_id=test_version_id,
            content_hash=sample_hash,
            segments=sample_segments,
        )
        next_version_id = uuid4()
        fake_db_session.add(
            DocumentVersion(
                id=next_version_id,
                document_id=test_document_id,
                version_number=2,
                source="writer_agent",
                content_hash="new-version-hash",
                byte_size=16,
                word_count=3,
                file_path="chapters/001_v2.md",
                metadata_={"segments": {"S001": "A newer version."}},
            )
        )
        await fake_db_session.commit()

        class AdvancingVersionProvider(RecordingSynthesisProvider):
            def synthesize_report(self, request: Any) -> Any:
                output = super().synthesize_report(request)
                document = self.db.storage[Document][test_document_id]
                document.current_version_id = next_version_id
                return output

        result = await service.generate_editor_handoff_report(
            session_id=panel_session.id,
            provider=AdvancingVersionProvider(fake_db_session),
        )

        assert result.status == ReaderPanelStatus.COMPLETED.value
        assert result.stale is True
        report = next(iter(fake_db_session.storage[ReviewReport].values()))
        assert report.target_version_id == test_version_id
        assert report.raw_report["source_version_id"] == str(test_version_id)
        assert report.raw_report["stale"] is True
        assert report.raw_report["automatic_application_allowed"] is False
