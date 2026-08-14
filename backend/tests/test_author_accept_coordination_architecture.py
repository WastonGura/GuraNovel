from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "app/services/author_accept_coordination.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
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


def test_coordinator_has_no_provider_or_facade_authority() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")
    tree = _tree(COORDINATOR)
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

    assert len(source.splitlines()) <= 250
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


def test_coordinator_is_constructed_once_by_the_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "AuthorAcceptCoordinator" in source
    assert source.count("self._author_accept = AuthorAcceptCoordinator(self)") == 1


def test_resolve_author_action_is_a_thin_delegate() -> None:
    method, _ = _service_method("resolve_author_action")

    assert len(method.splitlines()) <= 45
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
    ):
        assert forbidden not in method
    assert "self._author_accept.accept" in method
    assert "ChapterActionDecision.ACCEPT.value" in method


def test_stale_adoption_exception_is_owned_by_the_coordinator_module() -> None:
    facade_source = FACADE.read_text(encoding="utf-8")
    coordinator_source = COORDINATOR.read_text(encoding="utf-8")

    assert "_StaleActionAdopted" in coordinator_source
    assert "class _StaleActionAdopted" not in facade_source
    assert "from app.services.author_accept_coordination import" in facade_source
