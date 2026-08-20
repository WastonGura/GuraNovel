from __future__ import annotations

import pytest
from langgraph.graph import END

from app.graph.chapter_production_topology import (
    BUSINESS_NODES,
    GRAPH_ENABLED,
    LEGAL_EDGES,
    build_chapter_production_graph,
    route_for_outcome,
)
from app.graph.contracts import GraphError
from app.graph.runtime import NODE_NAMES


def test_server_owned_graph_switch_defaults_off() -> None:
    assert GRAPH_ENABLED is False


def test_topology_node_set_is_exactly_the_frozen_eleven_nodes() -> None:
    assert set(LEGAL_EDGES) == set(NODE_NAMES)
    assert BUSINESS_NODES == NODE_NAMES - {"reconstruct"}


def test_legal_continue_edges_are_exact() -> None:
    assert LEGAL_EDGES == {
        "reconstruct": frozenset(BUSINESS_NODES),
        "draft": frozenset({"await_author_action"}),
        "await_author_action": frozenset({"draft", "author_revision", "editor_review"}),
        "author_revision": frozenset({"editor_review"}),
        "editor_review": frozenset(
            {"chief_editor_review", "lore_review", "corrective_revision"}
        ),
        "chief_editor_review": frozenset({"lore_review", "corrective_revision"}),
        "lore_review": frozenset({"mark_revision_ready", "corrective_revision"}),
        "corrective_revision": frozenset({"editor_review"}),
        "mark_revision_ready": frozenset({"finalize"}),
        "finalize": frozenset(),
        "reconcile": frozenset(BUSINESS_NODES),
    }


def test_route_for_outcome_accepts_only_legal_continue_edges() -> None:
    assert (
        route_for_outcome(
            {"kind": "continue", "next_cursor": "editor_review"}, "author_revision"
        )
        == "editor_review"
    )
    with pytest.raises(GraphError):
        route_for_outcome(
            {"kind": "continue", "next_cursor": "draft"}, "finalize"
        )
    with pytest.raises(GraphError):
        route_for_outcome(
            {"kind": "continue", "next_cursor": "unknown"}, "reconstruct"
        )


@pytest.mark.parametrize(
    "outcome",
    (
        {"kind": "await-user", "action_request_id": "11111111-1111-1111-1111-111111111111"},
        {"kind": "retryable-failure", "failure_code": "provider_unavailable"},
        {"kind": "reconciliation-required", "failure_code": "reconciliation_required"},
        {"kind": "cancelled"},
        {"kind": "complete", "completion_code": "success"},
    ),
)
def test_terminal_outcomes_route_to_end(outcome: dict[str, object]) -> None:
    assert route_for_outcome(outcome, "finalize") == END


def test_build_chapter_production_graph_compiles_all_frozen_nodes() -> None:
    def fake(state: object) -> dict[str, str]:
        return {"kind": "complete", "completion_code": "success"}

    ports = {name: fake for name in NODE_NAMES}

    compiled = build_chapter_production_graph(ports)

    assert compiled is not None


def test_build_chapter_production_graph_rejects_non_callable_ports() -> None:
    ports = {name: (lambda state: None) for name in NODE_NAMES}
    ports["reconstruct"] = object()  # type: ignore[dict-item]

    with pytest.raises(GraphError):
        build_chapter_production_graph(ports)
