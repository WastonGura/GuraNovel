"""Compatibility and authority-boundary tests for V2 phase contracts."""

from __future__ import annotations

import ast
import importlib
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.services import (
    ChapterProductionV2CommitIndeterminateError as PackageCommitError,
)
from app.services import ChapterProductionV2Finalized as PackageFinalized
from app.services import ChapterProductionV2ProviderError as PackageProviderError
from app.services import (
    ChapterProductionV2ReconciliationError as PackageReconciliationError,
)
from app.services import (
    ChapterProductionV2ReviewProviderError as PackageReviewProviderError,
)
from app.services import ChapterProductionV2Started as PackageStarted
from app.services import ChapterProductionV2Updated as PackageUpdated
from app.services import ChapterProductionV2ValidationError as PackageValidationError


SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
CONTRACTS = SERVICES / "chapter_production_v2_contracts.py"


def _contracts() -> object:
    return importlib.import_module("app.services.chapter_production_v2_contracts")


def _tree(path: Path) -> tuple[str, ast.Module]:
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source)


def _imported_modules(tree: ast.AST) -> set[str]:
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def test_contracts_preserve_stable_service_and_package_export_identity() -> None:
    contracts = _contracts()
    facade = importlib.import_module("app.services.chapter_production_v2_service")
    expected = {
        "ChapterProductionV2ValidationError": PackageValidationError,
        "ChapterProductionV2ProviderError": PackageProviderError,
        "ChapterProductionV2ReviewProviderError": PackageReviewProviderError,
        "ChapterProductionV2CommitIndeterminateError": PackageCommitError,
        "ChapterProductionV2ReconciliationError": PackageReconciliationError,
        "ChapterProductionV2Started": PackageStarted,
        "ChapterProductionV2Updated": PackageUpdated,
        "ChapterProductionV2Finalized": PackageFinalized,
    }

    for name, package_export in expected.items():
        extracted = getattr(contracts, name)
        assert getattr(facade, name) is extracted
        assert package_export is extracted


def test_existing_result_dto_shapes_remain_exact() -> None:
    contracts = _contracts()
    started = contracts.ChapterProductionV2Started(*(uuid4() for _ in range(6)))
    updated = contracts.ChapterProductionV2Updated(
        workflow_run_id=uuid4(),
        draft_document_id=uuid4(),
        draft_version_id=uuid4(),
        action_request_id=None,
    )
    finalized = contracts.ChapterProductionV2Finalized(*(uuid4() for _ in range(3)))

    assert tuple(started.__dataclass_fields__) == (
        "workflow_run_id",
        "action_request_id",
        "outline_document_id",
        "outline_version_id",
        "draft_document_id",
        "draft_version_id",
    )
    assert tuple(updated.__dataclass_fields__) == (
        "workflow_run_id",
        "draft_document_id",
        "draft_version_id",
        "action_request_id",
    )
    assert tuple(finalized.__dataclass_fields__) == (
        "workflow_run_id",
        "final_document_id",
        "final_version_id",
    )
    with pytest.raises(FrozenInstanceError):
        updated.action_request_id = uuid4()


def test_phase_snapshots_are_frozen_pure_values_with_content_safe_repr() -> None:
    contracts = _contracts()
    scope = contracts.ChapterDraftPhaseScope(
        project_id=uuid4(),
        chapter_id=uuid4(),
        workflow_run_id=uuid4(),
        actor_user_id=uuid4(),
    )
    snapshot = contracts.ChapterDraftSourceSnapshot(
        scope=scope,
        checkpoint_index=3,
        source_document_id=uuid4(),
        source_version_id=uuid4(),
        source_content_hash="a" * 64,
        content="PRIVATE-CANDIDATE-CONTENT",
    )

    assert "PRIVATE-CANDIDATE-CONTENT" not in repr(snapshot)
    assert not hasattr(scope, "__dict__")
    assert not hasattr(snapshot, "__dict__")
    with pytest.raises(FrozenInstanceError):
        snapshot.checkpoint_index = 4


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("project_id", "PRIVATE-PROJECT"),
        ("chapter_id", object()),
        ("workflow_run_id", uuid4().hex),
        ("actor_user_id", type("SecretAuthority", (), {})()),
        ("actor_user_id", UUID(int=0)),
    ),
)
def test_phase_scope_rejects_non_uuid_authority_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    contracts = _contracts()
    values: dict[str, object] = {
        "project_id": uuid4(),
        "chapter_id": uuid4(),
        "workflow_run_id": uuid4(),
        "actor_user_id": uuid4(),
    }
    values[field_name] = invalid_value

    with pytest.raises(contracts.ChapterProductionV2ValidationError) as raised:
        contracts.ChapterDraftPhaseScope(**values)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("scope", object()),
        ("checkpoint_index", True),
        ("checkpoint_index", -1),
        ("checkpoint_index", 2_147_483_648),
        ("source_document_id", "PRIVATE-DOCUMENT"),
        ("source_version_id", uuid4().hex),
        ("source_version_id", UUID(int=0)),
        ("source_content_hash", "PRIVATE-CONTENT"),
        ("source_content_hash", "A" * 64),
        ("content", object()),
    ),
)
def test_source_snapshot_rejects_noncanonical_runtime_values(
    field_name: str,
    invalid_value: object,
) -> None:
    contracts = _contracts()
    scope = contracts.ChapterDraftPhaseScope(
        project_id=uuid4(),
        chapter_id=uuid4(),
        workflow_run_id=uuid4(),
        actor_user_id=uuid4(),
    )
    values: dict[str, object] = {
        "scope": scope,
        "checkpoint_index": 0,
        "source_document_id": uuid4(),
        "source_version_id": uuid4(),
        "source_content_hash": "a" * 64,
        "content": "PRIVATE-CANDIDATE-CONTENT",
    }
    values[field_name] = invalid_value

    with pytest.raises(contracts.ChapterProductionV2ValidationError) as raised:
        contracts.ChapterDraftSourceSnapshot(**values)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "PRIVATE" not in str(raised.value)


def test_contract_module_has_one_way_dependency_and_no_authority_types() -> None:
    assert CONTRACTS.exists()
    source, tree = _tree(CONTRACTS)
    imports = _imported_modules(tree)
    annotations = "\n".join(
        ast.unparse(node.annotation)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
    )

    assert "ChapterProductionV2Service" not in source
    assert "app.services.chapter_production_v2_service" not in imports
    for forbidden in (
        "AsyncSession",
        "ChapterProductionRepository",
        "DocumentService",
        "WorkflowRun",
        "WorkflowCheckpoint",
        "ActionRequest",
        "Document",
        "DocumentVersion",
        "ReviewReport",
    ):
        assert forbidden not in annotations
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in (
            "app.agents",
            "app.models",
            "app.services.document_service",
            "app.services.chapter_production_repository",
            "app.workspace",
            "app.api",
            "app.graphs",
            "langgraph",
            "sqlalchemy",
            "pathlib",
            "os",
        )
    )


def test_contract_module_stays_within_size_budgets() -> None:
    source, tree = _tree(CONTRACTS)
    assert len(source.splitlines()) <= 800
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno is not None
            assert node.end_lineno - node.lineno + 1 <= 400
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno is not None
            assert node.end_lineno - node.lineno + 1 <= 80
