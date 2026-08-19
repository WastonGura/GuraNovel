from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH_PACKAGE = ROOT / "app/graph"
RECONSTRUCTION = ROOT / "app/services/chapter_production_graph_reconstruction.py"

FORBIDDEN_GRAPH_IMPORTS = {
    "sqlalchemy",
    "app.models",
    "app.services",
    "app.workspace",
    "app.agents",
    "app.llm",
    "httpx",
    "requests",
}

FORBIDDEN_GRAPH_AUTHORITY = (
    "AsyncSession",
    "DocumentService",
    "select(",
    "with_for_update",
    "Path(",
    "open(",
    "Provider(",
    "provider_selection",
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


def _graph_modules() -> tuple[Path, ...]:
    return tuple(sorted(GRAPH_PACKAGE.glob("*.py")))


def test_graph_package_exists_and_all_modules_are_bounded() -> None:
    modules = _graph_modules()
    assert modules
    for path in modules:
        source = path.read_text(encoding="utf-8")
        tree = _tree(path)
        assert len(source.splitlines()) <= 800, path
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert _span(node) <= 400, (path, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert _span(node) <= 80, (path, node.name)


def test_cursor_enum_is_closed_and_matches_server_topology() -> None:
    from app.graph.contracts import Cursor

    assert {cursor.value for cursor in Cursor} == {
        "reconstruct",
        "draft",
        "await_author_action",
        "author_revision",
        "editor_review",
        "chief_editor_review",
        "lore_review",
        "corrective_revision",
        "mark_revision_ready",
        "finalize",
        "reconcile",
        "complete",
        "cancelled",
    }


def test_graph_package_has_no_database_or_domain_authority_imports() -> None:
    for path in _graph_modules():
        imports = _module_imports(path)
        assert imports.isdisjoint(FORBIDDEN_GRAPH_IMPORTS), (path, imports)
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_GRAPH_AUTHORITY:
            assert forbidden not in source, (path, forbidden)


def test_reconstruction_adapter_exists_and_is_read_only() -> None:
    assert RECONSTRUCTION.exists(), RECONSTRUCTION
    source = RECONSTRUCTION.read_text(encoding="utf-8")
    tree = _tree(RECONSTRUCTION)

    assert len(source.splitlines()) <= 800
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert _span(node) <= 400, node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert _span(node) <= 80, node.name

    for forbidden in (
        "commit(",
        "rollback(",
        "flush(",
        "session.add(",
        "session.delete(",
        "session.merge(",
        "DocumentService",
        "MarkdownStore",
        "Path(",
        "open(",
    ):
        assert forbidden not in source, forbidden
    assert "select(" in source
