"""Cancellation-safe lifecycle for application-owned chapter phase sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.identity import WeakInstanceDict

from app.services.chapter_phase_session_invariants import validate_source_session
from app.services.chapter_phase_session_source import (
    ChapterPhaseSessionSource,
    _ChapterPhaseSessionSourceResult,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _raise_cancelled() -> None:
    error = asyncio.CancelledError()
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


def _contains_identity(values: list[object], candidate: object) -> bool:
    return any(value is candidate for value in values)


@dataclass(frozen=True, slots=True)
class _OwnedCandidate:
    result: _ChapterPhaseSessionSourceResult = field(repr=False)
    session: AsyncSession = field(repr=False)
    sync_session: Session = field(repr=False)
    identity_map: WeakInstanceDict = field(repr=False)


@dataclass(slots=True, init=False, repr=False)
class ChapterPhaseSessionLease:
    """Yield each fresh source-owned session once and close it exactly once."""

    _source: ChapterPhaseSessionSource
    _sessions: list[object]
    _sync_sessions: list[object]
    _identity_maps: list[object]

    def __init__(self, source: object) -> None:
        if type(source) is not ChapterPhaseSessionSource:
            raise _invalid() from None
        self._source = source
        self._sessions = []
        self._sync_sessions = []
        self._identity_maps = []

    def _claim(self, result: object) -> _OwnedCandidate | None:
        if type(result) is not _ChapterPhaseSessionSourceResult:
            return None
        session = result.session
        sync_session = result.sync_session
        if type(session) is not AsyncSession or type(sync_session) is not Session:
            return None
        values = session.__dict__
        sync_values = sync_session.__dict__
        identity_map = sync_values.get("identity_map")
        if (
            values.get("sync_session") is not sync_session
            or values.get("_proxied") is not sync_session
            or type(identity_map) is not WeakInstanceDict
        ):
            return None
        if (
            _contains_identity(self._sessions, session)
            or _contains_identity(self._sync_sessions, sync_session)
            or _contains_identity(self._identity_maps, identity_map)
        ):
            return None
        self._sessions.append(session)
        self._sync_sessions.append(sync_session)
        self._identity_maps.append(identity_map)
        return _OwnedCandidate(result, session, sync_session, identity_map)

    async def _close(self, session: AsyncSession) -> str:
        outcome = "closed"
        try:
            await session.close()
        except asyncio.CancelledError:
            outcome = "cancelled"
        except Exception:
            outcome = "failed"
        return outcome

    def _create(self) -> tuple[_OwnedCandidate | None, str]:
        candidate: _OwnedCandidate | None = None
        outcome = "created"
        try:
            result = self._source.create()
            candidate = self._claim(result)
            if candidate is None:
                outcome = "failed"
        except asyncio.CancelledError:
            outcome = "cancelled"
        except Exception:
            outcome = "failed"
        return candidate, outcome

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[AsyncSession]:
        """Yield one validated candidate, then close it with fixed precedence."""

        candidate, create_outcome = self._create()
        if create_outcome == "cancelled":
            _raise_cancelled()
        if create_outcome != "created" or candidate is None:
            raise _invalid() from None

        validation_outcome = "validated"
        validated: object | None = None
        try:
            validated = validate_source_session(candidate.result)
        except asyncio.CancelledError:
            validation_outcome = "cancelled"
        except Exception:
            validation_outcome = "failed"
        if validation_outcome != "validated" or validated is None:
            close_outcome = await self._close(candidate.session)
            if validation_outcome == "cancelled" or close_outcome == "cancelled":
                _raise_cancelled()
            raise _invalid() from None

        body_error: BaseException | None = None
        try:
            yield candidate.session
        except BaseException as error:
            body_error = error
        close_outcome = await self._close(candidate.session)
        if close_outcome == "cancelled" or isinstance(body_error, asyncio.CancelledError):
            _raise_cancelled()
        if body_error is not None:
            raise body_error from None
        if close_outcome == "failed":
            raise _invalid() from None

__all__ = ["ChapterPhaseSessionLease"]
