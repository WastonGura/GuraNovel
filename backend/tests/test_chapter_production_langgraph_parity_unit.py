from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeCancelledError
from langgraph.graph import END
import pytest

import app.services.initial_draft_lifecycle as lifecycle_module
from app.graph import chapter_production_topology
from app.graph.chapter_production_execution import (
    build_chapter_production_ports,
    invoke_chapter_production_graph,
)
from app.graph.chapter_production_topology import build_chapter_production_graph
from app.graph.contracts import (
    GRAPH_ID,
    GRAPH_VERSION,
    GraphError,
    GraphState,
    OutcomeKind,
    parse_graph_state,
)
from app.graph.runtime import (
    GraphDefinition,
    NODE_NAMES,
    build_config,
)
from app.services.chapter_production_graph_domain import (
    ChapterProductionInvocationContext,
    ChapterProductionSchedulingResult,
    advance_chapter_production,
)
from app.services.chapter_production_runtime import (
    chapter_production_langgraph_pin,
    chapter_production_runtime_pin,
    strict_runtime,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Started,
    ChapterProductionV2ValidationError,
)
from app.services.initial_bootstrap_evidence import (
    InitialBootstrapBinding,
    pristine_run_metadata,
)
from app.services.initial_draft_lifecycle import (
    InitialDraftLifecycle,
)
from app.services.initial_provider_handoff import _InitialEvidencePhase
from app.services.initial_run_bootstrap import _InitialRunPhase
from app.workflows.chapter_production import (
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionStatus,
)


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = UUID("44444444-4444-4444-8444-444444444444")
OUTLINE_ID = UUID("55555555-5555-4555-8555-555555555555")
OUTLINE_VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
DRAFT_ID = UUID("77777777-7777-4777-8777-777777777777")
DRAFT_VERSION_ID = UUID("88888888-8888-4888-8888-888888888888")
ACTION_ID = UUID("99999999-9999-4999-8999-999999999999")
HASH = "a" * 64
KEY = "b" * 64
CONTEXT = ChapterProductionInvocationContext(PROJECT_ID, CHAPTER_ID, ACTOR_ID)


def sample_binding(run_id: UUID = RUN_ID) -> InitialBootstrapBinding:
    return InitialBootstrapBinding(
        run_id,
        CHAPTER_ID,
        OUTLINE_ID,
        OUTLINE_VERSION_ID,
        HASH,
        KEY,
        True,
    )


def sample_graph_state(cursor: str = "draft") -> GraphState:
    return {
        "workflow_run_id": RUN_ID,
        "graph_id": GRAPH_ID,
        "graph_version": GRAPH_VERSION,
        "cursor": cursor,
        "workflow_checkpoint_index": 0,
        "invocation_id": UUID("55555555-5555-4555-8555-555555555555"),
        "attempt_id": None,
        "claim_id": None,
        "action_request_id": None,
        "resume_reason": "new",
    }


def domain_state(
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


def mock_service(*states: object) -> SimpleNamespace:
    return SimpleNamespace(
        load_state=AsyncMock(side_effect=states),
        _schedule_drafting=AsyncMock(),
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


class _LeaseContext:
    def __init__(self, marker: dict[str, bool] | None = None) -> None:
        self.marker = marker if marker is not None else {}
        self.session = SimpleNamespace()

    async def __aenter__(self) -> object:
        self.marker["active"] = True
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        self.marker["active"] = False


def mock_lifecycle(marker: dict[str, bool] | None = None) -> InitialDraftLifecycle:
    value = object.__new__(InitialDraftLifecycle)
    value._service = SimpleNamespace()
    value.sessions = SimpleNamespace(lease=lambda: _LeaseContext(marker))
    value.handoff = SimpleNamespace(
        bootstrap=SimpleNamespace(start_or_resume=AsyncMock(return_value=RUN_ID))
    )
    value.finalizer = SimpleNamespace(resume=AsyncMock(return_value=None))
    value._run = AsyncMock(
        return_value=ChapterProductionV2Started(
            RUN_ID, ACTION_ID, OUTLINE_ID, OUTLINE_VERSION_ID, DRAFT_ID, DRAFT_VERSION_ID
        )
    )
    return value


# ============================================================================
# 1. Lifecycle Parity Across All 11 Nodes
# ============================================================================


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cursor", "status", "awaiting", "action_id", "expected_kind", "expected_target"),
    (
        ("reconstruct", ChapterProductionStatus.DRAFTING, False, None, "continue", "draft"),
        ("reconstruct", ChapterProductionStatus.AUTHOR_REVISION, True, ACTION_ID, "await-user", None),
        ("reconstruct", ChapterProductionStatus.AUTHOR_REVISION, False, None, "continue", "author_revision"),
        ("reconstruct", ChapterProductionStatus.EDITOR_REVIEW, False, None, "continue", "editor_review"),
        ("reconstruct", ChapterProductionStatus.CHIEF_FINAL_REVIEW, False, None, "continue", "chief_editor_review"),
        ("reconstruct", ChapterProductionStatus.LORE_FINAL_REVIEW, False, None, "continue", "lore_review"),
        ("reconstruct", ChapterProductionStatus.REVIEW_REVISION, False, None, "continue", "corrective_revision"),
        ("reconstruct", ChapterProductionStatus.REVISION_READY, False, None, "continue", "mark_revision_ready"),
        ("reconstruct", ChapterProductionStatus.ARCHIVE_UPDATE, False, None, "continue", "finalize"),
        ("reconstruct", ChapterProductionStatus.FAILED, False, None, "continue", "reconcile"),
        ("reconstruct", ChapterProductionStatus.COMPLETED, False, None, "complete", "success"),
        ("reconstruct", ChapterProductionStatus.CANCELLED, False, None, "cancelled", None),
    ),
)
async def test_reconstruct_node_lifecycle_parity_for_all_domain_statuses(
    cursor: str,
    status: ChapterProductionStatus,
    awaiting: bool,
    action_id: UUID | None,
    expected_kind: str,
    expected_target: str | None,
) -> None:
    st = domain_state(status, awaiting_user=awaiting, action_request_id=action_id)
    facade = mock_service(st)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor=cursor
    )

    assert result.kind == expected_kind
    if expected_kind == "continue":
        assert result.next_cursor == expected_target
    elif expected_kind == "await-user":
        assert result.action_request_id == action_id
    elif expected_kind == "complete":
        assert result.completion_code == "success"


@pytest.mark.anyio
async def test_draft_node_transitions_to_author_action_awaiting_user() -> None:
    before = domain_state(ChapterProductionStatus.DRAFTING)
    after = domain_state(
        ChapterProductionStatus.AUTHOR_REVISION,
        awaiting_user=True,
        action_request_id=ACTION_ID,
    )
    facade = mock_service(before, after)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="draft"
    )

    assert result.kind == "await-user"
    assert result.action_request_id == ACTION_ID
    facade._schedule_drafting.assert_awaited_once_with(**scope_kwargs())


@pytest.mark.anyio
async def test_await_author_action_node_is_read_only_and_validates_action() -> None:
    st = domain_state(
        ChapterProductionStatus.AUTHOR_REVISION,
        awaiting_user=True,
        action_request_id=ACTION_ID,
        action_kind=ChapterActionKind.AUTHOR_REVISION,
    )
    facade = mock_service(st)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="await_author_action"
    )

    assert result.kind == "await-user"
    assert result.action_request_id == ACTION_ID
    facade.validate_scheduling_action.assert_awaited_once_with(
        **scope_kwargs(),
        action_request_id=ACTION_ID,
        action_kind=ChapterActionKind.AUTHOR_REVISION,
    )
    facade._schedule_drafting.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("after_status", "expected_next_cursor"),
    (
        (ChapterProductionStatus.EDITOR_REVIEW, "editor_review"),
    ),
)
async def test_author_revision_node_accept_routes_to_editor_review(
    after_status: ChapterProductionStatus,
    expected_next_cursor: str,
) -> None:
    st = domain_state(after_status)
    facade = mock_service(st)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="author_revision"
    )

    assert result.kind == "continue"
    assert result.next_cursor == expected_next_cursor


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cursor", "before_status", "after_status", "expected_next_cursor"),
    (
        ("editor_review", ChapterProductionStatus.EDITOR_REVIEW, ChapterProductionStatus.CHIEF_FINAL_REVIEW, "chief_editor_review"),
        ("editor_review", ChapterProductionStatus.EDITOR_REVIEW, ChapterProductionStatus.LORE_FINAL_REVIEW, "lore_review"),
        ("editor_review", ChapterProductionStatus.EDITOR_REVIEW, ChapterProductionStatus.REVIEW_REVISION, "corrective_revision"),
        ("chief_editor_review", ChapterProductionStatus.CHIEF_FINAL_REVIEW, ChapterProductionStatus.LORE_FINAL_REVIEW, "lore_review"),
        ("lore_review", ChapterProductionStatus.LORE_FINAL_REVIEW, ChapterProductionStatus.REVISION_READY, "mark_revision_ready"),
    ),
)
async def test_review_nodes_lifecycle_parity(
    cursor: str,
    before_status: ChapterProductionStatus,
    after_status: ChapterProductionStatus,
    expected_next_cursor: str,
) -> None:
    before = domain_state(before_status)
    after = domain_state(after_status)
    facade = mock_service(before, after)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor=cursor
    )

    assert result.kind == "continue"
    assert result.next_cursor == expected_next_cursor
    facade.execute_current_review.assert_awaited_once_with(**scope_kwargs())


@pytest.mark.anyio
async def test_corrective_revision_node_returns_reconciliation_required() -> None:
    st = domain_state(ChapterProductionStatus.REVIEW_REVISION)
    facade = mock_service(st)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="corrective_revision"
    )

    assert result.kind == "reconciliation-required"
    assert result.failure_code == ChapterFailureCode.RECONCILIATION_REQUIRED.value


@pytest.mark.anyio
async def test_mark_revision_ready_routes_to_finalize() -> None:
    st = domain_state(ChapterProductionStatus.REVISION_READY)
    facade = mock_service(st)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="mark_revision_ready"
    )

    assert result.kind == "continue"
    assert result.next_cursor == "finalize"


@pytest.mark.anyio
async def test_finalize_node_transitions_to_complete() -> None:
    before = domain_state(ChapterProductionStatus.REVISION_READY)
    after = domain_state(ChapterProductionStatus.COMPLETED)
    facade = mock_service(before, after)

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="finalize"
    )

    assert result.kind == "complete"
    assert result.completion_code == "success"
    facade.finalize_without_reader_panel.assert_awaited_once_with(**scope_kwargs())


@pytest.mark.anyio
async def test_reconcile_node_recovers_failed_state_to_review() -> None:
    failed = domain_state(
        ChapterProductionStatus.FAILED,
        failure_code=ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
    )
    recovered = domain_state(ChapterProductionStatus.EDITOR_REVIEW)
    facade = mock_service(failed)
    facade.reconcile_indeterminate.return_value = recovered

    result = await advance_chapter_production(
        facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="reconcile"
    )

    assert result.kind == "continue"
    assert result.next_cursor == "editor_review"
    facade.reconcile_indeterminate.assert_awaited_once_with(**scope_kwargs())


@pytest.mark.anyio
async def test_all_11_nodes_registered_in_graph_ports_and_topology() -> None:
    assert len(NODE_NAMES) == 11
    facade = mock_service(domain_state(ChapterProductionStatus.DRAFTING))
    service = SimpleNamespace(
        _phase_sessions=SimpleNamespace(lease=lambda: _LeaseContext()),
        _new_scheduling_facade=lambda _session: facade,
    )
    ports = build_chapter_production_ports(service, CONTEXT)
    assert set(ports) == set(NODE_NAMES)
    compiled = build_chapter_production_graph(ports)
    assert compiled is not None


# ============================================================================
# 2. Default Cutover: GRAPH_ENABLED = True Sets LangGraph Pin
# ============================================================================


@pytest.mark.anyio
async def test_default_cutover_pins_new_runs_to_langgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    added: list[object] = []
    phase = object.__new__(_InitialRunPhase)
    phase.session = SimpleNamespace(add=added.append, flush=AsyncMock())
    phase.chief_editor_required = True

    run = await phase._create(
        PROJECT_ID, CHAPTER_ID, OUTLINE_ID, OUTLINE_VERSION_ID, HASH, KEY
    )

    expected_pin = chapter_production_langgraph_pin()
    assert run.metadata_["chapter_production_runtime"] == expected_pin
    assert strict_runtime(run.metadata_["chapter_production_runtime"]) == expected_pin


# ============================================================================
# 3. Rollback Verification & Non-Fallback Isolation
# ============================================================================


@pytest.mark.anyio
async def test_rollback_to_false_creates_service_pinned_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    added: list[object] = []
    phase = object.__new__(_InitialRunPhase)
    phase.session = SimpleNamespace(add=added.append, flush=AsyncMock())
    phase.chief_editor_required = True

    run = await phase._create(
        PROJECT_ID, CHAPTER_ID, OUTLINE_ID, OUTLINE_VERSION_ID, HASH, KEY
    )

    expected_pin = chapter_production_runtime_pin()
    assert run.metadata_["chapter_production_runtime"] == expected_pin
    assert strict_runtime(run.metadata_["chapter_production_runtime"]) == expected_pin


@pytest.mark.anyio
async def test_existing_langgraph_pinned_run_dispatches_via_langgraph_when_flag_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    value = mock_lifecycle()
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(return_value=sample_graph_state("draft")),
    )
    graph_invoke = AsyncMock(
        return_value=ChapterProductionSchedulingResult(
            kind="await-user", action_request_id=ACTION_ID
        )
    )
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", graph_invoke)

    result = await value.resume(
        PROJECT_ID, CHAPTER_ID, RUN_ID, actor_user_id=ACTOR_ID
    )

    assert result.workflow_run_id == RUN_ID
    graph_invoke.assert_awaited_once()


@pytest.mark.anyio
async def test_langgraph_pinned_run_failure_never_falls_back_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    value = mock_lifecycle()
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(return_value=sample_graph_state("draft")),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "invoke_chapter_production_graph",
        AsyncMock(side_effect=GraphError()),
    )

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    value._run.assert_not_awaited()


@pytest.mark.anyio
async def test_service_pinned_run_never_invokes_langgraph_even_when_flag_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    value = mock_lifecycle()
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_runtime_pin()),
    )
    graph_invoke = AsyncMock()
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", graph_invoke)

    result = await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert result.workflow_run_id == RUN_ID
    graph_invoke.assert_not_awaited()
    value._run.assert_awaited_once_with(PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)


# ============================================================================
# 4. Ephemeral Graph State Deletion & Reconstruction Parity
# ============================================================================


@pytest.mark.anyio
async def test_inmemory_state_deletion_preserves_postgresql_reconstruction() -> None:
    saver = InMemorySaver()
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=(
            (
                "reconstruct",
                lambda state: {"kind": OutcomeKind.CONTINUE.value, "next_cursor": "draft"},
                lambda outcome: "draft",
            ),
            (
                "draft",
                lambda state: {"kind": OutcomeKind.AWAIT_USER.value, "action_request_id": ACTION_ID},
                lambda outcome: END,
            ),
        ),
    )
    compiled = definition.compile(checkpointer=saver)
    config = build_config(RUN_ID)
    initial_state = sample_graph_state("reconstruct")

    await compiled.ainvoke(initial_state, config=config)

    # Verify checkpointer state exists
    checkpoint_tuple = await saver.aget_tuple(config)
    assert checkpoint_tuple is not None

    # Delete ephemeral state
    await saver.adelete_thread(str(RUN_ID))
    deleted_tuple = await saver.aget_tuple(config)
    assert deleted_tuple is None

    # Verify that parse_graph_state canonicalizes the fresh input from simulated PG
    reconstructed = parse_graph_state(
        {
            "workflow_run_id": RUN_ID,
            "graph_id": GRAPH_ID,
            "graph_version": GRAPH_VERSION,
            "cursor": "draft",
            "workflow_checkpoint_index": 1,
            "invocation_id": uuid4(),
            "attempt_id": None,
            "claim_id": None,
            "action_request_id": ACTION_ID,
            "resume_reason": "action-resolved",
        }
    )
    assert reconstructed["workflow_run_id"] == RUN_ID
    assert reconstructed["cursor"] == "draft"
    assert reconstructed["action_request_id"] == ACTION_ID


# ============================================================================
# 5. Stale Attempt Token ABA & Concurrency Protection
# ============================================================================


def test_stale_attempt_token_fails_validation_closed() -> None:
    expected_meta = pristine_run_metadata(sample_binding())
    stale_meta = dict(expected_meta)
    stale_meta["operation_key"] = "stale-key-" + ("c" * 54)
    run = SimpleNamespace(metadata_=stale_meta)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        _InitialEvidencePhase._attempt(run, expected_meta, KEY)


@pytest.mark.anyio
async def test_stale_action_request_fails_scheduling_validation_closed() -> None:
    st = domain_state(
        ChapterProductionStatus.AUTHOR_REVISION,
        awaiting_user=True,
        action_request_id=ACTION_ID,
    )
    facade = mock_service(st)
    facade.validate_scheduling_action.side_effect = ChapterProductionV2ValidationError()

    with pytest.raises(ChapterProductionV2ValidationError):
        await advance_chapter_production(
            facade, context=CONTEXT, workflow_run_id=RUN_ID, cursor="await_author_action"
        )


# ============================================================================
# 6. Sanitized Exception & Cancellation
# ============================================================================


@pytest.mark.anyio
async def test_node_cancelled_error_is_sanitized_to_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.graph.chapter_production_execution.advance_chapter_production",
        AsyncMock(side_effect=NodeCancelledError("sensitive-subgraph-state-leak")),
    )
    service = SimpleNamespace(
        _phase_sessions=SimpleNamespace(lease=lambda: _LeaseContext()),
        _new_scheduling_facade=lambda _session: SimpleNamespace(),
    )

    with pytest.raises(asyncio.CancelledError) as captured:
        await invoke_chapter_production_graph(
            service, context=CONTEXT, state=sample_graph_state("reconstruct")
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "sensitive" not in repr(captured.value)


@pytest.mark.anyio
async def test_provider_error_with_secret_content_is_sanitized_without_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = mock_lifecycle()
    load = AsyncMock(return_value=chapter_production_langgraph_pin())
    reconstruct = AsyncMock(return_value=sample_graph_state("draft"))
    invoke = AsyncMock(side_effect=RuntimeError("secret-provider-api-key-leak-12345"))

    monkeypatch.setattr(lifecycle_module, "load_chapter_production_runtime", load)
    monkeypatch.setattr(lifecycle_module, "reconstruct_scheduler_input", reconstruct)
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", invoke)

    with pytest.raises(ChapterProductionV2ReconciliationError) as captured:
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "secret" not in repr(captured.value)
    assert "12345" not in repr(captured.value)


# ============================================================================
# 7. Asyncpg UUID Canonicalization
# ============================================================================


@pytest.mark.anyio
async def test_asyncpg_uuid_is_normalized_to_stdlib_uuid_across_boundary() -> None:
    pg_proj = asyncpg.pgproto.pgproto.UUID(PROJECT_ID.hex)
    pg_chap = asyncpg.pgproto.pgproto.UUID(CHAPTER_ID.hex)
    pg_run = asyncpg.pgproto.pgproto.UUID(RUN_ID.hex)
    pg_actor = asyncpg.pgproto.pgproto.UUID(ACTOR_ID.hex)

    ctx = ChapterProductionInvocationContext(pg_proj, pg_chap, pg_actor)  # type: ignore[arg-type]
    assert type(ctx.project_id) is UUID
    assert type(ctx.chapter_id) is UUID
    assert type(ctx.actor_user_id) is UUID
    assert ctx.project_id == PROJECT_ID
    assert ctx.chapter_id == CHAPTER_ID
    assert ctx.actor_user_id == ACTOR_ID

    facade = mock_service(domain_state(ChapterProductionStatus.DRAFTING))
    await advance_chapter_production(
        facade, context=ctx, workflow_run_id=pg_run, cursor="reconstruct"  # type: ignore[arg-type]
    )

    assert facade.load_state.await_args.kwargs["workflow_run_id"] == RUN_ID
    assert type(facade.load_state.await_args.kwargs["workflow_run_id"]) is UUID


def test_hostile_uuid_object_fails_closed_without_leakage() -> None:
    class HostileUuid:
        @property
        def int(self) -> int:
            raise RuntimeError("secret-uuid-exploit")

        def __str__(self) -> str:
            raise RuntimeError("secret-uuid-exploit")

    with pytest.raises(ChapterProductionV2ValidationError) as captured:
        ChapterProductionInvocationContext(HostileUuid(), CHAPTER_ID, ACTOR_ID)  # type: ignore[arg-type]

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "secret" not in repr(captured.value)
