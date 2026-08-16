from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
COORDINATOR = ROOT / "app/services/chapter_review_coordinator.py"


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


def _coordinator_method(name: str) -> str:
    source = COORDINATOR.read_text(encoding="utf-8")
    tree = _tree(COORDINATOR)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChapterReviewCoordinator"
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    return ast.get_source_segment(source, method) or ""


def test_coordinator_exists_and_is_bounded() -> None:
    assert COORDINATOR.exists()
    source = COORDINATOR.read_text(encoding="utf-8")
    tree = _tree(COORDINATOR)

    assert len(source.splitlines()) <= 600
    assert all(
        _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_coordinator_does_not_import_the_facade() -> None:
    tree = _tree(COORDINATOR)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    }

    assert "app.services.chapter_production_v2_service" not in imports
    assert "ChapterProductionV2Service" not in COORDINATOR.read_text(encoding="utf-8")


def test_provider_call_is_outside_transactions_and_orm_work() -> None:
    provider = _coordinator_method("_provider")

    assert "agent.review(" in provider or ".review(" in provider
    for forbidden in (
        "service._commit",
        "service._rollback",
        "service.session",
        "select(",
        "with_for_update",
        "_claim_current_review",
        "_persist_current_review",
    ):
        assert forbidden not in provider


def test_coordinator_is_constructed_once_by_the_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "ChapterReviewCoordinator" in source
    assert source.count("self._review_coordinator = ChapterReviewCoordinator(") == 1


def test_execute_current_review_is_a_thin_review_delegate() -> None:
    method, _ = _service_method("execute_current_review")

    assert len(method.splitlines()) <= 40
    assert "self._review_coordinator.execute_review" in method
    for forbidden in (
        "_claim_current_review",
        "_persist_current_review",
        "_release_reviewer_claim",
        "_fail_reviewer",
        "agent.review",
        "ProviderTimeoutError",
        "self.session",
        "select(",
    ):
        assert forbidden not in method


def test_resolve_review_action_is_a_thin_review_delegate() -> None:
    method, _ = _service_method("resolve_review_action")

    assert len(method.splitlines()) <= 40
    assert "self._review_coordinator.resolve_action" in method
    for forbidden in (
        "_review_action_metadata",
        "_validated_persisted_review_report",
        "self.session",
        "select(",
    ):
        assert forbidden not in method


def test_acknowledge_reviewer_no_write_is_a_thin_review_delegate() -> None:
    method, _ = _service_method("acknowledge_reviewer_no_write")

    assert len(method.splitlines()) <= 40
    assert "self._review_coordinator.acknowledge_no_write" in method
    for forbidden in (
        "_exact_review_report_count",
        "_set_reviewer_claim",
        "self.session",
        "select(",
    ):
        assert forbidden not in method
