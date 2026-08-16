from __future__ import annotations

import ast
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HANDOFF = ROOT / "app/services/review_revision_handoff.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"

PRODUCTION_FILES = (
    "backend/app/services/review_revision_handoff.py",
    "backend/app/services/chapter_production_v2_service.py",
)


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
    raise AssertionError("no main/origin/main ref available for production budget gate")


def _production_additions() -> int:
    ref = _budget_base_ref()
    result = subprocess.run(
        ["git", "diff", f"{ref}...HEAD", "--numstat", "--", *PRODUCTION_FILES],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    added = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            added += int(parts[0])
    return added


def test_handoff_is_one_way_bounded_and_service_style() -> None:
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
    for forbidden in ("write_document", "create_document", "_finalize_review_revision"):
        assert forbidden not in source
    for expected_call in (
        "service._review_revision_context",
        "service._review_revision_request",
        "service._review_report_input_hash",
        "service._review_operation_key",
        "service._attempt_payload",
        "service._set_attempt",
        "service._release_attempt",
        "service._fail_provider",
    ):
        assert expected_call in source


def test_total_production_additions_stay_within_budget() -> None:
    assert _production_additions() <= 600


def test_handoff_is_constructed_once_by_the_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "ReviewRevisionHandoff" in source
    assert source.count("self._review_handoff = ReviewRevisionHandoff(self") == 1


def test_execute_review_revision_is_a_thin_composition() -> None:
    method, _ = _service_method("execute_review_revision")

    assert len(method.splitlines()) <= 80
    for forbidden in (
        "_review_revision_context",
        "_recover_failed_attempt",
        "_review_operation_key",
        "_review_report_input_hash",
        "_review_revision_request",
        "_set_attempt",
        "_attempt_payload",
        "_new_attempt_id",
        "merge_segment_replacements",
        "write_document",
        "_finalize_review_revision",
        "review_driven_revision",
    ):
        assert forbidden not in method
    assert "self._review_handoff.execute" in method
    assert "self._persist_review_revision" in method


def test_plan_dto_is_frozen_slots_and_content_safe() -> None:
    source = HANDOFF.read_text(encoding="utf-8")
    tree = _tree(HANDOFF)
    plan = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ReviewRevisionPlan"
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
    assert "    segment_map: ChapterSegmentMap = field(repr=False)" in source
    assert "    candidate: CandidateChapterOutput = field(repr=False)" in source
