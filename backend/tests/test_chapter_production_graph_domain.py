from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg
import pytest

import app.services.chapter_production_recovery_evidence as recovery_evidence
from app.services.chapter_production_graph_domain import (
    ChapterProductionInvocationContext,
    ChapterProductionSchedulingResult,
    advance_chapter_production,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import (
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
)


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = UUID("44444444-4444-4444-8444-444444444444")
ACTION_ID = UUID("55555555-5555-4555-8555-555555555555")
DOCUMENT_ID = UUID("66666666-6666-4666-8666-666666666666")
VERSION_ID = UUID("77777777-7777-4777-8777-777777777777")
CONTEXT = ChapterProductionInvocationContext(PROJECT_ID, CHAPTER_ID, ACTOR_ID)


def state(
    status: ChapterProductionStatus,
    *,
    awaiting_user: bool = False,
    action_request_id: UUID | None = None,
    failure_code: object = None,
    action_kind: ChapterActionKind | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        awaiting_user=awaiting_user,
        action_request_id=str(action_request_id) if action_request_id else None,
        failure_code=failure_code,
        action_kind=(
            action_kind
            or (
                ChapterActionKind.AUTHOR_REVISION
                if awaiting_user
                else None
            )
        ),
    )


def service(*states: object) -> SimpleNamespace:
    return SimpleNamespace(
        load_state=AsyncMock(side_effect=states),
        resume_drafting=AsyncMock(),
        execute_current_review=AsyncMock(),
        finalize_without_reader_panel=AsyncMock(),
        reconcile_indeterminate=AsyncMock(),
        validate_scheduling_action=AsyncMock(),
    )


def scope_kwargs() -> dict[str, UUID]:
    return {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "workflow_run_id": RUN_ID,
        "actor_user_id": ACTOR_ID,
    }


def test_trusted_context_is_content_free_and_outside_graph_contracts() -> None:
    assert tuple(CONTEXT.__dataclass_fields__) == (
        "project_id",
        "chapter_id",
        "actor_user_id",
    )
    assert "workflow_run_id" not in CONTEXT.__dataclass_fields__
    for value in ("prose", "content", "report", "locator", "session"):
        assert value not in repr(CONTEXT).lower()
    with pytest.raises(ChapterProductionV2ValidationError):
        ChapterProductionInvocationContext(UUID(int=0), CHAPTER_ID, ACTOR_ID)


def test_context_normalizes_pgproto_uuid_and_contains_hostile_uuid_failures() -> None:
    pg_uuid = asyncpg.pgproto.pgproto.UUID("11111111111141118111111111111111")
    context = ChapterProductionInvocationContext(pg_uuid, pg_uuid, pg_uuid)
    assert type(context.project_id) is UUID

    class Hostile:
        @property
        def int(self) -> int:
            raise RuntimeError("canary")

        def __str__(self) -> str:
            raise RuntimeError("canary")

    with pytest.raises(ChapterProductionV2ValidationError) as captured:
        ChapterProductionInvocationContext(Hostile(), CHAPTER_ID, ACTOR_ID)  # type: ignore[arg-type]
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "canary" not in repr(captured.value)


@pytest.mark.anyio
async def test_real_domain_states_reconstruct_to_draft_and_reconcile() -> None:
    initial = ChapterProductionState.initial(
        chapter_workflow_run_id=str(RUN_ID),
        chapter_id=str(CHAPTER_ID),
        review_policy_version="chapter-quality-v1",
        chief_editor_required=True,
    )
    failed = initial.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)

    drafting = await advance_chapter_production(
        service(initial), context=CONTEXT, workflow_run_id=RUN_ID, cursor="reconstruct"
    )
    recovering = await advance_chapter_production(
        service(failed), context=CONTEXT, workflow_run_id=RUN_ID, cursor="reconstruct"
    )

    assert (drafting.kind, drafting.next_cursor) == ("continue", "draft")
    assert (recovering.kind, recovering.next_cursor) == ("continue", "reconcile")


@pytest.mark.anyio
async def test_real_failed_states_return_only_closed_failure_codes() -> None:
    initial = ChapterProductionState.initial(
        chapter_workflow_run_id=str(RUN_ID),
        chapter_id=str(CHAPTER_ID),
        review_policy_version="chapter-quality-v1",
        chief_editor_required=True,
    )
    retryable = initial.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)
    uncertain = initial.fail(ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE)

    retry = await advance_chapter_production(
        service(retryable), context=CONTEXT, workflow_run_id=RUN_ID, cursor="draft"
    )
    reconcile = await advance_chapter_production(
        service(uncertain), context=CONTEXT, workflow_run_id=RUN_ID, cursor="draft"
    )

    assert (retry.kind, retry.failure_code) == (
        "retryable-failure",
        "provider_unavailable",
    )
    assert (reconcile.kind, reconcile.failure_code) == (
        "reconciliation-required",
        "document_commit_indeterminate",
    )


def test_scheduling_result_rejects_mixed_or_extended_shapes() -> None:
    with pytest.raises(ChapterProductionV2ValidationError):
        ChapterProductionSchedulingResult(
            kind="continue",
            next_cursor="draft",
            failure_code="provider_unavailable",
        )
    with pytest.raises(TypeError):
        ChapterProductionSchedulingResult(  # type: ignore[call-arg]
            kind="complete", completion_code="success", prose="canary"
        )
    for kind, failure_code in (
        ("retryable-failure", "reconciliation_required"),
        ("reconciliation-required", "provider_unavailable"),
    ):
        with pytest.raises(ChapterProductionV2ValidationError):
            ChapterProductionSchedulingResult(kind=kind, failure_code=failure_code)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cursor", "before", "after", "method", "next_cursor"),
    (
        ("draft", ChapterProductionStatus.DRAFTING, ChapterProductionStatus.AUTHOR_REVISION, "resume_drafting", None),
        ("editor_review", ChapterProductionStatus.EDITOR_REVIEW, ChapterProductionStatus.CHIEF_FINAL_REVIEW, "execute_current_review", "chief_editor_review"),
        ("chief_editor_review", ChapterProductionStatus.CHIEF_FINAL_REVIEW, ChapterProductionStatus.LORE_FINAL_REVIEW, "execute_current_review", "lore_review"),
        ("lore_review", ChapterProductionStatus.LORE_FINAL_REVIEW, ChapterProductionStatus.REVISION_READY, "execute_current_review", "mark_revision_ready"),
        ("finalize", ChapterProductionStatus.REVISION_READY, ChapterProductionStatus.COMPLETED, "finalize_without_reader_panel", None),
        ("reconcile", ChapterProductionStatus.FAILED, ChapterProductionStatus.EDITOR_REVIEW, "reconcile_indeterminate", "editor_review"),
    ),
)
async def test_cursor_calls_only_its_existing_facade_entry(
    cursor: str,
    before: ChapterProductionStatus,
    after: ChapterProductionStatus,
    method: str,
    next_cursor: str | None,
) -> None:
    before_state = state(
        before,
        failure_code=(
            ChapterFailureCode.PROVIDER_UNAVAILABLE
            if before is ChapterProductionStatus.FAILED
            else None
        ),
    )
    after_state = state(
        after,
        awaiting_user=after is ChapterProductionStatus.AUTHOR_REVISION,
        action_request_id=(ACTION_ID if after is ChapterProductionStatus.AUTHOR_REVISION else None),
    )
    facade = service(before_state, after_state)
    if method == "reconcile_indeterminate":
        facade.reconcile_indeterminate.return_value = after_state

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor=cursor
    )

    getattr(facade, method).assert_awaited_once_with(**scope_kwargs())
    for other in (
        "resume_drafting",
        "execute_current_review",
        "finalize_without_reader_panel",
        "reconcile_indeterminate",
    ):
        if other != method:
            getattr(facade, other).assert_not_awaited()
    if after is ChapterProductionStatus.AUTHOR_REVISION:
        assert (result.kind, result.action_request_id) == ("await-user", ACTION_ID)
    elif after is ChapterProductionStatus.COMPLETED:
        assert result.kind == "complete"
    else:
        assert (result.kind, result.next_cursor) == ("continue", next_cursor)


@pytest.mark.anyio
async def test_fresh_state_mismatch_routes_without_calling_wrong_facade_entry() -> None:
    facade = service(state(ChapterProductionStatus.EDITOR_REVIEW))

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="draft"
    )

    assert (result.kind, result.next_cursor) == ("continue", "editor_review")
    facade.resume_drafting.assert_not_awaited()
    facade.execute_current_review.assert_not_awaited()


@pytest.mark.anyio
async def test_gate_await_and_ready_reuse_are_read_only_scheduling_entries() -> None:
    gate = service(
        state(
            ChapterProductionStatus.AUTHOR_REVISION,
            awaiting_user=True,
            action_request_id=ACTION_ID,
        )
    )
    ready = service(state(ChapterProductionStatus.REVISION_READY))

    waiting = await advance_chapter_production(
        gate, context=CONTEXT, workflow_run_id=RUN_ID, cursor="await_author_action"
    )
    validated = await advance_chapter_production(
        ready, context=CONTEXT, workflow_run_id=RUN_ID, cursor="mark_revision_ready"
    )

    assert (waiting.kind, waiting.action_request_id) == ("await-user", ACTION_ID)
    assert (validated.kind, validated.next_cursor) == ("continue", "finalize")
    gate.load_state.assert_awaited_once_with(
        **scope_kwargs(), require_langgraph_runtime=True
    )
    ready.load_state.assert_awaited_once_with(
        **scope_kwargs(), require_langgraph_runtime=True
    )
    for facade in (gate, ready):
        facade.resume_drafting.assert_not_awaited()
        facade.execute_current_review.assert_not_awaited()
        facade.finalize_without_reader_panel.assert_not_awaited()
        facade.reconcile_indeterminate.assert_not_awaited()
    gate.validate_scheduling_action.assert_awaited_once_with(
        **scope_kwargs(),
        action_request_id=ACTION_ID,
        action_kind=ChapterActionKind.AUTHOR_REVISION,
    )
    ready.validate_scheduling_action.assert_not_awaited()


@pytest.mark.anyio
async def test_stale_author_gate_validation_never_adopts_or_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    facade = ChapterProductionV2Service(session, writer_agent=object())  # type: ignore[arg-type]
    chapter = SimpleNamespace(id=CHAPTER_ID, current_draft_document_id=DOCUMENT_ID)
    run = SimpleNamespace(id=RUN_ID, project_id=PROJECT_ID, chapter_id=CHAPTER_ID)
    checkpoint = object()
    current = state(
        ChapterProductionStatus.AUTHOR_REVISION,
        awaiting_user=True,
        action_request_id=ACTION_ID,
    )
    action = SimpleNamespace(
        id=ACTION_ID,
        status="pending",
        user_decision=None,
        user_feedback=None,
        resolved_by_id=None,
        resolved_at=None,
    )
    stale_document = SimpleNamespace(id=DOCUMENT_ID)
    stale_version = SimpleNamespace(
        id=VERSION_ID,
        source="user",
        actor_user_id=ACTOR_ID,
        agent_role=None,
        workflow_run_id=None,
    )
    appended_checkpoints: list[object] = []

    facade._require_project_owner = AsyncMock()  # type: ignore[method-assign]
    facade._chapter = AsyncMock(return_value=chapter)  # type: ignore[method-assign]
    facade._run = AsyncMock(return_value=run)  # type: ignore[method-assign]
    facade._action_metadata = lambda _action: {  # type: ignore[method-assign]
        "document_id": str(DOCUMENT_ID),
        "document_version_id": str(VERSION_ID),
        "content_hash": "a" * 64,
    }
    monkeypatch.setattr(
        recovery_evidence,
        "locked_state",
        AsyncMock(return_value=(current, checkpoint)),
    )
    monkeypatch.setattr(
        recovery_evidence,
        "_locked_author_action",
        AsyncMock(return_value=(action, 1)),
    )
    monkeypatch.setattr(
        recovery_evidence,
        "_locked_author_document",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        recovery_evidence,
        "_locked_stale_author_document",
        AsyncMock(return_value=(stale_document, stale_version)),
    )

    async def adopt(*_args: object, **kwargs: object) -> None:
        action.status = "cancelled"
        appended_checkpoints.append(kwargs["checkpoint"])
        await session.commit()

    adoption = AsyncMock(side_effect=adopt)
    monkeypatch.setattr(recovery_evidence, "_commit_stale_author_adoption", adoption)

    with pytest.raises(ChapterProductionV2ValidationError):
        await facade.validate_scheduling_action(
            PROJECT_ID,
            CHAPTER_ID,
            RUN_ID,
            ACTION_ID,
            actor_user_id=ACTOR_ID,
            action_kind=ChapterActionKind.AUTHOR_REVISION,
        )

    assert action.status == "pending"
    assert appended_checkpoints == []
    adoption.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_exact_langgraph_pin_is_required_before_any_domain_entry() -> None:
    facade = service(state(ChapterProductionStatus.DRAFTING))
    facade.load_state.side_effect = ChapterProductionV2ValidationError()

    with pytest.raises(ChapterProductionV2ValidationError):
        await advance_chapter_production(
            facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="draft"
        )

    facade.load_state.assert_awaited_once_with(
        **scope_kwargs(), require_langgraph_runtime=True
    )
    facade.resume_drafting.assert_not_awaited()


@pytest.mark.anyio
async def test_scope_or_actor_failure_is_fixed_and_precedes_domain_entry() -> None:
    facade = service()
    facade.load_state.side_effect = ChapterProductionV2ValidationError()

    with pytest.raises(ChapterProductionV2ValidationError) as captured:
        await advance_chapter_production(
            facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="draft"
        )

    assert captured.value.__cause__ is None
    facade.resume_drafting.assert_not_awaited()


def test_domain_seam_has_no_graph_session_provider_or_persistence_authority() -> None:
    module = Path(__file__).parents[1] / "app/services/chapter_production_graph_domain.py"
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "app.graph" not in imports
    assert imports.isdisjoint({"sqlalchemy", "app.models", "app.agents", "app.llm", "app.workspace"})
    for forbidden in (
        "AsyncSession",
        "DocumentService",
        "commit(",
        "rollback(",
        "Provider(",
        "provider_selection",
        "GRAPH_ENABLED",
        "build_chapter_production_graph",
    ):
        assert forbidden not in source
