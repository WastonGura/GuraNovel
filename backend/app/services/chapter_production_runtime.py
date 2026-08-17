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
SCHEDULER_KIND_SERVICE_V2_LEGACY = "service_v2_legacy"
SCHEDULER_KIND_LEGACY = "legacy"
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


def strict_runtime(value: object) -> dict[str, str] | None:
    """Return the exact runtime namespace, or None for legacy absence."""
    if type(value) is not dict:
        raise _invalid()
    if not value:
        return None
    if set(value) != _RUNTIME_KEYS or any(type(item) is not str for item in value.values()):
        raise _invalid()
    scheduler_kind = value["scheduler_kind"]
    if scheduler_kind != SCHEDULER_KIND_SERVICE_V2:
        raise _invalid()
    if value["graph_id"] != _GRAPH_ID or value["graph_version"] != _GRAPH_VERSION:
        raise _invalid()
    return dict(value)


def classify_runtime(metadata: object) -> str:
    """Classify one run's runtime without rewriting its metadata."""
    if type(metadata) is dict and "chapter_production_runtime" in metadata:
        runtime = strict_runtime(metadata["chapter_production_runtime"])
        assert runtime is not None
        return runtime["scheduler_kind"]
    return SCHEDULER_KIND_LEGACY


async def next_event_sequence(
    session: AsyncSession, workflow_run_id: UUID
) -> int:
    """Allocate the next per-run event ordinal under the caller's run lock."""
    latest = await session.scalar(
        select(func.max(WorkflowEvent.event_sequence))
        .where(WorkflowEvent.workflow_run_id == workflow_run_id)
        .with_for_update()
    )
    return (latest or 0) + 1


__all__ = [
    "SCHEDULER_KIND_LEGACY",
    "SCHEDULER_KIND_SERVICE_V2",
    "SCHEDULER_KIND_SERVICE_V2_LEGACY",
    "chapter_production_runtime_pin",
    "classify_runtime",
    "next_event_sequence",
    "strict_runtime",
]
