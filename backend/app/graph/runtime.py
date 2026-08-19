"""Server-owned versioned LangGraph composition with checked node ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.graph.contracts import (
    GRAPH_ID,
    GRAPH_VERSION,
    Cursor,
    GraphError,
    GraphState,
    OutcomeKind,
    parse_graph_outcome,
    parse_graph_state,
    sanitize_config,
)

GraphOutcome = dict[str, Any]

NODE_NAMES = frozenset(
    {
        "reconstruct",
        "draft",
        "await_author_action",
        "author_revision",
        "editor_review",
        "chief_editor_review",
        "lore_review",
        "corrective_revision",
        "mark_revision_ready",
        "finalize",
        "reconcile",
    }
)

_CURSOR_ORDER = tuple(cursor.value for cursor in Cursor)


class NodePort(Protocol):
    """A node port receives typed state and returns a closed typed outcome."""

    def __call__(self, state: GraphState) -> GraphOutcome: ...


def checked_node(
    port: NodePort,
    route: Callable[[GraphOutcome], str],
    *,
    allowed_targets: frozenset[str],
) -> Callable[[GraphState], Command]:
    """Wrap a node port so state and outcome are strictly validated before routing."""

    def wrapper(state: GraphState) -> Command:
        parsed_state = parse_graph_state(state)
        outcome = parse_graph_outcome(port(parsed_state))
        goto = route(outcome)
        if goto not in allowed_targets:
            raise GraphError()
        update = (
            {"cursor": outcome["next_cursor"]}
            if outcome["kind"] == OutcomeKind.CONTINUE.value
            else {}
        )
        return Command(goto=goto, update=update)

    return wrapper


class CheckedGraph:
    """Compile result that sanitizes state/config at the invoke boundary."""

    def __init__(self, compiled: CompiledStateGraph) -> None:
        self._compiled = compiled

    def _prepare(self, state: object, config: object | None) -> tuple[GraphState, dict[str, Any]]:
        parsed_state = parse_graph_state(state)
        parsed_config = (
            sanitize_config(config)
            if config is not None
            else build_config(parsed_state["workflow_run_id"])
        )
        return parsed_state, parsed_config

    def invoke(self, state: object, config: object | None = None, **kwargs: Any) -> GraphState:
        parsed_state, parsed_config = self._prepare(state, config)
        result = self._compiled.invoke(parsed_state, config=parsed_config, **kwargs)
        return parse_graph_state(result)

    async def ainvoke(
        self, state: object, config: object | None = None, **kwargs: Any
    ) -> GraphState:
        parsed_state, parsed_config = self._prepare(state, config)
        result = await self._compiled.ainvoke(parsed_state, config=parsed_config, **kwargs)
        return parse_graph_state(result)


@dataclass(frozen=True, slots=True)
class GraphDefinition:
    """A server-owned, immutable graph identity/version with checked nodes."""

    graph_id: str
    graph_version: str
    nodes: tuple[tuple[str, NodePort, Callable[[GraphOutcome], str]], ...]

    def compile(self, checkpointer: Any = None) -> CheckedGraph:
        if self.graph_id != GRAPH_ID or self.graph_version != GRAPH_VERSION or not self.nodes:
            raise GraphError()
        names: list[str] = []
        for name, _, _ in self.nodes:
            if type(name) is not str or name not in NODE_NAMES or name in names:
                raise GraphError()
            names.append(name)
        allowed_targets = frozenset(names) | {END}
        builder = StateGraph(GraphState)
        for name, port, route in self.nodes:
            builder.add_node(
                name, checked_node(port, route, allowed_targets=allowed_targets)
            )
        builder.add_edge(START, self.nodes[0][0])
        return CheckedGraph(builder.compile(checkpointer=checkpointer))


def build_config(workflow_run_id: UUID, recursion_limit: int = 25) -> dict[str, Any]:
    """Build the only permitted server-owned LangGraph invocation config."""
    return sanitize_config(
        {
            "configurable": {"thread_id": workflow_run_id},
            "recursion_limit": recursion_limit,
        }
    )


def fake_reconstruct_port(state: GraphState) -> GraphOutcome:
    """Deterministic, content-free fake reconstruction node for tests and tooling."""
    if state["cursor"] == Cursor.COMPLETE.value:
        return {"kind": OutcomeKind.COMPLETE.value, "completion_code": "success"}
    if state["cursor"] == Cursor.CANCELLED.value:
        return {"kind": OutcomeKind.CANCELLED.value}
    if state["cursor"] == Cursor.RECONCILE.value:
        return {
            "kind": OutcomeKind.RECONCILIATION_REQUIRED.value,
            "failure_code": "reconciliation_required",
        }
    index = _CURSOR_ORDER.index(state["cursor"])
    next_cursor = _CURSOR_ORDER[min(index + 1, len(_CURSOR_ORDER) - 1)]
    return {"kind": OutcomeKind.CONTINUE.value, "next_cursor": next_cursor}


def observability_projection(
    *,
    state: GraphState,
    node_name: str | None = None,
    outcome: GraphOutcome | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Project only the closed observability allowlist from validated values."""
    parsed_state = parse_graph_state(state)
    parsed_outcome = parse_graph_outcome(outcome) if outcome is not None else None
    if node_name is not None and (type(node_name) is not str or not node_name):
        raise GraphError()
    if duration_ms is not None and (
        type(duration_ms) is not int
        or isinstance(duration_ms, bool)
        or not 0 <= duration_ms <= 86_400_000
    ):
        raise GraphError()
    projection: dict[str, Any] = {
        "graph_id": parsed_state["graph_id"],
        "graph_version": parsed_state["graph_version"],
        "workflow_run_id": parsed_state["workflow_run_id"],
        "node_name": node_name,
        "invocation_id": parsed_state["invocation_id"],
        "attempt_id": parsed_state["attempt_id"],
        "claim_id": parsed_state["claim_id"],
        "action_request_id": parsed_state["action_request_id"],
    }
    if parsed_outcome is not None:
        projection["outcome_code"] = parsed_outcome["kind"]
        if "failure_code" in parsed_outcome:
            projection["failure_code"] = parsed_outcome["failure_code"]
    if duration_ms is not None:
        projection["duration_ms"] = duration_ms
    return {key: value for key, value in projection.items() if value is not None}
