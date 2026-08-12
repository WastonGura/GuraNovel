"""Application-owned construction source for chapter phase sessions."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import Session

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


@dataclass(frozen=True, slots=True)
class _ChapterPhaseSessionSourceResult:
    """Fresh construction facts for the next invariant layer."""

    session: AsyncSession = field(repr=False)
    sync_session: Session = field(repr=False)
    engine: AsyncEngine = field(repr=False)
    sync_engine: Engine = field(repr=False)


@dataclass(frozen=True, slots=True, init=False)
class ChapterPhaseSessionSource:
    """Create fresh exact sessions from one application-owned async engine."""

    _engine: AsyncEngine = field(repr=False)
    _sync_engine: Engine = field(repr=False)
    _maker: async_sessionmaker[AsyncSession] = field(repr=False)

    def __init__(self, engine: object) -> None:
        sync_engine: Engine | None = None
        invalid = type(engine) is not AsyncEngine
        if not invalid:
            try:
                candidate = engine.sync_engine
                if type(candidate) is Engine:
                    sync_engine = candidate
                else:
                    invalid = True
            except Exception:
                invalid = True
        if invalid or sync_engine is None:
            raise _invalid() from None

        maker: async_sessionmaker[AsyncSession] | None = None
        failed = False
        try:
            maker = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                autoflush=True,
                autobegin=True,
                expire_on_commit=False,
                twophase=False,
                join_transaction_mode="conditional_savepoint",
                close_resets_only=True,
                sync_session_class=Session,
            )
        except Exception:
            failed = True
        if failed or maker is None:
            raise _invalid() from None

        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_sync_engine", sync_engine)
        object.__setattr__(self, "_maker", maker)

    def create(self) -> _ChapterPhaseSessionSourceResult:
        """Create one fresh pair without opening, inspecting, or closing it."""

        session: AsyncSession | None = None
        sync_session: Session | None = None
        failed = False
        try:
            candidate = self._maker()
            if type(candidate) is not AsyncSession:
                failed = True
            else:
                session = candidate
                sync_candidate = candidate.sync_session
                if type(sync_candidate) is not Session:
                    failed = True
                else:
                    sync_session = sync_candidate
        except Exception:
            failed = True
        if failed or session is None or sync_session is None:
            raise _invalid() from None
        return _ChapterPhaseSessionSourceResult(
            session=session,
            sync_session=sync_session,
            engine=self._engine,
            sync_engine=self._sync_engine,
        )


__all__ = ["ChapterPhaseSessionSource"]
