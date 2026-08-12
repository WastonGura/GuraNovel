from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from uuid import uuid4

import pytest


BACKEND = Path(__file__).resolve().parents[1]
STORE = BACKEND / "app" / "services" / "provider_attempt_store.py"


def _module():
    return importlib.import_module("app.services.provider_attempt_store")


def test_store_has_one_way_persistence_only_dependencies() -> None:
    assert STORE.exists()
    source = STORE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "app.agents",
        "app.services.chapter_production_v2_service",
        "app.services.document_service",
        "app.workflows",
        "app.workspace",
        "aiofiles",
        "langgraph",
        "os",
        "pathlib",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not ({"add", "commit", "delete", "flush", "merge", "rollback"} & calls)


def test_store_api_is_scope_bound_and_within_size_budgets() -> None:
    module = _module()
    assert tuple(module.__all__) == ("ProviderAttemptScope", "ProviderAttemptStore")
    scope_parameters = tuple(inspect.signature(module.ProviderAttemptScope).parameters)
    assert scope_parameters == (
        "project_id",
        "chapter_id",
        "workflow_run_id",
        "kind",
        "operation_key",
        "checkpoint_index",
        "attempt_id",
    )
    assert tuple(inspect.signature(module.ProviderAttemptStore).parameters) == (
        "session",
        "repository",
    )
    for method in (
        "claim",
        "mark_failed",
        "release",
        "recover_failed",
        "acknowledge_no_write",
    ):
        assert "scope" in inspect.signature(getattr(module.ProviderAttemptStore, method)).parameters

    source = STORE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert len(source.splitlines()) <= 600
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 400
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 80


def test_scope_is_frozen_content_free_and_strict() -> None:
    contracts = importlib.import_module("app.services.provider_attempt_contracts")
    module = _module()
    scope = module.ProviderAttemptScope(
        project_id=uuid4(),
        chapter_id=uuid4(),
        workflow_run_id=uuid4(),
        kind=contracts.ProviderAttemptKind.INITIAL,
        operation_key="a" * 64,
        checkpoint_index=0,
        attempt_id=uuid4(),
    )
    assert repr(scope) == "ProviderAttemptScope()"
    with pytest.raises((AttributeError, TypeError)):
        scope.operation_key = "b" * 64

    invalid_values = (
        {"project_id": uuid4().hex},
        {"kind": "initial"},
        {"operation_key": "PRIVATE"},
        {"checkpoint_index": True},
        {"attempt_id": uuid4().hex},
    )
    for update in invalid_values:
        values = {
            "project_id": scope.project_id,
            "chapter_id": scope.chapter_id,
            "workflow_run_id": scope.workflow_run_id,
            "kind": scope.kind,
            "operation_key": scope.operation_key,
            "checkpoint_index": scope.checkpoint_index,
            "attempt_id": scope.attempt_id,
            **update,
        }
        with pytest.raises(contracts.ChapterProductionV2ValidationError) as error:
            module.ProviderAttemptScope(**values)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        assert "PRIVATE" not in repr(error.value) + str(error.value)


@pytest.mark.parametrize(
    "field",
    ("project_id", "chapter_id", "workflow_run_id", "attempt_id"),
)
def test_scope_rejects_hostile_uuid_subclasses_without_using_overrides(field: str) -> None:
    contracts = importlib.import_module("app.services.provider_attempt_contracts")
    module = _module()
    calls = 0

    class HostileUUID(type(uuid4())):
        def __str__(self) -> str:
            nonlocal calls
            calls += 1
            raise RuntimeError("PRIVATE string")

        def __eq__(self, _: object) -> bool:
            nonlocal calls
            calls += 1
            return True

        __hash__ = type(uuid4()).__hash__

    hostile = HostileUUID(uuid4().hex)

    def hostile_int(_: object) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("PRIVATE int")

    HostileUUID.int = property(hostile_int)
    values = {
        "project_id": uuid4(),
        "chapter_id": uuid4(),
        "workflow_run_id": uuid4(),
        "kind": contracts.ProviderAttemptKind.INITIAL,
        "operation_key": "a" * 64,
        "checkpoint_index": 0,
        "attempt_id": uuid4(),
        field: hostile,
    }

    with pytest.raises(contracts.ChapterProductionV2ValidationError) as error:
        module.ProviderAttemptScope(**values)
    assert calls == 0
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
