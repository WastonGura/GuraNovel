from __future__ import annotations

import ast
from pathlib import Path


SERVICES = Path(__file__).parents[1] / "app" / "services"


def test_handoff_has_one_way_dependencies_and_size_budget() -> None:
    path = SERVICES / "initial_provider_handoff.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert len(source.splitlines()) <= 520
    for forbidden in (
        "chapter_production_v2_service",
        "ActionRequest",
        "create_document",
        "write_document",
    ):
        assert forbidden not in source
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert (node.end_lineno or node.lineno) - node.lineno + 1 <= 80, node.name
        if isinstance(node, ast.ClassDef):
            assert (node.end_lineno or node.lineno) - node.lineno + 1 <= 400, node.name


def test_generation_snapshot_is_pure_and_bounded() -> None:
    path = SERVICES / "initial_generation_snapshot.py"
    source = path.read_text(encoding="utf-8")

    assert len(source.splitlines()) <= 120
    for forbidden in (
        "sqlalchemy",
        "AsyncSession",
        "ChapterProductionRepository",
        "DocumentService",
        "WriterAgent",
    ):
        assert forbidden not in source
