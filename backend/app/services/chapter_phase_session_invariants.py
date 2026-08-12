"""Pure fresh-state invariants for source-created chapter phase sessions."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.identity import WeakInstanceDict
from sqlalchemy.orm.session import _SessionCloseState

from app.services.chapter_phase_session_source import (
    _ChapterPhaseSessionSourceResult,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


@dataclass(frozen=True, slots=True)
class _ValidatedChapterPhaseSession:
    """Fresh source facts accepted by the future lease layer."""

    session: AsyncSession = field(repr=False)
    sync_session: Session = field(repr=False)
    engine: AsyncEngine = field(repr=False)
    sync_engine: Engine = field(repr=False)


def _identities_are_valid(result: _ChapterPhaseSessionSourceResult) -> bool:
    session = result.session
    sync_session = result.sync_session
    engine = result.engine
    sync_engine = result.sync_engine
    if (
        type(session) is not AsyncSession
        or type(sync_session) is not Session
        or type(engine) is not AsyncEngine
        or type(sync_engine) is not Engine
    ):
        return False
    async_values = session.__dict__
    sync_values = sync_session.__dict__
    binds = sync_values.get("_Session__binds")
    return (
        async_values.get("bind") is engine
        and async_values.get("sync_session") is sync_session
        and async_values.get("_proxied") is sync_session
        and engine.sync_engine is sync_engine
        and sync_values.get("bind") is sync_engine
        and type(binds) is dict
        and not binds
    )


def _configuration_is_valid(sync_session: Session) -> bool:
    values = sync_session.__dict__
    return (
        values.get("autoflush") is True
        and values.get("autobegin") is True
        and values.get("expire_on_commit") is False
        and values.get("twophase") is False
        and values.get("join_transaction_mode") == "conditional_savepoint"
        and values.get("_close_state") is _SessionCloseState.CLOSE_IS_RESET
        and values.get("_flushing") is False
    )


def _state_is_clean(sync_session: Session) -> bool:
    values = sync_session.__dict__
    new = values.get("_new")
    deleted = values.get("_deleted")
    identity_map = values.get("identity_map")
    return (
        values.get("_transaction") is None
        and values.get("_nested_transaction") is None
        and type(new) is dict
        and not new
        and type(deleted) is dict
        and not deleted
        and type(identity_map) is WeakInstanceDict
        and not identity_map
        and not identity_map._modified
    )


def _validated(
    result: _ChapterPhaseSessionSourceResult,
) -> _ValidatedChapterPhaseSession | None:
    if not _identities_are_valid(result):
        return None
    if not _configuration_is_valid(result.sync_session):
        return None
    if not _state_is_clean(result.sync_session):
        return None
    return _ValidatedChapterPhaseSession(
        session=result.session,
        sync_session=result.sync_session,
        engine=result.engine,
        sync_engine=result.sync_engine,
    )


def validate_source_session(result: object) -> _ValidatedChapterPhaseSession:
    """Validate only an exact source result without mutating its session pair."""

    validated: _ValidatedChapterPhaseSession | None = None
    failed = type(result) is not _ChapterPhaseSessionSourceResult
    if not failed:
        try:
            validated = _validated(result)
        except Exception:
            failed = True
    if failed or validated is None:
        raise _invalid() from None
    return validated


__all__ = ["validate_source_session"]
