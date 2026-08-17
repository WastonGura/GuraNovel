from __future__ import annotations

import pytest

from app.services.chapter_production_runtime import (
    SCHEDULER_KIND_LEGACY,
    SCHEDULER_KIND_SERVICE_V2,
    SCHEDULER_KIND_SERVICE_V2_LEGACY,
    chapter_production_runtime_pin,
    classify_runtime,
    strict_runtime,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


def test_pin_is_exact_and_server_owned() -> None:
    pin = chapter_production_runtime_pin()

    assert pin == {
        "scheduler_kind": SCHEDULER_KIND_SERVICE_V2,
        "graph_id": "chapter-production-v2",
        "graph_version": "0",
    }
    assert strict_runtime(pin) == pin


def test_absent_runtime_is_legacy() -> None:
    assert strict_runtime({}) is None
    assert classify_runtime({}) == SCHEDULER_KIND_LEGACY


@pytest.mark.parametrize(
    "payload",
    (
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


def test_service_v2_legacy_is_rejected_as_pin() -> None:
    payload = {
        "scheduler_kind": SCHEDULER_KIND_SERVICE_V2_LEGACY,
        "graph_id": "chapter-production-v2",
        "graph_version": "0",
    }

    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime(payload)
