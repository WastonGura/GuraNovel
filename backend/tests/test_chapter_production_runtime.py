from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.chapter_production_runtime import (
    SCHEDULER_KIND_SERVICE_V2,
    chapter_production_runtime_pin,
    strict_runtime,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)

MODULE = (
    Path(__file__).resolve().parents[1]
    / "app/services/chapter_production_runtime.py"
)


def test_pin_is_exact_and_server_owned() -> None:
    pin = chapter_production_runtime_pin()

    assert pin == {
        "scheduler_kind": SCHEDULER_KIND_SERVICE_V2,
        "graph_id": "chapter-production-v2",
        "graph_version": "0",
    }
    assert strict_runtime(pin) == pin


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"scheduler_kind": "service_v2", "graph_id": "g", "graph_version": "0", "extra": 1},
        {"scheduler_kind": "service_v2", "graph_id": "g"},
        {"scheduler_kind": "service_v2", "graph_id": "g", "graph_version": None},
        {"scheduler_kind": "client_selected", "graph_id": "g", "graph_version": "0"},
        {"scheduler_kind": "service_v2_legacy", "graph_id": "g", "graph_version": "0"},
        [],
    ),
)
def test_malformed_or_noncanonical_runtime_fails_closed(payload: object) -> None:
    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime(payload)


def test_allocator_sql_has_no_aggregate_for_update() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "next_event_sequence"
    )

    assert "with_for_update" not in ast.get_source_segment(source, function)


@pytest.mark.anyio
async def test_next_event_sequence_considers_db_and_unflushed_events() -> None:
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4
    from app.models import WorkflowEvent
    from app.services.chapter_production_runtime import next_event_sequence

    run_id = uuid4()
    session = MagicMock()
    session.scalar = AsyncMock(return_value=3)
    session.new = {
        WorkflowEvent(workflow_run_id=run_id, event_sequence=4, event_type="t1"),
        WorkflowEvent(workflow_run_id=run_id, event_sequence=5, event_type="t2"),
        WorkflowEvent(workflow_run_id=uuid4(), event_sequence=99, event_type="other"),
    }

    seq = await next_event_sequence(session, run_id)
    assert seq == 6

