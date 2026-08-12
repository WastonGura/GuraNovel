from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


MODULE = Path(__file__).resolve().parents[1] / "app/services/initial_run_bootstrap.py"


def test_bootstrap_has_only_persistence_composition_authority() -> None:
    assert MODULE.exists()
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "ChapterPhaseSessionLease" in source
    assert "ChapterProductionRepository" in source
    assert "validate_pristine_initial_evidence" in source
    for forbidden in (
        "WriterAgent",
        "RevisionAgent",
        "ProviderAttemptStore",
        "ActionRequest",
        "CandidateChapterOutput",
        "stage_create_document",
        "stage_write_document",
        "chapter_production_v2_service",
    ):
        assert forbidden not in source
    assert len(source.splitlines()) <= 260
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 200
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 65


def test_public_constructor_and_method_are_narrow() -> None:
    from app.services.initial_run_bootstrap import InitialRunBootstrap

    assert tuple(inspect.signature(InitialRunBootstrap).parameters) == (
        "phase_sessions",
        "chief_editor_required",
    )
    assert tuple(inspect.signature(InitialRunBootstrap.start_or_resume).parameters) == (
        "self",
        "project_id",
        "chapter_id",
        "actor_user_id",
    )
    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        InitialRunBootstrap(object(), True)  # type: ignore[arg-type]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert ChapterPhaseSessionLease is not object
