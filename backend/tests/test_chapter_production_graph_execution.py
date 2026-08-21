from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.graph.chapter_production_execution import (
    build_chapter_production_ports,
    invoke_chapter_production_graph,
)
from app.graph.runtime import NODE_NAMES
from app.services.chapter_production_graph_domain import (
    ChapterProductionInvocationContext,
    ChapterProductionSchedulingResult,
)


RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
CONTEXT = ChapterProductionInvocationContext(
    UUID("11111111-1111-4111-8111-111111111111"),
    UUID("22222222-2222-4222-8222-222222222222"),
    UUID("44444444-4444-4444-8444-444444444444"),
)


@asynccontextmanager
async def scheduling_facade(facade: object) -> object:
    yield facade


def graph_state(cursor: str = "draft") -> dict[str, object]:
    return {
        "workflow_run_id": RUN_ID,
        "graph_id": "chapter-production-langgraph",
        "graph_version": "0",
        "cursor": cursor,
        "workflow_checkpoint_index": 0,
        "invocation_id": UUID("55555555-5555-4555-8555-555555555555"),
        "attempt_id": None,
        "claim_id": None,
        "action_request_id": None,
        "resume_reason": "new",
    }


@pytest.mark.anyio
async def test_exact_async_ports_map_domain_results_without_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advance = AsyncMock(
        return_value=ChapterProductionSchedulingResult(
            kind="continue", next_cursor="draft"
        )
    )
    monkeypatch.setattr(
        "app.graph.chapter_production_execution.advance_chapter_production",
        advance,
    )
    facade = SimpleNamespace()
    service = SimpleNamespace(
        _phase_sessions=SimpleNamespace(lease=lambda: scheduling_facade(object())),
        _new_scheduling_facade=lambda _session: facade,
    )
    ports = build_chapter_production_ports(service, CONTEXT)

    assert set(ports) == set(NODE_NAMES)
    for name, port in ports.items():
        outcome = await port(graph_state())
        assert outcome == {"kind": "continue", "next_cursor": "draft"}
        assert set(outcome).isdisjoint({"prose", "content", "report", "locator"})
        advance.assert_awaited_with(
            facade,
            context=CONTEXT,
            workflow_run_id=RUN_ID,
            cursor=name,
        )


@pytest.mark.anyio
async def test_compiled_graph_awaits_async_ports_and_returns_closed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = ChapterProductionSchedulingResult(
        kind="await-user",
        action_request_id=UUID("66666666-6666-4666-8666-666666666666"),
    )
    monkeypatch.setattr(
        "app.graph.chapter_production_execution.advance_chapter_production",
        AsyncMock(return_value=terminal),
    )

    result = await invoke_chapter_production_graph(
        SimpleNamespace(
            _phase_sessions=SimpleNamespace(lease=lambda: scheduling_facade(object())),
            _new_scheduling_facade=lambda _session: SimpleNamespace(),
        ),
        context=CONTEXT,
        state=graph_state("reconstruct"),
    )

    assert result == terminal


@pytest.mark.anyio
async def test_compiled_graph_preserves_sanitized_node_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.graph.chapter_production_execution.advance_chapter_production",
        AsyncMock(side_effect=asyncio.CancelledError("canary")),
    )
    service = SimpleNamespace(
        _phase_sessions=SimpleNamespace(lease=lambda: scheduling_facade(object())),
        _new_scheduling_facade=lambda _session: SimpleNamespace(),
    )

    with pytest.raises(asyncio.CancelledError) as captured:
        await invoke_chapter_production_graph(
            service, context=CONTEXT, state=graph_state("reconstruct")
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "canary" not in repr(captured.value)
