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
_GRAPH_ID = "chapter-production-v2"
_GRAPH_VERSION = "0"
_RUNTIME_KEYS = frozenset({"scheduler_kind", "graph_id", "graph_version"})


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def chapter_production_runtime_pin() -> dict[str, str]:
    return {
        "scheduler_kind": SCHEDULER_KIND_SERVICE_V2,
        "graph_id": _GRAPH_ID,
        "graph_version": _GRAPH_VERSION,
    }


def strict_runtime(value: object) -> dict[str, str]:
    """Return the exact runtime namespace, or fail closed."""
    if type(value) is not dict or not value:
        raise _invalid()
    if set(value) != _RUNTIME_KEYS or any(type(item) is not str for item in value.values()):
        raise _invalid()
    if value["scheduler_kind"] != SCHEDULER_KIND_SERVICE_V2:
        raise _invalid()
    if value["graph_id"] != _GRAPH_ID or value["graph_version"] != _GRAPH_VERSION:
        raise _invalid()
    return dict(value)


async def next_event_sequence(
    session: AsyncSession, workflow_run_id: UUID
) -> int:
    """Allocate the next per-run event ordinal under the caller's run lock."""
    latest = await session.scalar(
        select(func.max(WorkflowEvent.event_sequence)).where(
            WorkflowEvent.workflow_run_id == workflow_run_id
        )
    )
    return (latest or 0) + 1


__all__ = [
    "SCHEDULER_KIND_SERVICE_V2",
    "chapter_production_runtime_pin",
    "next_event_sequence",
    "strict_runtime",
]
