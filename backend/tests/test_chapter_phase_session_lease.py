"""Lifecycle tests for application-owned chapter phase session leases."""

from __future__ import annotations

import asyncio
import ast
import importlib
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.agents import DeterministicChapterWriterProvider, WriterAgent
from app.models import User
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service


SERVICES = Path(__file__).resolve().parents[1] / "app/services"
LEASE_MODULE = SERVICES / "chapter_phase_session_lease.py"
SERVICE_MODULE = SERVICES / "chapter_production_v2_service.py"
URL = "postgresql+asyncpg://unused:unused@127.0.0.1/unused"


def _module() -> object:
    return importlib.import_module("app.services.chapter_phase_session_lease")


def _fixed(error: ChapterProductionV2ValidationError) -> None:
    assert type(error) is ChapterProductionV2ValidationError
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.details is None
    assert "PRIVATE" not in str(error)
    assert "PRIVATE" not in repr(error)


def _cancelled(error: asyncio.CancelledError) -> None:
    assert type(error) is asyncio.CancelledError
    assert error.args == ()
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.anyio
async def test_fresh_leases_are_distinct_closed_and_preserve_caller_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    caller = AsyncSession(engine, expire_on_commit=False)
    closed: list[AsyncSession] = []
    original_close = AsyncSession.close

    async def tracked_close(session: AsyncSession) -> None:
        closed.append(session)
        await original_close(session)

    monkeypatch.setattr(AsyncSession, "close", tracked_close)
    await caller.begin()
    caller.add(User(username="caller-pending"))
    lease = module.ChapterPhaseSessionLease(source)
    try:
        async with lease.lease() as first:
            assert type(first) is AsyncSession
            assert not first.in_transaction()
            assert closed == []
        async with lease.lease() as second:
            assert type(second) is AsyncSession
            assert second is not first
            assert not second.in_transaction()
        assert closed == [first, second]
        assert caller.in_transaction()
        assert len(caller.new) == 1
    finally:
        await original_close(caller)
        await engine.dispose()


@pytest.mark.anyio
async def test_reused_exact_candidate_is_rejected_without_second_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    result = source.create()
    close_calls = 0
    original_close = AsyncSession.close

    def reused(_: ChapterPhaseSessionSource) -> object:
        return result

    async def tracked_close(session: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        await original_close(session)

    monkeypatch.setattr(ChapterPhaseSessionSource, "create", reused)
    monkeypatch.setattr(AsyncSession, "close", tracked_close)
    lease = module.ChapterPhaseSessionLease(source)
    try:
        async with lease.lease() as yielded:
            assert yielded is result.session
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            async with lease.lease():
                pass
        _fixed(raised.value)
        assert close_calls == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("alias", ["sync_session", "identity_map"])
async def test_new_async_candidate_alias_is_rejected_without_closing_either_owner(
    monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    first, second = source.create(), source.create()
    original_sync = second.sync_session
    original_map = second.sync_session.identity_map
    if alias == "sync_session":
        second.session.sync_session = first.sync_session
        second.session._proxied = first.sync_session
        object.__setattr__(second, "sync_session", first.sync_session)
    else:
        second.sync_session.identity_map = first.sync_session.identity_map
    results: Iterator[object] = iter((first, second))
    close_calls: list[AsyncSession] = []
    original_close = AsyncSession.close

    monkeypatch.setattr(ChapterPhaseSessionSource, "create", lambda _: next(results))

    async def tracked_close(session: AsyncSession) -> None:
        close_calls.append(session)
        await original_close(session)

    monkeypatch.setattr(AsyncSession, "close", tracked_close)
    lease = module.ChapterPhaseSessionLease(source)
    try:
        async with lease.lease():
            with pytest.raises(ChapterProductionV2ValidationError) as raised:
                async with lease.lease():
                    pass
            _fixed(raised.value)
            assert close_calls == []
        assert close_calls == [first.session]
    finally:
        second.session.sync_session = original_sync
        second.session._proxied = original_sync
        object.__setattr__(second, "sync_session", original_sync)
        second.sync_session.identity_map = original_map
        await original_close(second.session)
        await engine.dispose()


@pytest.mark.anyio
async def test_invalid_independently_owned_candidate_is_closed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    result = source.create()
    result.sync_session.begin()
    close_calls = 0
    original_close = AsyncSession.close
    monkeypatch.setattr(ChapterPhaseSessionSource, "create", lambda _: result)

    async def tracked_close(session: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        await original_close(session)

    monkeypatch.setattr(AsyncSession, "close", tracked_close)
    try:
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            async with module.ChapterPhaseSessionLease(source).lease():
                pass
        _fixed(raised.value)
        assert close_calls == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["exception", "cancel"])
async def test_source_failure_is_fixed_or_sanitized_cancellation(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)

    def fail(_: ChapterPhaseSessionSource) -> object:
        if failure == "cancel":
            raise asyncio.CancelledError("PRIVATE source cancellation")
        raise RuntimeError("PRIVATE source failure")

    monkeypatch.setattr(ChapterPhaseSessionSource, "create", fail)
    try:
        expected = asyncio.CancelledError if failure == "cancel" else ChapterProductionV2ValidationError
        with pytest.raises(expected) as raised:
            async with module.ChapterPhaseSessionLease(source).lease():
                pass
        if failure == "cancel":
            _cancelled(raised.value)
        else:
            _fixed(raised.value)
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["exception", "cancel"])
async def test_validator_failure_closes_owned_candidate_and_hides_private_details(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    close_calls = 0
    original_close = AsyncSession.close

    def fail(_: object) -> object:
        if failure == "cancel":
            raise asyncio.CancelledError("PRIVATE validator cancellation")
        raise RuntimeError("PRIVATE validator failure")

    async def tracked_close(session: AsyncSession) -> None:
        nonlocal close_calls
        close_calls += 1
        await original_close(session)

    monkeypatch.setattr(module, "validate_source_session", fail)
    monkeypatch.setattr(AsyncSession, "close", tracked_close)
    try:
        expected = asyncio.CancelledError if failure == "cancel" else ChapterProductionV2ValidationError
        with pytest.raises(expected) as raised:
            async with module.ChapterPhaseSessionLease(source).lease():
                pass
        assert close_calls == 1
        if failure == "cancel":
            _cancelled(raised.value)
        else:
            _fixed(raised.value)
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("close_failure", ["exception", "cancel"])
async def test_validator_failure_preserves_close_failure_precedence(
    monkeypatch: pytest.MonkeyPatch, close_failure: str
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)

    def invalid(_: object) -> object:
        raise RuntimeError("PRIVATE validator failure")

    async def close(_: AsyncSession) -> None:
        if close_failure == "cancel":
            raise asyncio.CancelledError("PRIVATE close cancellation")
        raise RuntimeError("PRIVATE close failure")

    monkeypatch.setattr(module, "validate_source_session", invalid)
    monkeypatch.setattr(AsyncSession, "close", close)
    try:
        expected = (
            asyncio.CancelledError
            if close_failure == "cancel"
            else ChapterProductionV2ValidationError
        )
        with pytest.raises(expected) as raised:
            async with module.ChapterPhaseSessionLease(source).lease():
                pass
        if close_failure == "cancel":
            _cancelled(raised.value)
        else:
            _fixed(raised.value)
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body_failure", "close_failure", "expected"),
    [
        ("none", "exception", "fixed"),
        ("exception", "exception", "body"),
        ("cancel", "exception", "cancel"),
        ("none", "cancel", "cancel"),
        ("exception", "cancel", "cancel"),
        ("cancel", "cancel", "cancel"),
        ("cancel", "none", "cancel"),
    ],
)
async def test_body_and_close_failure_precedence(
    monkeypatch: pytest.MonkeyPatch,
    body_failure: str,
    close_failure: str,
    expected: str,
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    original_close = AsyncSession.close
    body_error = RuntimeError("body failure")

    async def close(session: AsyncSession) -> None:
        if close_failure == "cancel":
            raise asyncio.CancelledError("PRIVATE close cancellation")
        if close_failure == "exception":
            raise RuntimeError("PRIVATE close failure")
        await original_close(session)

    monkeypatch.setattr(AsyncSession, "close", close)
    yielded: AsyncSession | None = None
    try:
        error_type = {
            "fixed": ChapterProductionV2ValidationError,
            "body": RuntimeError,
            "cancel": asyncio.CancelledError,
        }[expected]
        with pytest.raises(error_type) as raised:
            async with module.ChapterPhaseSessionLease(source).lease() as yielded:
                if body_failure == "exception":
                    raise body_error
                if body_failure == "cancel":
                    raise asyncio.CancelledError("PRIVATE body cancellation")
        if expected == "fixed":
            _fixed(raised.value)
        elif expected == "cancel":
            _cancelled(raised.value)
        else:
            assert raised.value is body_error
    finally:
        if yielded is not None:
            await original_close(yielded)
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("close_failure", ["none", "exception", "cancel"])
async def test_body_cancelled_subclass_is_always_sanitized(
    monkeypatch: pytest.MonkeyPatch, close_failure: str
) -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    original_close = AsyncSession.close

    class PrivateCancellation(asyncio.CancelledError):
        pass

    async def close(session: AsyncSession) -> None:
        if close_failure == "cancel":
            raise asyncio.CancelledError("PRIVATE close cancellation")
        if close_failure == "exception":
            raise RuntimeError("PRIVATE close failure")
        await original_close(session)

    monkeypatch.setattr(AsyncSession, "close", close)
    yielded: AsyncSession | None = None
    try:
        with pytest.raises(asyncio.CancelledError) as raised:
            async with module.ChapterPhaseSessionLease(source).lease() as yielded:
                try:
                    raise RuntimeError("PRIVATE prior context")
                except RuntimeError:
                    raise PrivateCancellation("PRIVATE body cancellation")
        _cancelled(raised.value)
    finally:
        if yielded is not None:
            await original_close(yielded)
        await engine.dispose()


def test_facade_constructs_one_lazy_lease_without_using_it() -> None:
    module = _module()
    engine = create_async_engine(URL)
    source = ChapterPhaseSessionSource(engine)
    calls = 0
    original_create = ChapterPhaseSessionSource.create

    def tracked_create(value: ChapterPhaseSessionSource) -> object:
        nonlocal calls
        calls += 1
        return original_create(value)

    ChapterPhaseSessionSource.create = tracked_create
    try:
        service = ChapterProductionV2Service(
            object(),  # type: ignore[arg-type]
            writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
            phase_session_source=source,
        )
        assert isinstance(service._phase_sessions, module.ChapterPhaseSessionLease)
        assert service._phase_sessions is service._phase_sessions
        assert calls == 0
    finally:
        ChapterPhaseSessionSource.create = original_create


def test_lease_boundary_is_one_way_read_only_and_within_budget() -> None:
    assert LEASE_MODULE.exists()
    source = LEASE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert "app.services.chapter_phase_session_source" in imports
    assert "app.services.chapter_phase_session_invariants" in imports
    for prefix in (
        "app.agents",
        "app.models",
        "app.services.chapter_production_v2_service",
        "app.services.chapter_production_repository",
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
    assert not {"commit", "rollback", "flush", "expire", "add", "delete", "merge"} & calls
    assert len(source.splitlines()) <= 260
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 180
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 70


def test_facade_hook_is_optional_stable_and_has_no_business_lease_use() -> None:
    parameters = inspect.signature(ChapterProductionV2Service).parameters
    assert parameters["phase_session_source"].default is None
    source = SERVICE_MODULE.read_text(encoding="utf-8")
    assert source.count("ChapterPhaseSessionLease(") == 1
    assert ".lease(" not in source
