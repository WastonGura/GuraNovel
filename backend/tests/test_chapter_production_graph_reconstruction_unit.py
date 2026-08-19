from __future__ import annotations

from uuid import uuid4

import pytest

from app.graph.contracts import GRAPH_ID, GRAPH_VERSION, GraphError
from app.services.chapter_production_graph_reconstruction import (
    _cursor_for,
    _optional_uuid,
    _strict_runtime,
)


def test_cursor_for_known_status_and_awaiting_user_consistency() -> None:
    assert _cursor_for("DRAFTING", False) == "draft"
    assert _cursor_for("AUTHOR_REVISION", False) == "author_revision"
    assert _cursor_for("AUTHOR_REVISION", True) == "await_author_action"
    assert _cursor_for("EDITOR_REVIEW", True) == "editor_review"
    assert _cursor_for("CHIEF_FINAL_REVIEW", True) == "chief_editor_review"
    assert _cursor_for("LORE_FINAL_REVIEW", True) == "lore_review"
    assert _cursor_for("REVIEW_REVISION", True) == "corrective_revision"
    assert _cursor_for("CANCELLED", False) == "cancelled"
    assert _cursor_for("COMPLETED", False) == "complete"

    for status in ("COMPLETED", "CANCELLED", "FAILED", "DRAFTING", "UNKNOWN"):
        with pytest.raises(GraphError):
            _cursor_for(status, True)
    with pytest.raises(GraphError):
        _cursor_for("UNKNOWN", False)


def test_strict_runtime_accepts_only_exact_langgraph_pin() -> None:
    valid = {
        "chapter_production_runtime": {
            "scheduler_kind": "langgraph",
            "graph_id": GRAPH_ID,
            "graph_version": GRAPH_VERSION,
        }
    }
    assert _strict_runtime(valid) == (GRAPH_ID, GRAPH_VERSION)

    invalid = {
        "chapter_production_runtime": {
            "scheduler_kind": "langgraph",
            "graph_id": GRAPH_ID,
            "graph_version": "1",
        }
    }
    with pytest.raises(GraphError):
        _strict_runtime(invalid)
    with pytest.raises(GraphError):
        _strict_runtime(None)
    with pytest.raises(GraphError):
        _strict_runtime({"chapter_production_runtime": {"extra": 1}})


def test_optional_uuid_accepts_only_none_uuid_or_canonical_string() -> None:
    value = uuid4()
    assert _optional_uuid(None) is None
    assert _optional_uuid(value) == value
    assert _optional_uuid(str(value)) == value

    for payload in ("not-a-uuid", str(value).upper(), [], object()):
        with pytest.raises(GraphError):
            _optional_uuid(payload)
