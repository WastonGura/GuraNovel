from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAGA = ROOT / "app/services/manual_edit_saga.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
COORDINATOR = ROOT / "app/services/chapter_draft_revision_coordinator.py"
PROVIDER_CALLS = {
    "draft_initial",
    "revise_from_user_feedback",
    "revise_from_review",
    "user_feedback_revision",
    "execute_review_revision",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _span(node: ast.AST) -> int:
    return node.end_lineno - node.lineno + 1  # type: ignore[attr-defined]


def _service_method(name: str) -> tuple[str, ast.AsyncFunctionDef]:
    source = FACADE.read_text(encoding="utf-8")
    owner = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "ChapterProductionV2Service"
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, method) or "", method


def test_saga_has_no_provider_or_facade_authority() -> None:
    source = SAGA.read_text(encoding="utf-8")
    tree = _tree(SAGA)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert len(source.splitlines()) <= 800
    assert all(
        _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert "app.services.chapter_production_v2_service" not in imports
    assert not any(name.startswith("app.agents") or name == "app.llm" for name in imports)
    assert PROVIDER_CALLS.isdisjoint(calls)
    assert "clock_timestamp" in source
    assert "database_now" in source
    assert "expires_at" in source


def test_saga_is_constructed_once_by_the_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "ManualEditCoordinator" in source
    assert source.count("self._manual_edit = ManualEditCoordinator(self)") == 1


def test_submit_manual_edit_is_a_thin_delegate() -> None:
    method, _ = _service_method("submit_manual_edit")

    assert len(method.splitlines()) <= 50
    for forbidden in (
        "self.session",
        "select(",
        "ActionRequest(",
        "WorkflowCheckpoint(",
        "_author_context",
        "_resolve_action_row",
        "_append_state",
        "datetime.now",
        "resolve_action(",
        "write_document",
        "_finalize_manual_edit",
        "normalize_chapter_content",
        "_validated_prospective_map",
        "DocumentSource",
    ):
        assert forbidden not in method
    assert "self._manual_edit.submit" in method


def test_finalize_moved_into_the_saga_and_reconcile_delegates() -> None:
    facade_source = FACADE.read_text(encoding="utf-8")
    saga_source = SAGA.read_text(encoding="utf-8")
    coordinator_source = COORDINATOR.read_text(encoding="utf-8")

    assert "def _finalize_manual_edit" not in facade_source
    assert "def _finalize_manual_edit" in saga_source
    assert "self._draft_revision.reconcile" in facade_source
    assert "self._manual_edit.reconcile_manual" in coordinator_source
