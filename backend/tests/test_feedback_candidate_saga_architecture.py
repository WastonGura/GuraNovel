from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAGA = ROOT / "app/services/feedback_candidate_saga.py"
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


def test_saga_is_constructed_once_by_the_facade_and_private_bodies_are_removed() -> None:
    facade_source = FACADE.read_text(encoding="utf-8")
    saga_source = SAGA.read_text(encoding="utf-8")

    assert "FeedbackCandidateSaga" in facade_source
    assert facade_source.count("self._feedback_saga = FeedbackCandidateSaga(") == 1
    assert "def _revalidate_revision_prewrite" not in facade_source
    assert "def _finalize_feedback_revision" not in facade_source
    assert "def _revalidate_revision_prewrite" in saga_source
    assert "def _finalize_feedback_revision" in saga_source


def test_request_user_feedback_revision_is_a_thin_composition() -> None:
    method, _ = _service_method("request_user_feedback_revision")

    assert len(method.splitlines()) <= 80
    for forbidden in (
        "self.session",
        "select(",
        "ActionRequest(",
        "WorkflowCheckpoint(",
        "merge_segment_replacements",
        "_revalidate_revision_prewrite",
        "write_document",
        "_finalize_feedback_revision",
        "_validated_prospective_map",
        "DocumentSource",
        "_release_attempt",
    ):
        assert forbidden not in method
    assert "self._feedback_handoff.execute" in method
    assert "self._feedback_saga.persist" in method
    assert "self._feedback_saga.finalize" in method


def test_facade_reconcile_delegates_feedback_finalization_to_the_saga() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "self._feedback_saga.finalize" in source
    assert "await self._finalize_feedback_revision(" not in source
