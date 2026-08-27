from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.workflows.reader_panel import PanelMode


def _service(mode: PanelMode, *, matches: int = 1) -> tuple[object, AsyncMock, AsyncMock]:
    key = (str(uuid4()), str(uuid4()), "chapter-quality-v1")
    ready = SimpleNamespace(semantic_ready_key=key)
    pairs = tuple(SimpleNamespace(state=ready) for _ in range(matches))
    enter = AsyncMock(return_value=ready)
    validated_pairs = AsyncMock(return_value=pairs)
    launch = AsyncMock()
    service = object.__new__(ChapterProductionV2Service)
    service._readiness = SimpleNamespace(enter=enter, validated_pairs=validated_pairs)
    service._reader_panel_mode = mode
    service._reader_panel = SimpleNamespace(initialize_from_revision_ready=launch)
    return service, validated_pairs, launch


@pytest.mark.anyio
async def test_off_is_the_first_panel_branch_after_ready() -> None:
    service, validated_pairs, launch = _service(PanelMode.OFF)

    await service._enter_revision_ready_locked(
        run=SimpleNamespace(),
        checkpoint=SimpleNamespace(),
        state=SimpleNamespace(),
        document=SimpleNamespace(),
        version=SimpleNamespace(),
    )

    validated_pairs.assert_not_awaited()
    launch.assert_not_awaited()


@pytest.mark.anyio
async def test_enabled_mode_consumes_the_one_exact_ready_pair() -> None:
    service, validated_pairs, launch = _service(PanelMode.QUICK)
    run = SimpleNamespace()

    await service._enter_revision_ready_locked(
        run=run,
        checkpoint=SimpleNamespace(),
        state=SimpleNamespace(),
        document=SimpleNamespace(),
        version=SimpleNamespace(),
    )

    validated_pairs.assert_awaited_once_with(run)
    launch.assert_awaited_once()
    assert launch.await_args.kwargs["chapter_workflow_run"] is run
    assert launch.await_args.kwargs["mode"] is PanelMode.QUICK


@pytest.mark.anyio
async def test_enabled_mode_rejects_duplicate_ready_pairs_before_panel_launch() -> None:
    service, _, launch = _service(PanelMode.PANEL, matches=2)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await service._enter_revision_ready_locked(
            run=SimpleNamespace(),
            checkpoint=SimpleNamespace(),
            state=SimpleNamespace(),
            document=SimpleNamespace(),
            version=SimpleNamespace(),
        )

    launch.assert_not_awaited()
