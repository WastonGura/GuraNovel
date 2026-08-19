from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.types import Command

from app.graph.contracts import (
    GRAPH_ID,
    GRAPH_VERSION,
    CompletionCode,
    Cursor,
    GraphError,
    OutcomeKind,
    parse_graph_outcome,
    parse_graph_state,
    sanitize_checkpoint_payload,
    sanitize_config,
    sanitize_metadata,
)
from app.graph.runtime import (
    GraphDefinition,
    build_config,
    checked_node,
    fake_reconstruct_port,
    observability_projection,
)


def _uuid() -> UUID:
    return uuid4()


def _valid_state() -> dict[str, object]:
    return {
        "workflow_run_id": _uuid(),
        "graph_id": GRAPH_ID,
        "graph_version": GRAPH_VERSION,
        "cursor": "draft",
        "workflow_checkpoint_index": 0,
        "invocation_id": _uuid(),
        "attempt_id": None,
        "claim_id": None,
        "action_request_id": None,
        "resume_reason": "new",
    }


class _PgProtoUuid:
    """Minimal asyncpg.pgproto.UUID look-alike: same repr/int/str contract."""

    def __init__(self, value: UUID) -> None:
        self._value = value
        self.int = value.int

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        return repr(self._value)


def test_graph_state_exact_allowlist_parses_and_canonicalizes() -> None:
    state = parse_graph_state(_valid_state())

    assert set(state) == {
        "workflow_run_id",
        "graph_id",
        "graph_version",
        "cursor",
        "workflow_checkpoint_index",
        "invocation_id",
        "attempt_id",
        "claim_id",
        "action_request_id",
        "resume_reason",
    }
    assert type(state["workflow_run_id"]) is UUID
    assert state["graph_id"] == GRAPH_ID
    assert state["graph_version"] == GRAPH_VERSION
    assert state["cursor"] == "draft"
    assert state["workflow_checkpoint_index"] == 0
    assert type(state["invocation_id"]) is UUID
    assert state["attempt_id"] is None
    assert state["resume_reason"] == "new"


def test_graph_state_accepts_canonical_uuid_like_values_and_normalizes() -> None:
    run_id = _uuid()
    payload = _valid_state()
    payload["workflow_run_id"] = _PgProtoUuid(run_id)

    state = parse_graph_state(payload)

    assert type(state["workflow_run_id"]) is UUID
    assert state["workflow_run_id"] == run_id


@pytest.mark.parametrize(
    "mutator",
    (
        lambda s: s.pop("cursor"),
        lambda s: s.update({"prose": "leaked"}),
        lambda s: s.update({"tags": ["x"]}),
        lambda s: s.update({"cursor": None}),
        lambda s: s.update({"cursor": "unknown"}),
        lambda s: s.update({"workflow_checkpoint_index": True}),
        lambda s: s.update({"workflow_checkpoint_index": -1}),
        lambda s: s.update({"workflow_checkpoint_index": 2**31}),
        lambda s: s.update({"resume_reason": "client-resume"}),
        lambda s: s.update({"workflow_run_id": "not-a-uuid"}),
        lambda s: s.update({"attempt_id": {"id": _uuid()}}),
        lambda s: s.update({"graph_id": "client-selected"}),
        lambda s: s.update({"graph_version": "2"}),
    ),
)
def test_graph_state_rejects_unknown_or_invalid_fields(mutator: object) -> None:
    payload = _valid_state()
    mutator(payload)  # type: ignore[operator]
    with pytest.raises(GraphError):
        parse_graph_state(payload)


def test_graph_state_rejects_non_dict_and_dict_subclass_bypass() -> None:
    with pytest.raises(GraphError):
        parse_graph_state(list(_valid_state().items()))
    with pytest.raises(GraphError):
        parse_graph_state(type("State", (dict,), {})(_valid_state()))


def test_graph_outcome_accepts_every_closed_variant() -> None:
    action_id = _uuid()
    assert parse_graph_outcome({"kind": "continue", "next_cursor": "editor_review"}) == {
        "kind": "continue",
        "next_cursor": "editor_review",
    }
    assert parse_graph_outcome({"kind": "await-user", "action_request_id": action_id}) == {
        "kind": "await-user",
        "action_request_id": action_id,
    }
    assert parse_graph_outcome(
        {"kind": "retryable-failure", "failure_code": "provider_unavailable"}
    ) == {"kind": "retryable-failure", "failure_code": "provider_unavailable"}
    assert parse_graph_outcome(
        {"kind": "reconciliation-required", "failure_code": "reconciliation_required"}
    ) == {"kind": "reconciliation-required", "failure_code": "reconciliation_required"}
    assert parse_graph_outcome({"kind": "cancelled"}) == {"kind": "cancelled"}
    assert parse_graph_outcome({"kind": "complete", "completion_code": "success"}) == {
        "kind": "complete",
        "completion_code": "success",
    }


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "unknown"},
        {"kind": "continue"},
        {"kind": "continue", "next_cursor": "draft", "extra": 1},
        {"kind": "await-user"},
        {"kind": "complete", "completion_code": "success", "prose": "x"},
        {"kind": "retryable-failure", "failure_code": "raw-error"},
        [],
        None,
    ),
)
def test_graph_outcome_rejects_unknown_or_extra_fields(payload: object) -> None:
    with pytest.raises(GraphError):
        parse_graph_outcome(payload)


def test_config_sanitizer_accepts_only_server_built_shape() -> None:
    run_id = _uuid()
    config = sanitize_config(
        {"configurable": {"thread_id": run_id}, "recursion_limit": 25}
    )

    assert config == {
        "configurable": {"thread_id": str(run_id)},
        "recursion_limit": 25,
    }


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"configurable": {"thread_id": str(_uuid())}},
        {"recursion_limit": 25},
        {"configurable": {"thread_id": str(_uuid()), "extra": 1}, "recursion_limit": 25},
        {"configurable": {"thread_id": str(_uuid())}, "recursion_limit": True},
        {"configurable": {"thread_id": str(_uuid())}, "recursion_limit": 0},
        {"configurable": {"thread_id": str(_uuid())}, "recursion_limit": 1001},
        {"configurable": {"thread_id": str(_uuid())}, "recursion_limit": 25, "tags": ["x"]},
        {"configurable": {"thread_id": str(_uuid())}, "recursion_limit": 25, "callbacks": [object]},
        {"configurable": {"thread_id": "not-a-uuid"}, "recursion_limit": 25},
        [],
    ),
)
def test_config_sanitizer_rejects_unknown_nested_or_unbounded_fields(
    payload: object,
) -> None:
    with pytest.raises(GraphError):
        sanitize_config(payload)


def test_metadata_sanitizer_accepts_only_safe_subset() -> None:
    run_id = _uuid()
    invocation_id = _uuid()
    metadata = sanitize_metadata(
        {
            "workflow_run_id": run_id,
            "graph_id": GRAPH_ID,
            "graph_version": GRAPH_VERSION,
            "cursor": "draft",
            "workflow_checkpoint_index": 3,
            "invocation_id": invocation_id,
        }
    )

    assert metadata == {
        "workflow_run_id": run_id,
        "graph_id": GRAPH_ID,
        "graph_version": GRAPH_VERSION,
        "cursor": "draft",
        "workflow_checkpoint_index": 3,
        "invocation_id": invocation_id,
    }
    with pytest.raises(GraphError):
        sanitize_metadata({**metadata, "tags": ["hidden"]})


def test_checkpoint_payload_sanitizer_is_exact_state_allowlist() -> None:
    payload = _valid_state()
    assert sanitize_checkpoint_payload(payload) == parse_graph_state(payload)
    with pytest.raises(GraphError):
        sanitize_checkpoint_payload({**payload, "endpoint": "/db"})


def test_checked_node_validates_state_and_outcome_and_returns_command() -> None:
    def port(state: dict[str, object]) -> dict[str, object]:
        assert state["cursor"] == "draft"
        return {"kind": "continue", "next_cursor": "editor_review"}

    wrapper = checked_node(
        port, lambda outcome: "review", allowed_targets=frozenset({"review"})
    )

    result = wrapper(_valid_state())
    assert isinstance(result, Command)
    assert result.goto == "review"
    assert result.update == {"cursor": "editor_review"}

    with pytest.raises(GraphError):
        wrapper({**_valid_state(), "prose": "x"})

    def bad_port(_: dict[str, object]) -> dict[str, object]:
        return {"kind": "unknown"}

    with pytest.raises(GraphError):
        checked_node(
            bad_port, lambda outcome: "review", allowed_targets=frozenset({"review"})
        )(_valid_state())


def test_checked_graph_rejects_extra_state_before_checkpoint_persistence() -> None:
    run_id = _uuid()
    saver = InMemorySaver()
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=(("reconstruct", fake_reconstruct_port, lambda outcome: END),),
    )
    compiled = definition.compile(checkpointer=saver)
    state = _valid_state()
    state["workflow_run_id"] = run_id
    state["invocation_id"] = _uuid()
    state["cursor"] = "complete"

    with pytest.raises(GraphError):
        compiled.invoke({**state, "prose": "leak"}, config=build_config(run_id))

    assert saver.get_tuple({"configurable": {"thread_id": str(run_id)}}) is None


def test_checked_graph_rejects_unknown_config_at_invoke_boundary() -> None:
    run_id = _uuid()
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=(("reconstruct", fake_reconstruct_port, lambda outcome: END),),
    )
    compiled = definition.compile()
    state = _valid_state()
    state["workflow_run_id"] = run_id
    state["invocation_id"] = _uuid()
    state["cursor"] = "complete"

    with pytest.raises(GraphError):
        compiled.invoke(
            state,
            config={
                "configurable": {"thread_id": str(run_id), "tags": ["x"]},
                "recursion_limit": 25,
            },
        )


def test_graph_definition_rejects_unknown_node_name() -> None:
    with pytest.raises(GraphError):
        GraphDefinition(
            graph_id=GRAPH_ID,
            graph_version=GRAPH_VERSION,
            nodes=(("not-a-node", fake_reconstruct_port, lambda outcome: END),),
        ).compile()


def test_checked_node_rejects_unknown_route_target() -> None:
    def port(_: dict[str, object]) -> dict[str, object]:
        return {"kind": "complete", "completion_code": "success"}

    wrapper = checked_node(
        port,
        lambda outcome: "unknown-target",
        allowed_targets=frozenset({"reconstruct", END}),
    )

    with pytest.raises(GraphError):
        wrapper(_valid_state())


def test_graph_outcome_kind_is_canonical_plain_str() -> None:
    outcome = parse_graph_outcome(
        {
            "kind": OutcomeKind.COMPLETE,
            "completion_code": CompletionCode.SUCCESS,
        }
    )

    assert type(outcome["kind"]) is str
    assert type(outcome["completion_code"]) is str


def test_observability_projection_validates_raw_inputs() -> None:
    with pytest.raises(GraphError):
        observability_projection(
            state={**_valid_state(), "prose": "leak"},
            node_name="reconstruct",
            outcome=None,
        )
    with pytest.raises(GraphError):
        observability_projection(
            state=_valid_state(),
            node_name="reconstruct",
            outcome={"kind": "retryable-failure", "failure_code": "arbitrary"},
        )


def test_fake_reconstruct_port_returns_cancelled_for_cancelled_cursor() -> None:
    state = parse_graph_state({**_valid_state(), "cursor": Cursor.CANCELLED.value})

    assert fake_reconstruct_port(state) == {"kind": "cancelled"}


def test_graph_definition_compiles_and_invokes_with_memory_checkpointer() -> None:
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=(("reconstruct", fake_reconstruct_port, lambda outcome: END),),
    )
    compiled = definition.compile()
    run_id = _uuid()
    state = _valid_state()
    state["workflow_run_id"] = run_id
    state["invocation_id"] = _uuid()
    state["cursor"] = "complete"

    result = compiled.invoke(
        state,
        config=build_config(run_id),
    )

    assert result["graph_id"] == GRAPH_ID
    assert result["workflow_run_id"] == run_id
    assert result["cursor"] == "complete"


def test_deleting_graph_state_permits_reconstruction_from_business_truth() -> None:
    business_truth = parse_graph_state(_valid_state())
    run_id = business_truth["workflow_run_id"]
    saver = InMemorySaver()
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=(("reconstruct", fake_reconstruct_port, lambda outcome: END),),
    )
    compiled = definition.compile(checkpointer=saver)
    compiled.invoke(business_truth, config=build_config(run_id))
    saver.delete_thread(str(run_id))

    rebuilt = parse_graph_state(business_truth)

    assert rebuilt == business_truth


def test_build_config_uses_thread_id_equal_to_workflow_run_id() -> None:
    run_id = _uuid()
    config = build_config(run_id)

    assert config["configurable"]["thread_id"] == str(run_id)


def test_observability_projection_is_closed_and_content_free() -> None:
    projection = observability_projection(
        state=parse_graph_state(_valid_state()),
        node_name="reconstruct",
        outcome=parse_graph_outcome(
            {"kind": "retryable-failure", "failure_code": "provider_unavailable"}
        ),
        duration_ms=12,
    )

    assert set(projection) <= {
        "graph_id",
        "graph_version",
        "workflow_run_id",
        "node_name",
        "invocation_id",
        "attempt_id",
        "claim_id",
        "action_request_id",
        "outcome_code",
        "failure_code",
        "duration_ms",
    }
    assert projection["node_name"] == "reconstruct"
    assert projection["outcome_code"] == "retryable-failure"
    assert projection["failure_code"] == "provider_unavailable"
    assert projection["duration_ms"] == 12
    assert "prose" not in projection
