from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "app/services/initial_candidate_finalization.py"
LIFECYCLE = ROOT / "app/services/initial_draft_lifecycle.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _span(node: ast.AST) -> int:
    return node.end_lineno - node.lineno + 1  # type: ignore[attr-defined]


def _class_method(path: Path, class_name: str, name: str) -> tuple[str, ast.AsyncFunctionDef]:
    source = path.read_text(encoding="utf-8")
    owner = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, method) or "", method


def _service_method(name: str) -> tuple[str, ast.AsyncFunctionDef]:
    return _class_method(FACADE, "ChapterProductionV2Service", name)


def _initial_router_calls(method: ast.AsyncFunctionDef) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_initial_drafts"
    }


def test_finalizer_is_small_one_way_and_has_no_provider_or_writer_authority() -> None:
    source = FINALIZER.read_text(encoding="utf-8")
    tree = _tree(FINALIZER)
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

    assert len(source.splitlines()) <= 450
    assert all(
        _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert "app.services.chapter_production_v2_service" not in imports
    assert not any(name.startswith("app.agents") or name == "app.llm" for name in imports)
    assert {"create_document", "stage_create_document", "write_staged_files"}.isdisjoint(
        calls
    )
    assert {
        "initial_draft",
        "revise_from_user_feedback",
        "revise_from_review",
    }.isdisjoint(calls)


def test_lifecycle_owns_typed_legacy_routing_without_orm_authority() -> None:
    source = LIFECYCLE.read_text(encoding="utf-8")
    tree = _tree(LIFECYCLE)
    imports = {
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    }
    reconcile, _ = _class_method(LIFECYCLE, "InitialDraftLifecycle", "reconcile")

    assert len(source.splitlines()) <= 150
    assert all(
        _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert "app.services.chapter_production_v2_service" not in imports
    assert not any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in imports)
    assert "InitialCandidateNotApplicable" in reconcile
    assert "InitialRecoveryRoute.LEGACY" in reconcile


def test_initial_facade_methods_are_thin_and_legacy_initial_bodies_are_removed() -> None:
    source = FACADE.read_text(encoding="utf-8")
    forbidden_bodies = {
        "_approved_outline",
        "_operation_key",
        "_operation_run",
        "_resume_with_outline_map",
        "_finalize_draft",
        "_claim_initial_attempt",
        "_committed_draft",
        "_completed_result",
    }
    for name in forbidden_bodies:
        assert f"def {name}(" not in source

    for name in ("start_from_approved_outline", "resume_drafting"):
        method, _ = _service_method(name)
        assert len(method.splitlines()) <= 20
        for forbidden in (
            "self.session",
            "select(",
            "ActionRequest(",
            "WorkflowCheckpoint(",
            "initial_draft(",
            ".lease(",
        ):
            assert forbidden not in method
        assert "self._initial_drafts" in method


def test_mixed_recovery_uses_typed_route_before_legacy_fallback() -> None:
    reconcile_source, reconcile = _service_method("reconcile_indeterminate")
    acknowledge_source, acknowledge = _service_method("acknowledge_provider_no_write")

    assert _initial_router_calls(reconcile) == {"reconcile"}
    assert _initial_router_calls(acknowledge) == {"acknowledge_no_write"}
    assert "InitialRecoveryRoute.LEGACY" in reconcile_source
    assert "except InitialCandidateNotApplicable" not in reconcile_source
    assert "_finalize_draft(" not in reconcile_source
    for method in (reconcile, acknowledge):
        assert not any(
            isinstance(node, ast.Constant) and node.value == "initial"
            for node in ast.walk(method)
        )
    assert "self._initial_drafts" in acknowledge_source


def test_initial_start_and_resume_use_exact_lifecycle_ports() -> None:
    _, start = _service_method("start_from_approved_outline")
    _, resume = _service_method("resume_drafting")

    assert _initial_router_calls(start) == {"start"}
    assert _initial_router_calls(resume) == {"resume"}
