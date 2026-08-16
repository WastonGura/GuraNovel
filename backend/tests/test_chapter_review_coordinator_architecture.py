from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
COORDINATOR = ROOT / "app/services/chapter_review_coordinator.py"
CLAIM = ROOT / "app/services/chapter_review_claim.py"
PERSISTENCE = ROOT / "app/services/chapter_review_persistence.py"
VALIDATION = ROOT / "app/services/chapter_review_validation.py"
PROTOCOLS = ROOT / "app/services/chapter_review_protocols.py"


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


def test_review_modules_exist_and_are_bounded() -> None:
    for path, limit in (
        (COORDINATOR, 800),
        (CLAIM, 800),
        (PERSISTENCE, 800),
        (VALIDATION, 800),
        (PROTOCOLS, 800),
    ):
        assert path.exists(), path
        source = path.read_text(encoding="utf-8")
        tree = _tree(path)
        assert len(source.splitlines()) <= limit, path
        assert all(
            _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ), path


def test_review_modules_do_not_import_the_facade() -> None:
    for path in (COORDINATOR, CLAIM, PERSISTENCE, VALIDATION, PROTOCOLS):
        assert "app.services.chapter_production_v2_service" not in _module_imports(path)
        assert "ChapterProductionV2Service" not in path.read_text(encoding="utf-8")


def test_provider_call_is_outside_transactions_and_orm_work() -> None:
    provider = _coordinator_method("_provider")

    assert "agent.review(" in provider or ".review(" in provider
    for forbidden in (
        "service._commit",
        "service._rollback",
        "service.session",
        "select(",
        "with_for_update",
        "claim_current_review",
        "persist_current_review",
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


def test_facade_no_longer_defines_moved_review_bodies() -> None:
    source = FACADE.read_text(encoding="utf-8")

    for name in (
        "_claim_current_review",
        "_build_review_context_locked",
        "_review_context_snapshots",
        "_persist_current_review",
        "_resolve_review_action_locked",
        "_set_reviewer_claim",
        "_release_reviewer_claim",
        "_fail_reviewer",
        "_validated_resolved_review_action",
        "_validated_persisted_review_report",
    ):
        assert f"def {name}" not in source, name
        assert f"async def {name}" not in source, name


def test_review_phase_modules_define_moved_bodies() -> None:
    claim = CLAIM.read_text(encoding="utf-8")
    persistence = PERSISTENCE.read_text(encoding="utf-8")

    for name in (
        "claim_current_review",
        "build_review_context_locked",
        "review_context_snapshots",
        "set_reviewer_claim",
        "release_reviewer_claim",
        "fail_reviewer",
    ):
        assert f"def {name}" in claim or f"async def {name}" in claim, name
    validation = VALIDATION.read_text(encoding="utf-8")
    for name in (
        "persist_current_review",
        "resolve_review_action_locked",
    ):
        assert f"def {name}" in persistence or f"async def {name}" in persistence, name
    for name in (
        "validated_persisted_review_report",
        "validated_resolved_review_action",
        "new_review_action",
        "review_action_metadata",
    ):
        assert f"def {name}" in validation or f"async def {name}" in validation, name


def test_coordinator_uses_shared_reviewer_claim_status_constant() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")

    assert "REVIEWER_CLAIM_STATUS_CLAIMED" in source
    assert 'claim.get("status") != REVIEWER_CLAIM_STATUS_CLAIMED' in source
    assert 'claim.get("status") != "claimed"' not in source


def test_coordinator_and_phase_modules_use_typed_protocol() -> None:
    coordinator = COORDINATOR.read_text(encoding="utf-8")
    protocols = PROTOCOLS.read_text(encoding="utf-8")

    assert "class ChapterReviewCoordinator" in coordinator
    assert "def __init__(self, service: ChapterReviewService)" in coordinator
    assert "runtime_checkable" in protocols
    assert "class ChapterReviewService" in protocols
