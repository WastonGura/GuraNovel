from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
RECOVERY = ROOT / "app/services/chapter_production_recovery.py"


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


def test_recovery_module_exists_and_is_bounded() -> None:
    assert RECOVERY.exists(), RECOVERY
    source = RECOVERY.read_text(encoding="utf-8")
    tree = _tree(RECOVERY)

    assert len(source.splitlines()) <= 800
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert _span(node) <= 400, node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert _span(node) <= 80, node.name


def test_recovery_does_not_import_the_facade() -> None:
    imports = _module_imports(RECOVERY)

    assert "app.services.chapter_production_v2_service" not in imports
    assert "ChapterProductionV2Service" not in RECOVERY.read_text(encoding="utf-8")


def test_facade_constructs_recovery_once() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "ChapterProductionRecovery" in source
    assert source.count("self._recovery = ChapterProductionRecovery(self)") == 1


def test_facade_recovery_delegates_are_thin() -> None:
    for method_name, recovery_call in (
        ("load_state", "self._recovery.load_state"),
        ("_reconcile_review_route", "self._recovery.reconcile_review_route"),
        ("_fail_provider", "self._recovery.fail_provider"),
        ("_recover_failed_attempt", "self._recovery.recover_failed_attempt"),
        ("_release_attempt", "self._recovery.release_attempt"),
        ("_author_context", "self._recovery.author_context"),
        ("_review_revision_context", "self._recovery.review_revision_context"),
        ("_locked_review_document", "self._recovery.locked_review_document"),
        ("_exact_review_report_count", "self._recovery.exact_review_report_count"),
        ("_locked_state", "self._recovery.locked_state"),
        ("_verified_snapshot_content", "self._recovery.verified_snapshot_content"),
        ("_locked_current_revision", "self._recovery.locked_current_revision"),
    ):
        method = _service_method(method_name)
        assert recovery_call in method, method_name
        for forbidden in (
            "self.session",
            "select(",
            "with_for_update",
            "pg_advisory",
            "MarkdownStore",
            "Path(",
        ):
            assert forbidden not in method, (method_name, forbidden)


def test_facade_no_longer_defines_recovery_bodies() -> None:
    source = FACADE.read_text(encoding="utf-8")

    for fragment in (
        "source_checkpoint_index=attempt_checkpoint_index - 1",
        "attempt.get(\"kind\") != expected_kind",
        "stale_document.current_version_id",
        "trigger_mode = report_slots[-1][1]",
        "chapter.current_draft_document_id != document.id",
        "int(count or 0)",
        "checkpoint.checkpoint_index != checkpoints[1].checkpoint_index + 1",
    ):
        assert fragment not in source, fragment


def test_facade_has_no_direct_orm_or_filesystem_authority() -> None:
    source = FACADE.read_text(encoding="utf-8")

    for forbidden in (
        "select(",
        "with_for_update",
        "pg_advisory_xact_lock",
        "MarkdownStore(",
        "Path(",
    ):
        assert forbidden not in source, forbidden


def test_recovery_defines_recovery_bodies() -> None:
    source = RECOVERY.read_text(encoding="utf-8")

    for name in (
        "recover_failed_attempt",
        "release_attempt",
        "author_context",
        "review_revision_context",
        "locked_review_document",
        "exact_review_report_count",
        "locked_state",
    ):
        assert f"async def {name}" in source or f"def {name}" in source, name
