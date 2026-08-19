"""Strict server-owned Chapter Production V2 runtime pin parser."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkflowEvent
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)

SCHEDULER_KIND_SERVICE_V2 = "service_v2"
SCHEDULER_KIND_LANGGRAPH = "langgraph"
_GRAPH_ID = "chapter-production-v2"
_GRAPH_VERSION = "0"
_LANGGRAPH_GRAPH_ID = "chapter-production-langgraph"
_LANGGRAPH_GRAPH_VERSION = "0"
_RUNTIME_KEYS = frozenset({"scheduler_kind", "graph_id", "graph_version"})


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def chapter_production_runtime_pin() -> dict[str, str]:
    return {
        "scheduler_kind": SCHEDULER_KIND_SERVICE_V2,
        "graph_id": _GRAPH_ID,
        "graph_version": _GRAPH_VERSION,
    }


def chapter_production_langgraph_pin() -> dict[str, str]:
    return {
        "scheduler_kind": SCHEDULER_KIND_LANGGRAPH,
        "graph_id": _LANGGRAPH_GRAPH_ID,
        "graph_version": _LANGGRAPH_GRAPH_VERSION,
    }


def strict_runtime(value: object) -> dict[str, str]:
    """Return one of the two exact server-owned runtime namespaces, or fail closed."""
    if type(value) is not dict or not value:
        raise _invalid()
    if set(value) != _RUNTIME_KEYS or any(type(item) is not str for item in value.values()):
        raise _invalid()
    if value not in (chapter_production_runtime_pin(), chapter_production_langgraph_pin()):
        raise _invalid()
    return dict(value)


async def next_event_sequence(
    session: AsyncSession, workflow_run_id: UUID
) -> int:
    """Allocate the next per-run event ordinal under the caller's run lock."""
    in_memory = [
        obj.event_sequence
        for obj in session.new
        if isinstance(obj, WorkflowEvent)
        and obj.workflow_run_id == workflow_run_id
        and obj.event_sequence is not None
    ]
    latest = await session.scalar(
        select(func.max(WorkflowEvent.event_sequence)).where(
            WorkflowEvent.workflow_run_id == workflow_run_id
        )
    )
    current_max = max([latest or 0, *in_memory])
    return current_max + 1


__all__ = [
    "SCHEDULER_KIND_LANGGRAPH",
    "SCHEDULER_KIND_SERVICE_V2",
    "chapter_production_langgraph_pin",
    "chapter_production_runtime_pin",
    "next_event_sequence",
    "strict_runtime",
]
