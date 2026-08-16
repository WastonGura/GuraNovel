from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAGA = ROOT / "app/services/chapter_finalization_saga.py"
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
PROVIDER_CALLS = {
    "draft_initial",
    "revise_from_user_feedback",
    "revise_from_review",
    "user_feedback_revision",
    "execute_review_revision",
    "review_editor",
    "review_chief_final",
    "review_lore_final",
}
FORBIDDEN_IMPORTS = {
    "app.agents",
    "app.llm",
    "app.graph",
    "app.runtime",
    "langgraph",
    "langchain",
}


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
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
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


def test_saga_exists_and_is_bounded() -> None:
    assert SAGA.exists(), SAGA
    source = SAGA.read_text(encoding="utf-8")
    tree = _tree(SAGA)

    assert len(source.splitlines()) <= 800
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert _span(node) <= 400, node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert _span(node) <= 80, node.name


def test_saga_has_no_provider_graph_runtime_or_facade_authority() -> None:
    source = SAGA.read_text(encoding="utf-8")
    imports = _module_imports(SAGA)
    calls = {
        node.func.attr
        for node in ast.walk(_tree(SAGA))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert imports.isdisjoint(FORBIDDEN_IMPORTS)
    assert "app.services.chapter_production_v2_service" not in imports
    assert "ChapterProductionV2Service" not in source
    assert PROVIDER_CALLS.isdisjoint(calls)


def test_facade_constructs_saga_once() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "ChapterFinalizationSaga" in source
    assert source.count("self._finalization = ChapterFinalizationSaga(self)") == 1


def test_finalize_without_reader_panel_is_a_thin_delegate() -> None:
    method = _service_method("finalize_without_reader_panel")

    assert len(method.splitlines()) <= 30
    for forbidden in (
        "self.session",
        "select(",
        "stage_create_document",
        "write_staged_files",
        "_finalized_result_locked",
        "_final_operation_key",
        "_final_document_path",
        "state.complete(",
        "WorkflowEvent(",
        "chapter.final_document_id",
        "DocumentType.CHAPTER_FINAL",
        "MarkdownStore",
    ):
        assert forbidden not in method, forbidden
    assert "self._finalization.finalize" in method


def test_finalization_bodies_are_owned_by_the_saga_not_the_facade() -> None:
    facade_source = FACADE.read_text(encoding="utf-8")
    saga_source = SAGA.read_text(encoding="utf-8")

    for fragment in (
        "class ChapterFinalizationSaga",
        "def _final_operation_key",
        "def _final_document_path",
        "def _valid_final_document_paths",
        "def _verify_final_artifacts",
        "async def _finalized_result_locked",
    ):
        assert fragment in saga_source, fragment
        assert fragment not in facade_source, fragment

    for facade_forbidden in (
        "stage_create_document(",
        "write_staged_files(final_document",
        "final_document.path =",
        "chapter.final_document_id = final_document.id",
        "event_type=\"chapter_finalized\"",
    ):
        assert facade_forbidden not in facade_source, facade_forbidden


def test_saga_reuses_facade_shared_primitives_instead_of_reimplementing_them() -> None:
    source = SAGA.read_text(encoding="utf-8")

    for primitive in (
        "service._require_project_owner",
        "service._chapter",
        "service._run",
        "service._locked_state",
        "service._locked_review_document",
        "service._live_review_bindings_locked",
        "service._verified_snapshot_content",
        "service._locked_current_document_version",
        "service.documents",
        "service._commit",
        "service._rollback",
    ):
        assert primitive in source, primitive
