from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "app/services/feedback_revision_handoff.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"


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


def test_handoff_is_one_way_and_bounded() -> None:
    source = HANDOFF.read_text(encoding="utf-8")
    tree = _tree(HANDOFF)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    }

    assert len(source.splitlines()) <= 800
    assert all(
        _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert "app.services.chapter_production_v2_service" not in imports
    for forbidden in ("write_document", "create_document", "_finalize_feedback_revision"):
        assert forbidden not in source
    assert "clock_timestamp" in source
    assert "database_now" in source
    assert "expires_at" in source


def test_handoff_is_constructed_once_by_the_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "FeedbackRevisionHandoff" in source
    assert source.count("self._feedback_handoff = FeedbackRevisionHandoff(self") == 1


def test_request_user_feedback_revision_is_a_thin_composition() -> None:
    method, _ = _service_method("request_user_feedback_revision")

    assert len(method.splitlines()) <= 160
    for forbidden in (
        "_author_context",
        "_recover_failed_attempt",
        "_decision_operation_key",
        "_resolve_action_row",
        "_append_state",
        "_set_attempt",
        "_attempt_payload",
        "_new_attempt_id",
        "_validated_feedback",
        "_feedback_request",
        "sha256_content",
        "derive_chapter_segment_map",
    ):
        assert forbidden not in method
    assert "self._feedback_handoff.execute" in method
    assert "merge_segment_replacements" in method
    assert "_revalidate_revision_prewrite" in method
    assert "write_document" in method
    assert "_finalize_feedback_revision" in method


def test_plan_dto_is_frozen_slots_and_content_safe() -> None:
    source = HANDOFF.read_text(encoding="utf-8")
    tree = _tree(HANDOFF)
    plan = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FeedbackRevisionPlan"
    )

    keywords = {
        keyword.arg: getattr(keyword.value, "value", None)
        for decorator in plan.decorator_list
        if isinstance(decorator, ast.Call)
        for keyword in decorator.keywords
    }
    assert keywords.get("frozen") is True
    assert keywords.get("slots") is True
    assert keywords.get("repr") is False
    assert "    feedback: str = field(repr=False)" in source
    assert "    segment_map: ChapterSegmentMap = field(repr=False)" in source
    assert "    candidate: CandidateChapterOutput = field(repr=False)" in source

