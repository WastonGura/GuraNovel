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
from app.models.core import Chapter, Document, DocumentVersion, Project, WorkflowRun
from app.models.reader_panel import ReaderInitialReport, ReaderPanelSession, ReaderRun
from app.services.reader_panel_service import (
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

        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.MALFORMED_OUTPUT)
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
