from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.agents import DeterministicChapterWriterProvider, WriterAgent
from app.services.author_accept_coordination import _StaleActionAdopted
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.services.feedback_revision_handoff import FeedbackRevisionPlan


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTION_ID = UUID("44444444-4444-4444-8444-444444444444")
ACTOR_ID = UUID("77777777-7777-4777-8777-777777777777")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
SEGMENT_ID = UUID("88888888-8888-4888-8888-888888888888")


class _FakeSession:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class _StubHandoff:
    def __init__(self, plan: FeedbackRevisionPlan) -> None:
        self.plan = plan
        self.calls: list[dict[str, object]] = []
        self.error: BaseException | None = None

    async def execute(self, **kwargs: object) -> FeedbackRevisionPlan:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.plan


class _StubSaga:
    def __init__(self, identity: object, updated: ChapterProductionV2Updated) -> None:
        self.identity = identity
        self.updated = updated
        self.persist_calls: list[tuple[object, dict[str, object]]] = []
        self.finalize_calls: list[tuple[object, dict[str, object]]] = []

    async def persist(self, plan: object, **kwargs: object) -> object:
        self.persist_calls.append((plan, kwargs))
        return self.identity

    async def finalize(self, identity: object, **kwargs: object) -> ChapterProductionV2Updated:
        self.finalize_calls.append((identity, kwargs))
        return self.updated


def _service() -> ChapterProductionV2Service:
    return ChapterProductionV2Service(
        _FakeSession(),  # type: ignore[arg-type]
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
    )


def _plan() -> FeedbackRevisionPlan:
    return FeedbackRevisionPlan(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        operation_key="b" * 64,
        attempt_id=str(UUID(int=1)),
        attempt_checkpoint_index=1,
        feedback="plain feedback",
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(),
        candidate=SimpleNamespace(
            segments=(SimpleNamespace(segment_id=SEGMENT_ID, content="New scene."),)
        ),
    )


@pytest.mark.anyio
async def test_facade_composes_the_extracted_persistence_and_finalization_saga() -> None:
    service = _service()
    plan = _plan()
    identity = SimpleNamespace(operation_key=plan.operation_key)
    updated = ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, ACTION_ID)
    handoff = _StubHandoff(plan)
    saga = _StubSaga(identity, updated)
    service._feedback_handoff = handoff  # type: ignore[assignment]
    service._feedback_saga = saga  # type: ignore[assignment]

    result = await service.request_user_feedback_revision(
        PROJECT_ID,
        CHAPTER_ID,
        RUN_ID,
        ACTION_ID,
        actor_user_id=ACTOR_ID,
        feedback="plain feedback",
        target_segment_ids=(SEGMENT_ID,),
    )

    assert handoff.calls == [
        {
            "project_id": PROJECT_ID,
            "chapter_id": CHAPTER_ID,
            "workflow_run_id": RUN_ID,
            "action_request_id": ACTION_ID,
            "actor_user_id": ACTOR_ID,
            "feedback": "plain feedback",
            "target_segment_ids": (SEGMENT_ID,),
        }
    ]
    assert len(saga.persist_calls) == 1
    assert saga.persist_calls[0][0] is plan
    assert saga.persist_calls[0][1] == {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "workflow_run_id": RUN_ID,
        "action_request_id": ACTION_ID,
        "actor_user_id": ACTOR_ID,
    }
    assert len(saga.finalize_calls) == 1
    assert saga.finalize_calls[0][0] is identity
    assert saga.finalize_calls[0][1] == {"actor_user_id": ACTOR_ID}
    assert result == updated


@pytest.mark.anyio
async def test_facade_returns_the_committed_stale_adoption_result() -> None:
    service = _service()
    adopted = ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None)
    stub = _StubHandoff(_plan())
    stub.error = _StaleActionAdopted(adopted)
    service._feedback_handoff = stub  # type: ignore[assignment]
    saga = _StubSaga(SimpleNamespace(), adopted)
    service._feedback_saga = saga  # type: ignore[assignment]

    result = await service.request_user_feedback_revision(
        PROJECT_ID,
        CHAPTER_ID,
        RUN_ID,
        ACTION_ID,
        actor_user_id=ACTOR_ID,
        feedback="plain feedback",
        target_segment_ids=(SEGMENT_ID,),
    )

    assert result == adopted
    assert saga.persist_calls == []
    assert saga.finalize_calls == []


@pytest.mark.anyio
async def test_facade_rejects_oversized_feedback_before_delegation() -> None:
    service = _service()
    stub = _StubHandoff(_plan())
    saga = _StubSaga(
        SimpleNamespace(),
        ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None),
    )
    service._feedback_handoff = stub  # type: ignore[assignment]
    service._feedback_saga = saga  # type: ignore[assignment]

    with pytest.raises(ChapterProductionV2ValidationError):
        await service.request_user_feedback_revision(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            feedback="x" * 8001,
            target_segment_ids=(SEGMENT_ID,),
        )

    assert stub.calls == []
    assert saga.persist_calls == []
