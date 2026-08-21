"""Frozen Chapter Production V2 LangGraph topology and legal conditional edges."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END

from app.graph.contracts import GRAPH_ID, GRAPH_VERSION, GraphError, OutcomeKind, parse_graph_outcome
from app.graph.runtime import GraphDefinition, NODE_NAMES, NodePort

GRAPH_ENABLED = True

BUSINESS_NODES = frozenset(NODE_NAMES - {"reconstruct"})

# Legal targets for a node's `continue` outcome. `await-user` and terminal
# outcomes stop the graph; a fresh invocation reconstructs from PostgreSQL and
# re-enters through `reconstruct`, so they need no edge here.
LEGAL_EDGES: Mapping[str, frozenset[str]] = {
    "reconstruct": frozenset(BUSINESS_NODES),
    "draft": frozenset({"await_author_action"}),
    "await_author_action": frozenset({"draft", "author_revision", "editor_review"}),
    "author_revision": frozenset({"editor_review"}),
    "editor_review": frozenset({"chief_editor_review", "lore_review"}),
    "chief_editor_review": frozenset({"lore_review"}),
    "lore_review": frozenset({"mark_revision_ready"}),
    "corrective_revision": frozenset({"editor_review"}),
    "mark_revision_ready": frozenset({"finalize"}),
    "finalize": frozenset(),
    "reconcile": frozenset(BUSINESS_NODES),
}


def route_for_outcome(outcome: object, from_node: str) -> str:
    """Return the only legal target for a closed typed outcome, or fail closed."""
    if type(from_node) is not str or from_node not in LEGAL_EDGES:
        raise GraphError()
    parsed = parse_graph_outcome(outcome)
    if parsed["kind"] != OutcomeKind.CONTINUE.value:
        return END
    target = parsed["next_cursor"]
    if target not in LEGAL_EDGES[from_node] or target not in NODE_NAMES:
        raise GraphError()
    return target


def build_chapter_production_graph(
    ports: Mapping[str, NodePort],
    *,
    checkpointer: Any = None,
) -> Any:
    """Compile the exact 11-node server-owned topology with checked routing."""
    if (
        any(type(key) is not str for key in ports)
        or set(ports) != set(NODE_NAMES)
        or any(not callable(port) for port in ports.values())
    ):
        raise GraphError()
    ordered = ("reconstruct", *sorted(BUSINESS_NODES))
    nodes = tuple(
        (
            name,
            ports[name],
            lambda outcome, current=name: route_for_outcome(outcome, current),
        )
        for name in ordered
    )
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=nodes,
    )
    return definition.compile(checkpointer=checkpointer)


__all__ = [
    "BUSINESS_NODES",
    "GRAPH_ENABLED",
    "LEGAL_EDGES",
    "build_chapter_production_graph",
    "route_for_outcome",
]
