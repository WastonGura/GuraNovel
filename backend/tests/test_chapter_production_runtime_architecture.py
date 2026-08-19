from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.core import WorkflowEvent
from app.services.chapter_production_runtime import (
    SCHEDULER_KIND_SERVICE_V2,
    chapter_production_runtime_pin,
    next_event_sequence,
    strict_runtime,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULE = ROOT / "app/services/chapter_production_runtime.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
BOOTSTRAP_EVIDENCE = ROOT / "app/services/initial_bootstrap_evidence.py"
FINALIZATION_SAGA = ROOT / "app/services/chapter_finalization_saga.py"
REVIEW_PERSISTENCE = ROOT / "app/services/chapter_review_persistence.py"
READINESS_STORE = ROOT / "app/services/revision_readiness_store.py"

FORBIDDEN_RUNTIME_IMPORTS = {
    "app.agents",
    "app.llm",
    "app.graph",
    "app.workspace",
    "app.services.chapter_production_v2_service",
    "langgraph",
    "langchain",
    "httpx",
    "requests",
}

V2_EVENT_EMISSION_MODULES = (
    FINALIZATION_SAGA,
    REVIEW_PERSISTENCE,
    READINESS_STORE,
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _span(node: ast.AST) -> int:
    return node.end_lineno - node.lineno + 1  # type: ignore[attr-defined]


def _module_imports(path: Path) -> set[str]:
    tree = _tree(path)
    return {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    }


def test_runtime_module_exists_and_is_pure_and_bounded() -> None:
    assert RUNTIME_MODULE.exists(), RUNTIME_MODULE
    source = RUNTIME_MODULE.read_text(encoding="utf-8")
    tree = _tree(RUNTIME_MODULE)

    assert len(source.splitlines()) <= 800
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert _span(node) <= 400, node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert _span(node) <= 80, node.name

    imports = _module_imports(RUNTIME_MODULE)
    assert imports.isdisjoint(FORBIDDEN_RUNTIME_IMPORTS)
    assert "chapter_production_v2_service" not in source


def test_runtime_pin_exports_and_canonical_invariants() -> None:
    pin = chapter_production_runtime_pin()
    assert pin == {
        "scheduler_kind": SCHEDULER_KIND_SERVICE_V2,
        "graph_id": "chapter-production-v2",
        "graph_version": "0",
    }
    validated = strict_runtime(pin)
    assert validated == pin
    assert validated is not pin  # Must return a distinct copy to prevent mutation pollution

    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime({"scheduler_kind": "custom", "graph_id": "chapter-production-v2", "graph_version": "0"})
    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime({"scheduler_kind": SCHEDULER_KIND_SERVICE_V2, "graph_id": "other", "graph_version": "0"})
    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime({"scheduler_kind": SCHEDULER_KIND_SERVICE_V2, "graph_id": "chapter-production-v2", "graph_version": "1"})
    with pytest.raises(ChapterProductionV2ValidationError):
        strict_runtime(None)


def test_facade_enforces_strict_runtime_pin_validation() -> None:
    source = FACADE.read_text(encoding="utf-8")
    tree = _tree(FACADE)

    imports = _module_imports(FACADE)
    assert "app.services.chapter_production_runtime" in imports or "strict_runtime" in source
    assert "strict_runtime(" in source

    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChapterProductionV2Service"
    )
    run_metadata_fn = next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_run_metadata"
    )
    run_metadata_source = ast.get_source_segment(source, run_metadata_fn) or ""
    assert "strict_runtime(" in run_metadata_source
    assert "chapter_production_runtime" in run_metadata_source


def test_bootstrap_evidence_attaches_and_validates_runtime_pin() -> None:
    source = BOOTSTRAP_EVIDENCE.read_text(encoding="utf-8")
    assert "chapter_production_runtime_pin" in source
    assert "chapter_production_runtime" in source

    from app.services.initial_bootstrap_evidence import (
        InitialBootstrapBinding,
        pristine_run_metadata,
    )

    binding = InitialBootstrapBinding(
        workflow_run_id=uuid4(),
        chapter_id=uuid4(),
        outline_document_id=uuid4(),
        outline_version_id=uuid4(),
        outline_content_hash="a" * 64,
        operation_key="b" * 64,
        chief_editor_required=True,
    )
    metadata = pristine_run_metadata(binding)
    assert metadata["chapter_production_runtime"] == chapter_production_runtime_pin()


def test_all_v2_event_emission_paths_use_next_event_sequence() -> None:
    for module_path in V2_EVENT_EMISSION_MODULES:
        assert module_path.exists(), module_path
        source = module_path.read_text(encoding="utf-8")
        tree = _tree(module_path)

        assert "next_event_sequence" in source, f"{module_path.name} must use next_event_sequence"

        workflow_event_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "WorkflowEvent")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "WorkflowEvent")
            )
        ]
        assert workflow_event_calls, f"Expected WorkflowEvent instantiation in {module_path.name}"

        for call in workflow_event_calls:
            kw_names = {kw.arg for kw in call.keywords if kw.arg is not None}
            assert "event_sequence" in kw_names, (
                f"WorkflowEvent instantiation at line {call.lineno} in {module_path.name} "
                f"must explicitly specify event_sequence"
            )


@pytest.mark.anyio
async def test_next_event_sequence_allocator_monotonicity_invariants() -> None:
    run_id = uuid4()
    other_run_id = uuid4()

    # Case 1: Database has no events, no in-memory events -> sequence starts at 1
    session_empty = MagicMock()
    session_empty.scalar = AsyncMock(return_value=None)
    session_empty.new = set()
    seq = await next_event_sequence(session_empty, run_id)
    assert seq == 1

    # Case 2: Database has max sequence 5, no in-memory events -> sequence becomes 6
    session_db = MagicMock()
    session_db.scalar = AsyncMock(return_value=5)
    session_db.new = set()
    seq = await next_event_sequence(session_db, run_id)
    assert seq == 6

    # Case 3: Database has max sequence 5, in-memory has sequence 6, 7 -> sequence becomes 8
    session_unflushed = MagicMock()
    session_unflushed.scalar = AsyncMock(return_value=5)
    session_unflushed.new = {
        WorkflowEvent(workflow_run_id=run_id, event_sequence=6, event_type="test1"),
        WorkflowEvent(workflow_run_id=run_id, event_sequence=7, event_type="test2"),
        WorkflowEvent(workflow_run_id=other_run_id, event_sequence=99, event_type="other"),
    }
    seq = await next_event_sequence(session_unflushed, run_id)
    assert seq == 8


def test_database_model_enforces_event_sequence_uniqueness_constraint() -> None:
    from app.models.core import WorkflowEvent

    has_event_sequence_col = hasattr(WorkflowEvent, "event_sequence")
    assert has_event_sequence_col

    table_args = getattr(WorkflowEvent, "__table_args__", ())
    index_names = [arg.name for arg in table_args if hasattr(arg, "name")]
    assert "uq_workflow_events_run_sequence" in index_names

    unique_index = next(arg for arg in table_args if getattr(arg, "name", None) == "uq_workflow_events_run_sequence")
    assert unique_index.unique is True
    indexed_columns = [col.name if hasattr(col, "name") else str(col) for col in unique_index.columns]
    assert "workflow_run_id" in indexed_columns
    assert "event_sequence" in indexed_columns
