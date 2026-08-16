from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReviewProviderError,
)
from app.services.chapter_review_coordinator import ChapterReviewCoordinator
from app.workflows.chapter_production import ChapterReviewStage


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = UUID("77777777-7777-4777-8777-777777777777")


class _FakeAgent:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def review(self, request: object) -> object:
        raise self.error


def test_review_provider_error_contract() -> None:
    error = ChapterProductionV2ReviewProviderError()

    assert isinstance(error, Exception)
    assert error.status_code == 503
    assert error.code == "chapter_production_v2_review_provider_failed"
    assert error.message == "Chapter review failed safely."


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("agent_error",),
    [
        (ProviderTimeoutError(),),
        (ProviderInvalidOutputError(),),
        (RuntimeError("untrusted provider text"),),
    ],
)
async def test_execute_review_raises_review_provider_error(
    agent_error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_claim(service: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            stage=ChapterReviewStage.EDITOR,
            run=SimpleNamespace(),
            operation_key="a" * 64,
            request_hash="b" * 64,
            request=SimpleNamespace(),
        )

    async def fake_fail(service: object, **kwargs: object) -> None:
        return None

    async def fake_release(service: object, *args: object, **kwargs: object) -> None:
        return None

    service = SimpleNamespace(
        _validated_ids=lambda *values: values,
        _run_metadata=lambda run: {
            "reviewer_claim": {"claim_id": str(uuid4())}
        },
        editor_agent=_FakeAgent(agent_error),
        chief_editor_agent=None,
        lore_agent=None,
    )
    monkeypatch.setattr(
        "app.services.chapter_review_coordinator.claim_current_review", fake_claim
    )
    monkeypatch.setattr(
        "app.services.chapter_review_coordinator.fail_reviewer", fake_fail
    )
    monkeypatch.setattr(
        "app.services.chapter_review_coordinator.release_reviewer_claim", fake_release
    )
    coordinator = ChapterReviewCoordinator(service)  # type: ignore[arg-type]

    with pytest.raises(ChapterProductionV2ReviewProviderError) as exc_info:
        await coordinator.execute_review(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            actor_user_id=ACTOR_ID,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "chapter_production_v2_review_provider_failed"
    assert exc_info.value.message == "Chapter review failed safely."
