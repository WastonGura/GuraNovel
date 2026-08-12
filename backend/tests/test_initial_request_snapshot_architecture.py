from __future__ import annotations

import ast
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "app/services/initial_request_snapshot.py"


def test_initial_request_snapshot_is_tiny_and_pure() -> None:
    assert MODULE.exists()
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "model_dump" not in source
    for forbidden in (
        "sqlalchemy", "AsyncSession", "Repository", "DocumentService", "ProviderAttempt",
        "InitialBootstrapBinding", "filesystem", "pathlib", "open(",
    ):
        assert forbidden not in source
    assert len(source.splitlines()) <= 90
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            limit = 80 if isinstance(node, ast.ClassDef) else 40
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= limit
