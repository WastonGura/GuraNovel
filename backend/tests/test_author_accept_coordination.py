from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.agents import DeterministicChapterWriterProvider, WriterAgent
from app.services.author_accept_coordination import (
    AuthorAcceptCoordinator,
    _StaleActionAdopted,
    _expiry_precludes_resolution,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_v2_service import (
    ChapterProductionV2Service,
)


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTION_ID = UUID("44444444-4444-4444-8444-444444444444")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
ACTOR_ID = UUID("77777777-7777-4777-8777-777777777777")
NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_expiry_precludes_resolution_only_at_or_after_the_database_clock() -> None:
    assert _expiry_precludes_resolution(None, NOW) is False
    assert _expiry_precludes_resolution(NOW + timedelta(hours=1), NOW) is False
    assert _expiry_precludes_resolution(NOW, NOW) is True
    assert _expiry_precludes_resolution(NOW - timedelta(hours=1), NOW) is True


def _context(*, expires_at: datetime | None = None) -> SimpleNamespace:
    decisions: list[object] = []
    state = SimpleNamespace(
        resolve_action=lambda action, decision: decisions.append(decision) or "NEXT",
        decisions=decisions,
    )
    return SimpleNamespace(
        action=SimpleNamespace(expires_at=expires_at),
        state=state,
        binding=SimpleNamespace(),
        run=SimpleNamespace(id=RUN_ID),
        document=SimpleNamespace(id=DOCUMENT_ID),
        version=SimpleNamespace(id=VERSION_ID),
        checkpoint=SimpleNamespace(),
    )


class FakeSession:
    def __init__(self, database_now: datetime) -> None:
        self.database_now = database_now
        self.scalar_calls: list[object] = []

    async def scalar(self, statement: object) -> datetime:
        self.scalar_calls.append(statement)
        return self.database_now


class RecordingService:
    def __init__(
        self,
        context: object,
        database_now: datetime,
        *,
        stale: _StaleActionAdopted | None = None,
    ) -> None:
        self.session = FakeSession(database_now)
        self.context = context
        self.stale = stale
        self.context_kwargs: dict[str, object] | None = None
        self.row_calls: list[tuple[object, dict[str, object]]] = []
        self.appended: list[tuple[object, object, object]] = []
        self.commits = 0

    async def _author_context(self, **kwargs: object) -> object:
        self.context_kwargs = kwargs
        if self.stale is not None:
            raise self.stale
        return self.context

    def _resolve_action_row(self, action: object, **kwargs: object) -> None:
        self.row_calls.append((action, kwargs))

    def _append_state(self, run: object, checkpoint: object, state: object) -> None:
        self.appended.append((run, checkpoint, state))

    async def _commit(self) -> None:
        self.commits += 1

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected service attribute access: {name}")


async def _accept(service: RecordingService) -> ChapterProductionV2Updated:
    return await AuthorAcceptCoordinator(service).accept(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        action_request_id=ACTION_ID,
        actor_user_id=ACTOR_ID,
    )


@pytest.mark.anyio
async def test_accept_resolves_null_expiry_with_the_frozen_accept_transition() -> None:
    service = RecordingService(_context(), NOW)
    result = await _accept(service)

    assert result == ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None)
    assert service.context_kwargs == {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "workflow_run_id": RUN_ID,
        "action_request_id": ACTION_ID,
        "actor_user_id": ACTOR_ID,
    }
    assert service.context.state.decisions == ["accept"]
    assert len(service.row_calls) == 1
    _, row_kwargs = service.row_calls[0]
    assert row_kwargs["status"].value == "approved"
    assert row_kwargs["decision"].value == "accept"
    assert row_kwargs["actor_user_id"] == ACTOR_ID
    assert service.appended == [
        (service.context.run, service.context.checkpoint, "NEXT")
    ]
    assert service.commits == 1


@pytest.mark.anyio
async def test_accept_uses_a_single_clock_timestamp_query_after_all_locks() -> None:
    service = RecordingService(_context(), NOW)
    await _accept(service)

    assert len(service.session.scalar_calls) == 1
    assert "clock_timestamp" in str(service.session.scalar_calls[0])


@pytest.mark.anyio
async def test_accept_resolves_a_future_expiry_before_the_database_clock() -> None:
    service = RecordingService(_context(expires_at=NOW + timedelta(hours=1)), NOW)
    result = await _accept(service)

    assert result == ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None)
    assert service.commits == 1


@pytest.mark.anyio
async def test_accept_fails_closed_once_the_database_clock_passes_expiry() -> None:
    service = RecordingService(_context(expires_at=NOW - timedelta(hours=1)), NOW)

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await _accept(service)

    assert str(raised.value) == "Chapter production input is invalid."
    assert service.context.state.decisions == []
    assert service.row_calls == []
    assert service.appended == []
    assert service.commits == 0


@pytest.mark.anyio
async def test_accept_fails_closed_at_the_expiry_boundary() -> None:
    service = RecordingService(_context(expires_at=NOW), NOW)

    with pytest.raises(ChapterProductionV2ValidationError):
        await _accept(service)

    assert service.commits == 0


@pytest.mark.anyio
async def test_accept_returns_the_committed_stale_direct_user_adoption() -> None:
    adopted = ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None)
    service = RecordingService(_context(), NOW, stale=_StaleActionAdopted(adopted))

    result = await _accept(service)

    assert result == adopted
    assert service.session.scalar_calls == []
    assert service.commits == 0


@pytest.mark.anyio
async def test_accept_makes_zero_provider_calls() -> None:
    service = RecordingService(_context(), NOW)
    await _accept(service)

    assert service.commits == 1


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

    async def accept(self, **kwargs: object) -> ChapterProductionV2Updated:
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
async def test_resolve_author_action_delegates_to_the_coordinator() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None))
    service._author_accept = stub  # type: ignore[assignment]

    result = await service.resolve_author_action(
        PROJECT_ID,
        CHAPTER_ID,
        RUN_ID,
        ACTION_ID,
        actor_user_id=ACTOR_ID,
        decision="accept",
    )

    assert result == ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None)
    assert stub.calls == [
        {
            "project_id": PROJECT_ID,
            "chapter_id": CHAPTER_ID,
            "workflow_run_id": RUN_ID,
            "action_request_id": ACTION_ID,
            "actor_user_id": ACTOR_ID,
        }
    ]


@pytest.mark.anyio
async def test_resolve_author_action_rejects_non_accept_decisions_before_delegation() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None))
    service._author_accept = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            decision="request_revision",
        )

    assert stub.calls == []


@pytest.mark.anyio
async def test_resolve_author_action_rejects_invalid_ids_before_delegation() -> None:
    service = _facade(FakeFacadeSession())
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None))
    service._author_accept = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=UUID(int=0),
            decision="accept",
        )

    assert stub.calls == []


@pytest.mark.anyio
async def test_resolve_author_action_rolls_back_on_coordinator_validation_error() -> None:
    session = FakeFacadeSession()
    service = _facade(session)
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None))
    stub.error = ChapterProductionV2ValidationError()
    service._author_accept = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            decision="accept",
        )

    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_resolve_author_action_rolls_back_and_hides_coordinator_failure() -> None:
    session = FakeFacadeSession()
    service = _facade(session)
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None))
    stub.error = RuntimeError("private-coordinator-secret")
    service._author_accept = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await service.resolve_author_action(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            decision="accept",
        )

    assert session.rollbacks == 1
    assert "private-coordinator-secret" not in str(raised.value)


@pytest.mark.anyio
async def test_resolve_author_action_rethrows_commit_indeterminate_without_rollback() -> None:
    session = FakeFacadeSession()
    service = _facade(session)
    stub = StubCoordinator(ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None))
    stub.error = ChapterProductionV2CommitIndeterminateError()
    service._author_accept = stub  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.resolve_author_action(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            decision="accept",
        )

    assert session.rollbacks == 0
