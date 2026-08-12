"""Fresh-state invariant tests for source-created phase sessions."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import make_transient_to_detached
from sqlalchemy.orm.session import _SessionCloseState

from app.models import User
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


MODULE = Path(__file__).resolve().parents[1] / "app/services/chapter_phase_session_invariants.py"


def _module() -> object:
    return importlib.import_module("app.services.chapter_phase_session_invariants")


def _fixed(error: ChapterProductionV2ValidationError) -> None:
    assert type(error) is ChapterProductionV2ValidationError
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.details is None
    assert "PRIVATE" not in str(error)
    assert "PRIVATE" not in repr(error)


def _persistent_user() -> User:
    user = User(id=uuid4(), username=f"user-{uuid4()}")
    make_transient_to_detached(user)
    return user


@pytest.mark.anyio
async def test_clean_source_result_returns_frozen_repr_empty_exact_facts() -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    result = ChapterPhaseSessionSource(engine).create()
    before = dict(result.sync_session.__dict__)
    try:
        validated = module.validate_source_session(result)
        assert validated.session is result.session
        assert validated.sync_session is result.sync_session
        assert validated.engine is result.engine
        assert validated.sync_engine is result.sync_engine
        assert repr(validated) == "_ValidatedChapterPhaseSession()"
        assert result.sync_session.__dict__ == before
        with pytest.raises((AttributeError, TypeError)):
            validated.engine = object()
    finally:
        await result.session.close()
        await engine.dispose()


@pytest.mark.anyio
async def test_rejects_bare_sessions_engines_factories_and_arbitrary_objects() -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    result = ChapterPhaseSessionSource(engine).create()

    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("PRIVATE repr")

    try:
        for candidate in (
            result.session,
            result.sync_session,
            result.engine,
            ChapterPhaseSessionSource(engine),
            Hostile(),
            None,
        ):
            with pytest.raises(ChapterProductionV2ValidationError) as raised:
                module.validate_source_session(candidate)
            _fixed(raised.value)
    finally:
        await result.session.close()
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field",
    ["session", "sync_session", "engine", "sync_engine"],
)
async def test_rejects_source_result_identity_and_engine_mismatch(field: str) -> None:
    module = _module()
    first_engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/first")
    second_engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/second")
    first = ChapterPhaseSessionSource(first_engine).create()
    second = ChapterPhaseSessionSource(second_engine).create()
    first_session = first.session
    replacement = {
        "session": second.session,
        "sync_session": second.sync_session,
        "engine": second.engine,
        "sync_engine": second.sync_engine,
    }[field]
    object.__setattr__(first, field, replacement)
    try:
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            module.validate_source_session(first)
        _fixed(raised.value)
    finally:
        await first_session.close()
        if second.session is not first_session:
            await second.session.close()
        await second_engine.dispose()
        await first_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("autoflush", False),
        ("expire_on_commit", True),
        ("autobegin", False),
        ("twophase", True),
        ("join_transaction_mode", "create_savepoint"),
        ("_close_state", _SessionCloseState.ACTIVE),
        ("_flushing", True),
    ],
)
async def test_rejects_each_frozen_session_configuration_drift(
    field: str, value: object
) -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    result = ChapterPhaseSessionSource(engine).create()
    setattr(result.sync_session, field, value)
    try:
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            module.validate_source_session(result)
        _fixed(raised.value)
    finally:
        await result.session.close()
        await engine.dispose()


@pytest.mark.anyio
async def test_rejects_active_transaction_without_mutating_it() -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    result = ChapterPhaseSessionSource(engine).create()
    transaction = result.sync_session.begin()
    try:
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            module.validate_source_session(result)
        _fixed(raised.value)
        assert result.sync_session.get_transaction() is transaction
        assert transaction.is_active is True
    finally:
        transaction.rollback()
        await result.session.close()
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["new", "dirty", "deleted", "identity_map"])
async def test_rejects_each_nonempty_unit_of_work_state(state: str) -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    result = ChapterPhaseSessionSource(engine).create()
    sync_session = result.sync_session
    user = _persistent_user()
    instance_state = sqlalchemy_inspect(user)
    if state == "new":
        transient = User(username=f"user-{uuid4()}")
        sync_session.__dict__["_new"][sqlalchemy_inspect(transient)] = transient
    else:
        sync_session.identity_map.add(instance_state)
        if state == "dirty":
            user.username = f"changed-{uuid4()}"
        elif state == "deleted":
            sync_session.__dict__["_deleted"][instance_state] = user
    try:
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            module.validate_source_session(result)
        _fixed(raised.value)
    finally:
        await result.session.close()
        await engine.dispose()


def test_validator_api_and_import_direction_stay_narrow_and_read_only() -> None:
    module = _module()
    assert tuple(inspect.signature(module.validate_source_session).parameters) == ("result",)
    assert module.__all__ == ["validate_source_session"]

    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert "app.services.chapter_phase_session_source" in imports
    for prefix in (
        "app.agents",
        "app.models",
        "app.services.chapter_production_v2_service",
        "app.services.chapter_production_repository",
        "app.services.chapter_phase_session_lease",
        "app.services.document_service",
        "app.workspace",
        "app.api",
        "langgraph",
        "pathlib",
        "os",
    ):
        assert not any(name == prefix or name.startswith(f"{prefix}.") for name in imports)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not {
        "create",
        "close",
        "commit",
        "rollback",
        "flush",
        "expire",
        "add",
        "delete",
        "merge",
    } & calls
    assert len(source.splitlines()) <= 160
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 120
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 50
