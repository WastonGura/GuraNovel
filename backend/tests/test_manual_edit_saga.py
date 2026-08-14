from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.agents import DeterministicChapterWriterProvider, WriterAgent
from app.documents.chapter_segments import MAX_CHAPTER_CONTENT_BYTES
from app.models import ActionRequestStatus, DocumentSource
from app.services.author_accept_coordination import _StaleActionAdopted
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.services.document_service import DocumentCommitIndeterminateError
from app.services.manual_edit_saga import (
    ManualEditCoordinator,
    _expiry_precludes_resolution,
)
from app.workflows.chapter_production import ChapterActionDecision


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTION_ID = UUID("44444444-4444-4444-8444-444444444444")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
ACTOR_ID = UUID("77777777-7777-4777-8777-777777777777")
CHILD_VERSION_ID = UUID("88888888-8888-4888-8888-888888888888")
NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
FIXED_KEY = "a" * 64
CONTENT_HASH = "c" * 64
CHILD_HASH = "d" * 64
CONTENT = "# Arrival\n\nUser-authored exact replacement.\n"


def test_expiry_precludes_resolution_only_at_or_after_the_database_clock() -> None:
    assert _expiry_precludes_resolution(None, NOW) is False
    assert _expiry_precludes_resolution(NOW + timedelta(hours=1), NOW) is False
    assert _expiry_precludes_resolution(NOW, NOW) is True
    assert _expiry_precludes_resolution(NOW - timedelta(hours=1), NOW) is True


def _context(*, expires_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        run=SimpleNamespace(id=RUN_ID),
        state=SimpleNamespace(),
        checkpoint=SimpleNamespace(),
        action=SimpleNamespace(id=ACTION_ID, expires_at=expires_at),
        binding=SimpleNamespace(
            document_id=str(DOCUMENT_ID),
            document_version_id=str(VERSION_ID),
            content_hash=CONTENT_HASH,
        ),
        document=SimpleNamespace(id=DOCUMENT_ID),
        version=SimpleNamespace(id=VERSION_ID),
    )


def _finalize_action(
    *,
    project_id: UUID = PROJECT_ID,
    chapter_id: UUID = CHAPTER_ID,
    metadata_key: str = FIXED_KEY,
) -> SimpleNamespace:
    return SimpleNamespace(
        project_id=project_id,
        chapter_id=chapter_id,
        request_type="chapter_author_revision",
        prompt="Review the current chapter draft.",
        options=["accept", "request_revision", "submit_manual_edit"],
        default_option="accept",
        user_feedback=None,
        resolved_at=NOW,
        expires_at=None,
        metadata_={
            "contract_version": "chapter-production-v2",
            "action_kind": "author_revision",
            "document_id": str(DOCUMENT_ID),
            "document_version_id": str(VERSION_ID),
            "content_hash": CONTENT_HASH,
            "operation_key": metadata_key,
        },
    )


class FakeSession:
    """Dispatch the only two scalar statements the saga may issue."""

    def __init__(self, database_now: datetime, action: object) -> None:
        self.database_now = database_now
        self.action = action
        self.scalar_calls: list[object] = []

    async def scalar(self, statement: object) -> object:
        self.scalar_calls.append(statement)
        text = str(statement)
        if "clock_timestamp" in text:
            return self.database_now
        if "action_requests" in text:
            return self.action
        raise AssertionError(f"unexpected scalar statement: {text}")


class FakeDocuments:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.write_calls: list[dict[str, object]] = []

    async def write_document(self, **kwargs: object) -> object:
        self.write_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id=CHILD_VERSION_ID, content_hash=CHILD_HASH)


class _FakeState:
    def __init__(self) -> None:
        self.resolve_calls: list[dict[str, object]] = []

    def resolve_action(self, **kwargs: object) -> str:
        self.resolve_calls.append(kwargs)
        return "NEXT_STATE"


_MISSING = object()


class RecordingService:
    """Stand-in for the facade exposing only the locked helpers the saga reuses."""

    def __init__(
        self,
        *,
        expires_at: datetime | None = None,
        database_now: datetime = NOW,
        action: object = _MISSING,
        stale: _StaleActionAdopted | None = None,
        commit_error: BaseException | None = None,
        write_error: BaseException | None = None,
        chapter_current_document_id: UUID = DOCUMENT_ID,
    ) -> None:
        self.session = FakeSession(
            database_now, _finalize_action() if action is _MISSING else action
        )
        self.documents = FakeDocuments(write_error)
        self.stale = stale
        self.commit_error = commit_error
        self.chapter = SimpleNamespace(current_draft_document_id=chapter_current_document_id)
        self.run = SimpleNamespace(id=RUN_ID)
        self.state = _FakeState()
        self.checkpoint = SimpleNamespace()
        self.document = SimpleNamespace(id=DOCUMENT_ID)
        self.version = SimpleNamespace(id=CHILD_VERSION_ID, content_hash=CHILD_HASH)
        self.context = _context(expires_at=expires_at)
        self.context_kwargs: dict[str, object] | None = None
        self.key_calls: list[tuple[object, ...]] = []
        self.row_calls: list[tuple[object, dict[str, object]]] = []
        self.appended: list[tuple[object, object, object]] = []
        self.owner_calls: list[tuple[object, object]] = []
        self.commits = 0
        self.rollbacks = 0

    async def _author_context(self, **kwargs: object) -> object:
        self.context_kwargs = kwargs
        if self.stale is not None:
            raise self.stale
        return self.context

    def _decision_operation_key(self, *args: object) -> str:
        self.key_calls.append(args)
        return FIXED_KEY

    def _resolve_action_row(self, action: object, **kwargs: object) -> None:
        self.row_calls.append((action, kwargs))

    async def _require_project_owner(
        self, project_id: object, actor_user_id: object, **kwargs: object
    ) -> None:
        self.owner_calls.append((project_id, actor_user_id))

    async def _chapter(self, project_id: object, chapter_id: object, *, lock: bool) -> object:
        return self.chapter

    async def _run(
        self, project_id: object, chapter_id: object, workflow_run_id: object, *, lock: bool
    ) -> object:
        return self.run

    def _run_metadata(self, run: object) -> dict[str, object]:
        return {"provider_attempt": None, "reviewer_claim": None}

    async def _locked_state(self, run: object) -> tuple[object, object]:
        return self.state, self.checkpoint

    async def _locked_current_revision(self, **kwargs: object) -> tuple[object, object]:
        return self.document, self.version

    def _append_state(self, run: object, checkpoint: object, state: object) -> None:
        self.appended.append((run, checkpoint, state))

    async def _commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def _rollback(self) -> None:
        self.rollbacks += 1

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected service attribute access: {name}")


async def _submit(service: RecordingService, *, content: str = CONTENT) -> ChapterProductionV2Updated:
    return await ManualEditCoordinator(service).submit(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        action_request_id=ACTION_ID,
        actor_user_id=ACTOR_ID,
        content=content,
    )


@pytest.mark.anyio
async def test_submit_resolves_null_expiry_with_the_frozen_manual_transition() -> None:
    service = RecordingService()
    result = await _submit(service)

    assert result == ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None)
    assert service.context_kwargs == {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "workflow_run_id": RUN_ID,
        "action_request_id": ACTION_ID,
        "actor_user_id": ACTOR_ID,
    }
    # One clock query after the locks, then the finalize action query.
    assert len(service.session.scalar_calls) == 2
    assert "clock_timestamp" in str(service.session.scalar_calls[0])
    assert service.key_calls == [
        (RUN_ID, ACTION_ID, VERSION_ID, "manual"),
        (RUN_ID, ACTION_ID, VERSION_ID, "manual"),
    ]
    assert service.row_calls == [
        (
            service.context.action,
            {
                "status": ActionRequestStatus.REVISED,
                "decision": ChapterActionDecision.SUBMIT_MANUAL_EDIT,
                "actor_user_id": ACTOR_ID,
            },
        )
    ]
    assert len(service.documents.write_calls) == 1
    write = service.documents.write_calls[0]
    assert write["document_id"] == DOCUMENT_ID
    assert write["content"] == CONTENT
    assert write["source"] == DocumentSource.USER
    assert write["expected_current_version_id"] == VERSION_ID
    assert write["actor_user_id"] == ACTOR_ID
    assert write["workflow_run_id"] == RUN_ID
    assert write["change_summary"] == "Applied an authorized Chapter Production V2 manual edit."
    assert write["version_metadata"] == {
        "contract_version": "chapter-production-v2",
        "operation_key": FIXED_KEY,
    }
    assert service.state.resolve_calls == [
        {
            "action": service.context.binding,
            "decision": ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            "document_id": str(DOCUMENT_ID),
            "document_version_id": str(CHILD_VERSION_ID),
            "content_hash": CHILD_HASH,
        }
    ]
    assert service.appended == [(service.run, service.checkpoint, "NEXT_STATE")]
    assert service.owner_calls == [(PROJECT_ID, ACTOR_ID), (PROJECT_ID, ACTOR_ID)]
    assert service.commits == 1
    assert service.rollbacks == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n  ",
        "# a\r\n\r\nb",
        "# ok\x00content\n",
        "x" * (MAX_CHAPTER_CONTENT_BYTES + 1),
        "界" * (MAX_CHAPTER_CONTENT_BYTES // 3 + 1),
    ],
)
async def test_submit_rejects_invalid_content_before_any_mutation(content: str) -> None:
    service = RecordingService()

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service, content=content)

    assert service.row_calls == []
    assert service.documents.write_calls == []
    assert service.appended == []
    assert service.commits == 0
    assert service.rollbacks == 1


@pytest.mark.anyio
async def test_submit_uses_a_single_clock_timestamp_query_after_all_locks() -> None:
    service = RecordingService()
    await _submit(service)

    assert len(service.session.scalar_calls) == 2
    statement = str(service.session.scalar_calls[0])
    assert "clock_timestamp" in statement
    for forbidden in ("CURRENT_TIMESTAMP", "transaction_timestamp", "statement_timestamp"):
        assert forbidden not in statement


@pytest.mark.anyio
async def test_submit_resolves_a_future_expiry_before_the_database_clock() -> None:
    service = RecordingService(expires_at=NOW + timedelta(hours=1))
    result = await _submit(service)

    assert result == ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None)
    assert service.commits == 1


@pytest.mark.anyio
async def test_submit_fails_closed_once_the_database_clock_passes_expiry() -> None:
    service = RecordingService(expires_at=NOW - timedelta(hours=1))

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await _submit(service)

    assert str(raised.value) == "Chapter production input is invalid."
    assert service.row_calls == []
    assert service.documents.write_calls == []
    assert service.appended == []
    assert service.commits == 0
    # The saga raises before any mutation; the facade performs the rollback.
    assert service.rollbacks == 0


@pytest.mark.anyio
async def test_submit_fails_closed_at_the_expiry_boundary() -> None:
    service = RecordingService(expires_at=NOW)

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service)

    assert service.commits == 0
    assert service.rollbacks == 0


@pytest.mark.anyio
async def test_submit_returns_the_committed_stale_direct_user_adoption() -> None:
    adopted = ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None)
    service = RecordingService(stale=_StaleActionAdopted(adopted))

    result = await _submit(service)

    assert result == adopted
    assert service.session.scalar_calls == []
    assert service.commits == 0


@pytest.mark.anyio
async def test_submit_translates_document_commit_indeterminate() -> None:
    service = RecordingService(write_error=DocumentCommitIndeterminateError())

    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await _submit(service)

    assert service.rollbacks == 1
    assert service.commits == 0


@pytest.mark.anyio
async def test_finalize_fails_closed_without_exactly_one_committed_user_child() -> None:
    # The finalize query requires exactly one USER child; 0/N/foreign children
    # produce no matching action row, which the session reports as None.
    service = RecordingService(action=None)

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service)

    assert service.commits == 0
    assert service.rollbacks == 1


@pytest.mark.anyio
async def test_finalize_rejects_foreign_action_provenance() -> None:
    service = RecordingService(action=_finalize_action(project_id=UUID(int=7)))

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service)

    assert service.commits == 0
    assert service.rollbacks == 1


@pytest.mark.anyio
async def test_finalize_rejects_malformed_action_envelope() -> None:
    service = RecordingService(action=_finalize_action(metadata_key="not-a-hash"))

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service)

    assert service.commits == 0
    assert service.rollbacks == 1


@pytest.mark.anyio
async def test_finalize_rejects_non_dict_action_metadata() -> None:
    action = _finalize_action()
    action.metadata_ = "not-a-dict"  # type: ignore[assignment]
    service = RecordingService(action=action)

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service)

    assert service.rollbacks == 1


@pytest.mark.anyio
async def test_finalize_rejects_stale_current_document() -> None:
    service = RecordingService(chapter_current_document_id=UUID(int=9))

    with pytest.raises(ChapterProductionV2ValidationError):
        await _submit(service)

    assert service.commits == 0
    assert service.rollbacks == 1


@pytest.mark.anyio
async def test_submit_surfaces_commit_ack_loss_without_rollback() -> None:
    service = RecordingService(commit_error=ChapterProductionV2CommitIndeterminateError())

    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await _submit(service)

    assert service.commits == 1
    assert service.rollbacks == 0


@pytest.mark.anyio
async def test_submit_replays_the_finalize_deterministically() -> None:
    service = RecordingService()

    first = await _submit(service)
    second = await _submit(service)

    assert first == second == ChapterProductionV2Updated(
        RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None
    )
    assert service.commits == 2


@pytest.mark.anyio
async def test_submit_makes_zero_provider_calls_and_never_leaks_prose() -> None:
    service = RecordingService()
    await _submit(service)

    # RecordingService raises on any attribute outside the locked-helper
    # allowlist, so completing proves no provider/attempt/event authority was
    # touched and no fresh session was spawned.
    assert service.commits == 1
    assert service.rollbacks == 0
    # The resolved transition carries only content-free durable evidence.
    assert service.state.resolve_calls[0]["content_hash"] == CHILD_HASH
    assert "content" not in service.state.resolve_calls[0]


class FakeFacadeSession:
    def __init__(self) -> None:
        self.rollbacks = 0
        self.commits = 0

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        self.commits += 1


class StubCoordinator:
    def __init__(self, result: ChapterProductionV2Updated) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []
        self.error: BaseException | None = None

    async def submit(self, **kwargs: object) -> ChapterProductionV2Updated:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _facade(session: object) -> ChapterProductionV2Service:
    return ChapterProductionV2Service(
        session,  # type: ignore[arg-type]
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
    )


@pytest.mark.anyio
async def test_submit_manual_edit_delegates_to_the_coordinator() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    service._manual_edit = stub  # type: ignore[assignment]

    result = await service.submit_manual_edit(
        PROJECT_ID,
        CHAPTER_ID,
        RUN_ID,
        ACTION_ID,
        actor_user_id=ACTOR_ID,
        content=CONTENT,
    )

    assert result == ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None)
    assert stub.calls == [
        {
            "project_id": PROJECT_ID,
            "chapter_id": CHAPTER_ID,
            "workflow_run_id": RUN_ID,
            "action_request_id": ACTION_ID,
            "actor_user_id": ACTOR_ID,
            "content": CONTENT,
        }
    ]


@pytest.mark.anyio
async def test_submit_manual_edit_rejects_invalid_ids_before_delegation() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    service._manual_edit = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=UUID(int=0),
            content=CONTENT,
        )

    assert stub.calls == []


@pytest.mark.anyio
async def test_submit_manual_edit_rejects_non_string_content_before_delegation() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    service._manual_edit = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            content=object(),  # type: ignore[arg-type]
        )

    assert stub.calls == []


@pytest.mark.anyio
async def test_submit_manual_edit_rejects_oversize_content_before_delegation() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    service._manual_edit = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            content="x" * (MAX_CHAPTER_CONTENT_BYTES + 1),
        )

    assert stub.calls == []


@pytest.mark.anyio
async def test_submit_manual_edit_rolls_back_on_coordinator_validation_error() -> None:
    session = FakeFacadeSession()
    service = _facade(session)
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    stub.error = ChapterProductionV2ValidationError()
    service._manual_edit = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            content=CONTENT,
        )

    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_submit_manual_edit_rolls_back_and_hides_coordinator_failure() -> None:
    session = FakeFacadeSession()
    service = _facade(session)
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    stub.error = RuntimeError("private-coordinator-secret")
    service._manual_edit = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await service.submit_manual_edit(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            content=CONTENT,
        )

    assert "private-coordinator-secret" not in str(raised.value)
    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_submit_manual_edit_passes_commit_indeterminate_without_rollback() -> None:
    session = FakeFacadeSession()
    service = _facade(session)
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, CHILD_VERSION_ID, None))
    stub.error = ChapterProductionV2CommitIndeterminateError()
    service._manual_edit = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.submit_manual_edit(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            content=CONTENT,
        )

    assert session.rollbacks == 0
