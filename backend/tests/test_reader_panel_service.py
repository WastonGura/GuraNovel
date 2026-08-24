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
    ExtractedIssueItem,
    ModeratorIssueExtractionOutput,
    ReaderBallotOutput,
)
from app.models.core import Chapter, Document, DocumentVersion, Project, WorkflowRun
from app.models.reader_panel import (
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
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

    async def get(self, model_cls: type, pk: Any) -> Any | None:
        return self.storage[model_cls].get(pk)

    def add(self, obj: Any) -> None:
        if not hasattr(obj, "id") or obj.id is None:
            obj.id = uuid4()
        self.storage[type(obj)][obj.id] = obj

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def execute(self, stmt: Any) -> FakeExecuteResult:
        # Simple evaluation based on statement target table
        stmt_str = str(stmt)
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
        elif "documents" in stmt_str:
            docs = list(self.storage[Document].values())
            return FakeExecuteResult(docs)
        elif "workflow_runs" in stmt_str:
            wfs = list(self.storage[WorkflowRun].values())
            return FakeExecuteResult(wfs)
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
    def __init__(self, issues: list[ExtractedIssueItem]) -> None:
        self.issues = issues
        self.moderator_calls = 0
        self.ballot_requests: list[Any] = []

    def extract_issues(self, request: Any) -> ModeratorIssueExtractionOutput:
        self.moderator_calls += 1
        return ModeratorIssueExtractionOutput(issues=self.issues)

    def generate_blind_ballot(self, request: Any) -> ReaderBallotOutput:
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
        source_profile = panel_session.reader_runs[0].reader_profile_id
        issues = [
            ExtractedIssueItem(
                issue_number=4,
                title="  Unclear opening  ",
                category="clarity",
                symptom="The opening link is unclear.",
                root_cause_hypotheses=["Missing transition"],
                evidence=[{"segment_ids": ["S001"], "note": "Opening."}],
                source_reader_ids=[source_profile],
                target_audience_relevance="high",
            ),
            ExtractedIssueItem(
                issue_number=9,
                title="unclear opening",
                category="CLARITY",
                symptom="The opening link is unclear.",
                root_cause_hypotheses=["Missing transition"],
                evidence=[{"segment_ids": ["S001"], "note": "Same location."}],
                source_reader_ids=[source_profile],
                target_audience_relevance="high",
            ),
        ]
        provider = RecordingBallotProvider(issues)

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
        assert persisted_issues[0].source_reader_ids == [str(panel_session.reader_runs[0].id)]
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
                        evidence=[{"segment_ids": ["S999"], "note": "Foreign segment."}],
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
        second = await service.collect_initial_ballots(
            session_id=panel_session.id, provider=provider
        )

        assert first.initial_ballots_locked is True
        assert second.initial_ballots_locked is True
        assert (provider.moderator_calls, len(provider.ballot_requests)) == calls_after_first
        assert len(fake_db_session.storage[ReaderPanelIssue]) == 1
        assert len(fake_db_session.storage[ReaderPanelBallot]) == 2
