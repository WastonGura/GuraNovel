from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ProviderError,
    ChapterProductionV2ValidationError,
)
from app.services.feedback_revision_handoff import (
    FeedbackRevisionHandoff,
    FeedbackRevisionPlan,
    _Claim,
    _expiry_precludes_resolution,
    _new_attempt_id,
    _safe_cancelled_error,
)
from app.workflows.chapter_production import ChapterFailureCode, ChapterProductionStatus


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTION_ID = UUID("44444444-4444-4444-8444-444444444444")
ACTOR_ID = UUID("77777777-7777-4777-8777-777777777777")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
SEGMENT_ID = UUID("88888888-8888-4888-8888-888888888888")


def test_expiry_precludes_resolution_only_at_or_after_the_database_clock() -> None:
    assert _expiry_precludes_resolution(None, 10) is False
    assert _expiry_precludes_resolution(11, 10) is False
    assert _expiry_precludes_resolution(10, 10) is True
    assert _expiry_precludes_resolution(9, 10) is True


def test_provider_cancellation_is_rebuilt_without_untrusted_details() -> None:
    unsafe = asyncio.CancelledError("canary-provider-secret")
    safe = _safe_cancelled_error(unsafe)

    assert type(safe) is asyncio.CancelledError
    assert safe.args == ()
    assert safe.__cause__ is None and safe.__context__ is None
    assert "canary" not in repr(safe)


def test_provider_attempt_ids_are_unique_canonical_uuids() -> None:
    first = _new_attempt_id()
    second = _new_attempt_id()

    assert first != second
    assert str(UUID(first)) == first and str(UUID(second)) == second


def test_replacement_plan_is_frozen_and_never_reprs_prose() -> None:
    plan = FeedbackRevisionPlan(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        operation_key="b" * 64,
        attempt_id=str(UUID(int=1)),
        attempt_checkpoint_index=1,
        feedback="canary-private-feedback",
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(secret="canary-segment-prose"),
        candidate=SimpleNamespace(segments=(), secret="canary-candidate-prose"),
    )

    with pytest.raises(FrozenInstanceError):
        plan.feedback = "mutated"  # type: ignore[misc]
    assert not hasattr(plan, "__dict__")
    assert "canary" not in repr(plan)
    assert "canary-private-feedback" not in repr(plan)
    assert "canary-segment-prose" not in repr(plan)
    assert "canary-candidate-prose" not in repr(plan)


def _handoff(service: object, revision_agent: object) -> FeedbackRevisionHandoff:
    handoff = object.__new__(FeedbackRevisionHandoff)
    handoff.service = service
    handoff.phase_sessions = None
    service.revision_agent = revision_agent  # type: ignore[attr-defined]
    return handoff


class _RecordingService:
    def __init__(self) -> None:
        self.release_calls: list[tuple[object, dict[str, object]]] = []
        self.fail_calls: list[tuple[object, object, dict[str, object]]] = []

    async def _release_attempt(self, workflow_run_id: object, **kwargs: object) -> None:
        self.release_calls.append((workflow_run_id, kwargs))

    async def _fail_provider(
        self, workflow_run_id: object, failure_code: object, **kwargs: object
    ) -> None:
        self.fail_calls.append((workflow_run_id, failure_code, kwargs))


def _claim(request: object) -> _Claim:
    return _Claim(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        operation_key="b" * 64,
        attempt_id=str(UUID(int=2)),
        attempt_checkpoint_index=3,
        feedback="plain feedback",
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(),
        request=request,
    )


class _Agent:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.received: object = None

    async def user_feedback_revision(self, request: object) -> object:
        self.received = request
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.mark.anyio
async def test_provider_receives_the_exact_claim_request_and_no_writes_happen() -> None:
    request = SimpleNamespace(plain="only-plain-values")
    candidate = SimpleNamespace(segments=())
    agent = _Agent(candidate)
    service = _RecordingService()
    handoff = _handoff(service, agent)

    result = await handoff._provider(RUN_ID, _claim(request))

    assert result is candidate
    assert agent.received is request
    assert not hasattr(agent.received, "session")
    assert not hasattr(agent.received, "repository")
    assert service.release_calls == [] and service.fail_calls == []


@pytest.mark.anyio
async def test_provider_cancellation_releases_the_exact_current_attempt() -> None:
    agent = _Agent(asyncio.CancelledError("canary-cancel"))
    service = _RecordingService()
    handoff = _handoff(service, agent)
    claim = _claim(SimpleNamespace())

    with pytest.raises(asyncio.CancelledError) as raised:
        await handoff._provider(RUN_ID, claim)

    assert raised.value.args == ()
    assert "canary" not in repr(raised.value)
    assert service.release_calls == [
        (
            RUN_ID,
            {
                "expected_key": claim.operation_key,
                "expected_attempt_id": claim.attempt_id,
                "expected_kind": "feedback",
                "expected_checkpoint_index": claim.attempt_checkpoint_index,
                "restore_feedback": True,
            },
        )
    ]
    assert service.fail_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderTimeoutError(), ChapterFailureCode.PROVIDER_TIMEOUT),
        (ProviderInvalidOutputError(), ChapterFailureCode.INVALID_PROVIDER_OUTPUT),
        (RuntimeError("private-provider-secret"), ChapterFailureCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_provider_failure_fails_only_the_exact_current_attempt(error: object, code: object) -> None:
    agent = _Agent(error)
    service = _RecordingService()
    handoff = _handoff(service, agent)
    claim = _claim(SimpleNamespace())

    with pytest.raises(ChapterProductionV2ProviderError) as raised:
        await handoff._provider(RUN_ID, claim)

    assert str(raised.value) == "Chapter drafting failed safely."
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert service.release_calls == []
    assert service.fail_calls == [
        (
            RUN_ID,
            code,
            {
                "expected_status": ChapterProductionStatus.DRAFTING,
                "expected_checkpoint_index": claim.attempt_checkpoint_index,
                "expected_attempt_key": claim.operation_key,
                "expected_attempt_id": claim.attempt_id,
            },
        )
    ]


class _Stop(Exception):
    pass


def _ordered_service(order: list[str]) -> object:
    class Service:
        def _validated_ids(self, *values: object) -> tuple[object, ...]:
            order.append("validated_ids")
            return values

        def _validated_uuid_sequence(self, values: object, *, maximum: int) -> object:
            order.append("validated_uuid_sequence")
            return values

        def _validated_feedback(self, feedback: str) -> str:
            order.append("validated_feedback")
            if "\x00" in feedback:
                raise ChapterProductionV2ValidationError()
            return feedback

        async def _recover_failed_attempt(self, **kwargs: object) -> None:
            order.append("recover")
            raise _Stop()

    return Service()


@pytest.mark.anyio
async def test_canonical_feedback_validation_precedes_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.feedback_revision_handoff as module

    order: list[str] = []
    original = module.sha256_content

    def recording_sha256(value: object) -> str:
        order.append("sha256")
        return original(value)

    monkeypatch.setattr(module, "sha256_content", recording_sha256)
    handoff = _handoff(_ordered_service(order), None)

    with pytest.raises(_Stop):
        await handoff.execute(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            action_request_id=ACTION_ID,
            actor_user_id=ACTOR_ID,
            feedback="valid feedback",
            target_segment_ids=(SEGMENT_ID,),
        )

    assert order.index("validated_feedback") < order.index("sha256")


@pytest.mark.anyio
async def test_invalid_feedback_is_rejected_before_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.feedback_revision_handoff as module

    order: list[str] = []
    original = module.sha256_content

    def recording_sha256(value: object) -> str:
        order.append("sha256")
        return original(value)

    monkeypatch.setattr(module, "sha256_content", recording_sha256)
    handoff = _handoff(_ordered_service(order), None)

    with pytest.raises(ChapterProductionV2ValidationError):
        await handoff.execute(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            action_request_id=ACTION_ID,
            actor_user_id=ACTOR_ID,
            feedback="bad\x00feedback",
            target_segment_ids=(SEGMENT_ID,),
        )

    assert "sha256" not in order



class _TimeoutAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def user_feedback_revision(self, request: object) -> object:
        self.calls.append(request)
        raise ProviderTimeoutError()


@pytest.mark.anyio
async def test_provider_reads_the_facade_agent_not_a_captured_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents import RevisionAgent

    agent_a = RevisionAgent(object())
    calls_a: list[object] = []

    async def succeed(request: object) -> object:
        calls_a.append(request)
        return SimpleNamespace(segments=())

    monkeypatch.setattr(agent_a, "user_feedback_revision", succeed)
    agent_b = _TimeoutAgent()
    service = _RecordingService()
    service.revision_agent = agent_a  # type: ignore[attr-defined]
    handoff = FeedbackRevisionHandoff(service, None, agent_a)
    service.revision_agent = agent_b  # mutate AFTER construction

    with pytest.raises(ChapterProductionV2ProviderError):
        await handoff._provider(RUN_ID, _claim(SimpleNamespace()))

    assert calls_a == []
    assert len(agent_b.calls) == 1
    assert service.release_calls == []
    assert service.fail_calls[0][1] == ChapterFailureCode.PROVIDER_TIMEOUT


@pytest.mark.anyio
async def test_claim_reads_none_revision_agent_dynamically(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.feedback_revision_handoff as module
    from app.agents import RevisionAgent

    agent_a = RevisionAgent(object())
    service = _RecordingService()
    service.revision_agent = agent_a  # type: ignore[attr-defined]
    handoff = FeedbackRevisionHandoff(service, None, agent_a)
    service.revision_agent = None  # mutate AFTER construction

    async def fake_author_context(self: object, *, scope: object) -> object:
        return SimpleNamespace(action=SimpleNamespace(expires_at=None))

    monkeypatch.setattr(module._FeedbackClaimPhase, "author_context", fake_author_context)

    with pytest.raises(ChapterProductionV2ProviderError):
        await handoff._claim_locked(SimpleNamespace(), SimpleNamespace())

