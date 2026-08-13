from __future__ import annotations

import ast
from pathlib import Path


MODULE = Path("app/services/initial_candidate_persistence.py")


def test_initial_candidate_persistence_has_one_bounded_public_surface() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    exports = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    assert ast.literal_eval(exports.value) == [
        "InitialCandidateIdentity",
        "InitialCandidatePersistence",
    ]
    assert len(source.splitlines()) <= 600
    assert max(
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ) <= 80


def test_initial_candidate_persistence_keeps_the_phase_boundary_closed() -> None:
    source = MODULE.read_text(encoding="utf-8")
    forbidden = (
        "chapter_production_v2_service",
        "ActionRequest",
        "WriterAgent",
        "RevisionAgent",
        "initial_draft(",
        "finalize_initial",
        "append_state",
    )
    assert all(item not in source for item in forbidden)
    assert "ChapterPhaseSessionLease" in source
    assert "DocumentService" in source
    assert "_InitialEvidencePhase" in source
