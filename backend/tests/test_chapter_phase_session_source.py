"""Owned construction tests for chapter phase sessions."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


MODULE = Path(__file__).resolve().parents[1] / "app/services/chapter_phase_session_source.py"


def _module() -> object:
    return importlib.import_module("app.services.chapter_phase_session_source")


def _fixed(error: ChapterProductionV2ValidationError) -> None:
    assert type(error) is ChapterProductionV2ValidationError
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.details is None
    assert "PRIVATE" not in str(error)
    assert "PRIVATE" not in repr(error)


@pytest.mark.anyio
async def test_exact_engine_creates_distinct_frozen_repr_empty_pairs() -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    source = module.ChapterPhaseSessionSource(engine)
    first = source.create()
    second = source.create()
    try:
        assert type(first.session) is AsyncSession
        assert type(first.sync_session) is Session
        assert first.engine is engine
        assert first.sync_engine is engine.sync_engine
        assert second.engine is engine
        assert second.sync_engine is engine.sync_engine
        assert first.session is not second.session
        assert first.sync_session is not second.sync_session
        assert first.session.sync_session is first.sync_session
        assert second.session.sync_session is second.sync_session
        assert first.session.bind is engine
        assert first.sync_session.bind is engine.sync_engine
        assert first.sync_session.autoflush is True
        assert first.sync_session.expire_on_commit is False
        assert first.sync_session.autobegin is True
        assert first.sync_session.twophase is False
        assert first.sync_session.join_transaction_mode == "conditional_savepoint"
        assert first.sync_session.__dict__["_Session__binds"] == {}
        assert repr(first) == "_ChapterPhaseSessionSourceResult()"
        assert repr(source) == "ChapterPhaseSessionSource()"
        with pytest.raises((AttributeError, TypeError)):
            first.engine = object()
        with pytest.raises((AttributeError, TypeError)):
            source._maker = async_sessionmaker(engine)
    finally:
        await first.session.close()
        await second.session.close()
        await engine.dispose()


@pytest.mark.anyio
async def test_rejects_session_connection_factory_and_arbitrary_inputs() -> None:
    module = _module()
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/unused")
    session = AsyncSession(engine, expire_on_commit=False)
    connection = engine.connect()
    maker = async_sessionmaker(engine, expire_on_commit=False)

    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("PRIVATE repr")

        def __call__(self) -> object:
            raise RuntimeError("PRIVATE call")

    try:
        for candidate in (
            session,
            session.sync_session,
            connection,
            maker,
            {object(): engine},
            Hostile(),
            None,
        ):
            with pytest.raises(ChapterProductionV2ValidationError) as raised:
                module.ChapterPhaseSessionSource(candidate)
            _fixed(raised.value)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.anyio
async def test_rejects_custom_async_and_sync_engine_classes_before_creation() -> None:
    module = _module()
    standard = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/standard")

    class CustomAsyncEngine(AsyncEngine):
        @property
        def sync_engine(self) -> Engine:
            raise RuntimeError("PRIVATE accessor")

    class CustomSyncEngine(Engine):
        pass

    custom_async = object.__new__(CustomAsyncEngine)
    custom_sync = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1/custom-sync")
    custom_sync.sync_engine.__class__ = CustomSyncEngine
    try:
        for candidate in (custom_async, custom_sync):
            with pytest.raises(ChapterProductionV2ValidationError) as raised:
                module.ChapterPhaseSessionSource(candidate)
            _fixed(raised.value)
    finally:
        custom_sync.sync_engine.__class__ = Engine
        await custom_sync.dispose()
        await standard.dispose()


def test_rejects_malformed_exact_engine_with_fixed_error() -> None:
    module = _module()
    malformed = object.__new__(AsyncEngine)

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        module.ChapterPhaseSessionSource(malformed)

    _fixed(raised.value)


def test_source_has_no_adoption_reset_or_lifecycle_api() -> None:
    module = _module()
    public = {
        name
        for name, value in inspect.getmembers(module.ChapterPhaseSessionSource)
        if not name.startswith("_") and callable(value)
    }
    parameters = tuple(inspect.signature(module.ChapterPhaseSessionSource).parameters)

    assert public == {"create"}
    assert parameters == ("engine",)
    for forbidden in (
        "adopt",
        "reset",
        "close",
        "lease",
        "session_factory",
        "maker",
    ):
        assert not hasattr(module.ChapterPhaseSessionSource, forbidden)


def test_source_module_is_one_way_side_effect_free_and_within_budget() -> None:
    assert MODULE.exists()
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for prefix in (
        "app.agents",
        "app.models",
        "app.services.chapter_production_v2_service",
        "app.services.chapter_production_repository",
        "app.services.chapter_phase_session_invariants",
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
        "close",
        "commit",
        "rollback",
        "flush",
        "expire",
        "add",
        "delete",
        "merge",
    } & calls
    assert len(source.splitlines()) <= 180
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 120
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 50
