from __future__ import annotations

import ast

import pytest
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SAGA = ROOT / "app/services/review_revision_saga.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"

PROVIDER_CALLS = {
    "draft_initial",
    "revise_from_user_feedback",
    "revise_from_review",
    "user_feedback_revision",
    "review_driven_revision",
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


def _budget_base_ref() -> str:
    for ref in ("main", "origin/main", "refs/remotes/origin/main", "HEAD^1"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return ref
    pytest.skip("base ref unavailable in this checkout")


def _production_additions() -> tuple[int, int]:
    ref = _budget_base_ref()
    result = subprocess.run(
        ["git", "diff", f"{ref}...HEAD", "--numstat", "--", SAGA.relative_to(REPO).as_posix()],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    added = 0
    files = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            added += int(parts[0])
            files += 1
    return added, files


def test_saga_is_bounded_one_way_and_has_no_provider_authority() -> None:
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
    assert "write_document" in source
    assert "service.documents.write_document" in source
    assert "service._review_revision_context" in source
    assert "service._locked_current_revision" in source


def test_saga_is_constructed_once_by_the_facade_and_private_bodies_are_removed() -> None:
    facade_source = FACADE.read_text(encoding="utf-8")
    saga_source = SAGA.read_text(encoding="utf-8")

    assert "ReviewRevisionSaga" in facade_source
    assert facade_source.count("self._review_saga = ReviewRevisionSaga(") == 1
    assert "def _write_review_revision" not in facade_source
    assert "def _finalize_review_revision" not in facade_source
    assert "def _finalize_review_revision" in saga_source


def test_persist_review_revision_is_a_thin_composition() -> None:
    method, _ = _service_method("_persist_review_revision")

    assert len(method.splitlines()) <= 80
    for forbidden in (
        "self.session",
        "select(",
        "merge_segment_replacements",
        "write_document",
        "_review_revision_context",
        "_review_report_input_hash",
        "_exact_attempt",
        "_validated_prospective_map",
        "_finalize_review_revision",
        "_release_attempt",
        "DocumentSource",
    ):
        assert forbidden not in method
    assert "self._review_saga.persist" in method
    assert "self._review_saga.finalize" in method


def test_facade_reconcile_delegates_review_finalization_to_the_saga() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "self._review_saga.finalize" in source
    assert "await self._finalize_review_revision(" not in source


def test_total_production_additions_stay_within_budget() -> None:
    added, files = _production_additions()

    assert added <= 600
    assert files <= 5


def test_local_slot_and_stage_mappings_match_facade_helpers() -> None:
    from types import SimpleNamespace
    from uuid import UUID

    from app.models import ReviewMode
    from app.services.chapter_production_v2_service import _review_report_slots
    from app.services.review_revision_saga import _report_slots, _review_stage
    from app.workflows.chapter_production import ChapterReviewStage

    editor_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    chief_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    lore_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    state = SimpleNamespace(
        editor_report_id=str(editor_id),
        chief_editor_report_id=str(chief_id),
        lore_report_id=str(lore_id),
    )

    assert _report_slots(state) == _review_report_slots(
        editor_report_id=editor_id,
        chief_editor_report_id=chief_id,
        lore_report_id=lore_id,
    )
    assert _review_stage(ReviewMode.CHAPTER_EDITOR.value) is ChapterReviewStage.EDITOR
    assert (
        _review_stage(ReviewMode.CHAPTER_CHIEF_FINAL.value)
        is ChapterReviewStage.CHIEF_EDITOR
    )
    assert _review_stage(ReviewMode.CHAPTER_FINAL_LORE.value) is ChapterReviewStage.LORE
