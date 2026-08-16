from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
STORE = ROOT / "app/services/revision_readiness_store.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _span(node: ast.AST) -> int:
    return node.end_lineno - node.lineno + 1  # type: ignore[attr-defined]


def _service_method(name: str) -> str:
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
    return ast.get_source_segment(source, method) or ""


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


def test_store_exists_and_is_bounded() -> None:
    assert STORE.exists(), STORE
    source = STORE.read_text(encoding="utf-8")
    tree = _tree(STORE)

    assert len(source.splitlines()) <= 800
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert _span(node) <= 400, node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert _span(node) <= 80, node.name


def test_store_does_not_import_the_facade() -> None:
    source = STORE.read_text(encoding="utf-8")

    assert "app.services.chapter_production_v2_service" not in _module_imports(STORE)
    assert "ChapterProductionV2Service" not in source


def test_store_owns_ready_bodies_and_facade_does_not_define_moved_private_bodies() -> None:
    facade_source = FACADE.read_text(encoding="utf-8")
    store_source = STORE.read_text(encoding="utf-8")

    for fragment in (
        "class RevisionReadinessStore",
        "def ready_semantic_key",
        "def ready_event_payload",
        "class RevisionReadyPair",
    ):
        assert fragment in store_source, fragment

    for fragment in (
        "class _ReadyPair",
        "class _ReviewStateReferences",
        "restored.append((await self._restore_ready_marker_locked",
        "expected_event_keys = {",
        "used_events: set[UUID]",
    ):
        assert fragment not in facade_source, fragment


def test_facade_ready_delegates_are_thin_and_use_the_store() -> None:
    for method_name, store_call in (
        ("_enter_revision_ready_locked", "self._readiness.enter"),
        ("_validated_ready_pairs_locked", "self._readiness.validated_pairs"),
        ("_restore_ready_marker_locked", "self._readiness.restore_marker"),
        ("_validate_existing_ready_pair_locked", "self._readiness.validate_existing_pair"),
        ("_live_review_bindings_locked", "self._readiness.live_review_bindings_locked"),
    ):
        method = _service_method(method_name)
        assert store_call in method, method_name
        assert "self.session" not in method, method_name
        assert "select(" not in method, method_name
        assert "WorkflowCheckpoint(" not in method, method_name
        assert "WorkflowEvent(" not in method, method_name


def test_facade_constructs_store_once() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "RevisionReadinessStore" in source
    assert source.count("self._readiness = RevisionReadinessStore(") == 1


def test_store_methods_with_self_are_not_staticmethods() -> None:
    tree = _tree(STORE)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RevisionReadinessStore"
    )
    for node in owner.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorator_names = {
            decorator.id
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Name)
        }
        if node.args.args and node.args.args[0].arg == "self":
            assert "staticmethod" not in decorator_names, node.name
