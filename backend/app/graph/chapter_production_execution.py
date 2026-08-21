"""Thin async node adapters for the pinned Chapter Production graph."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

from langgraph.errors import NodeCancelledError

from app.graph.chapter_production_topology import build_chapter_production_graph
from app.graph.contracts import GraphError, GraphState, parse_graph_outcome
from app.graph.runtime import GraphOutcome, NODE_NAMES, NodePort
from app.services.chapter_production_graph_domain import (
    ChapterProductionInvocationContext,
    ChapterProductionSchedulingResult,
    advance_chapter_production,
)


def _outcome(result: ChapterProductionSchedulingResult) -> GraphOutcome:
    payload: GraphOutcome = {"kind": result.kind}
    for field in (
        "next_cursor",
        "action_request_id",
        "failure_code",
        "completion_code",
    ):
        value = getattr(result, field)
        if value is not None:
            payload[field] = value
    return parse_graph_outcome(payload)


def _raise_cancelled() -> None:
    error = asyncio.CancelledError()
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


def _ports(
    service: object,
    context: ChapterProductionInvocationContext,
    record: Callable[[ChapterProductionSchedulingResult], None] | None,
) -> Mapping[str, NodePort]:
    def adapter(cursor: str) -> NodePort:
        async def invoke(state: GraphState) -> GraphOutcome:
            async with service._phase_sessions.lease() as session:  # type: ignore[attr-defined]
                facade = service._new_scheduling_facade(session)  # type: ignore[attr-defined]
                result = await advance_chapter_production(
                    facade,
                    context=context,
                    workflow_run_id=state["workflow_run_id"],
                    cursor=cursor,
                )
            if record is not None:
                record(result)
            return _outcome(result)

        return invoke

    return {name: adapter(name) for name in NODE_NAMES}


def build_chapter_production_ports(
    service: object, context: ChapterProductionInvocationContext
) -> Mapping[str, NodePort]:
    """Bind all eleven content-free nodes to one trusted server context."""

    if type(context) is not ChapterProductionInvocationContext:
        raise GraphError() from None
    return _ports(service, context, None)


async def invoke_chapter_production_graph(
    service: object,
    *,
    context: ChapterProductionInvocationContext,
    state: GraphState,
) -> ChapterProductionSchedulingResult:
    """Compile and invoke one request-scoped graph, returning its closed outcome."""

    results: list[ChapterProductionSchedulingResult] = []
    graph = build_chapter_production_graph(_ports(service, context, results.append))
    try:
        await graph.ainvoke(state)
    except NodeCancelledError:
        _raise_cancelled()
    if not results:
        raise GraphError() from None
    return results[-1]


__all__ = ["build_chapter_production_ports", "invoke_chapter_production_graph"]
