from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.chapter_production_runtime import (
    SCHEDULER_KIND_LANGGRAPH,
    SCHEDULER_KIND_SERVICE_V2,
    chapter_production_langgraph_pin,
    chapter_production_runtime_pin,
    initial_runtime_marker,
    load_chapter_production_runtime,
    persisted_runtime_pin,
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


def test_langgraph_pin_is_exact_server_owned_and_accepted() -> None:
    pin = chapter_production_langgraph_pin()

    assert pin == {
        "scheduler_kind": SCHEDULER_KIND_LANGGRAPH,
        "graph_id": "chapter-production-langgraph",
        "graph_version": "0",
    }
    assert strict_runtime(pin) == pin


def test_langgraph_pin_constants_match_app_graph_contracts() -> None:
    from app.graph.contracts import GRAPH_ID, GRAPH_VERSION

    assert chapter_production_langgraph_pin() == {
        "scheduler_kind": "langgraph",
        "graph_id": GRAPH_ID,
        "graph_version": GRAPH_VERSION,
    }


def test_historical_absence_is_only_a_marker_and_strict_pin_still_fails() -> None:
    metadata = {"historical": "evidence-must-validate-this-shape"}

    assert initial_runtime_marker(metadata) is None
    with pytest.raises(ChapterProductionV2ValidationError):
        persisted_runtime_pin(metadata)

    metadata["chapter_production_runtime"] = {"scheduler_kind": "service_v2"}
    with pytest.raises(ChapterProductionV2ValidationError):
        initial_runtime_marker(metadata)


@pytest.mark.anyio
async def test_persisted_runtime_loader_is_exact_and_fails_closed() -> None:
    pin = chapter_production_langgraph_pin()
    session = SimpleNamespace(scalar=AsyncMock(return_value={"chapter_production_runtime": pin}))

    assert await load_chapter_production_runtime(session, uuid4()) == pin

    session.scalar.return_value = {
        "chapter_production_runtime": {**pin, "graph_version": "missing"}
    }
    with pytest.raises(ChapterProductionV2ValidationError):
        await load_chapter_production_runtime(session, uuid4())


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


def test_str_subclass_keys_fail_closed_and_result_uses_plain_str_keys() -> None:
    class WeirdKey(str):
        pass

    payload = {
        WeirdKey("scheduler_kind"): "service_v2",
        WeirdKey("graph_id"): "chapter-production-v2",
        WeirdKey("graph_version"): "0",
    }
    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime(payload)

    canonical = strict_runtime(chapter_production_runtime_pin())
    assert all(type(key) is str for key in canonical)


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

