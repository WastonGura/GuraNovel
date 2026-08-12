"""Scope-bound persistence for Chapter Production V2 provider attempts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkflowCheckpoint, WorkflowRun, WorkflowType
from app.services.chapter_production_repository import ChapterProductionRepository
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.services.provider_attempt_contracts import (
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderAttemptStatus,
)


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _valid_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconciliation() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


@dataclass(frozen=True, slots=True, repr=False)
class ProviderAttemptScope:
    """Exact durable identity for one attempt generation."""

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    kind: ProviderAttemptKind
    operation_key: str
    checkpoint_index: int
    attempt_id: UUID

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_uuid(value)
                for value in (self.project_id, self.chapter_id, self.workflow_run_id)
            )
            or type(self.kind) is not ProviderAttemptKind
            or not _valid_hash(self.operation_key)
            or type(self.checkpoint_index) is not int
            or self.checkpoint_index < 0
            or not _valid_uuid(self.attempt_id)
        ):
            raise _invalid() from None

    def __repr__(self) -> str:
        return "ProviderAttemptScope()"


class ProviderAttemptStore:
    """Locked attempt mutations inside a caller-owned transaction."""

    def __init__(
        self, session: AsyncSession, repository: ChapterProductionRepository
    ) -> None:
        if (
            type(session) is not AsyncSession
            or type(repository) is not ChapterProductionRepository
            or repository.session is not session
        ):
            raise _invalid() from None
        self._session = session
        self._repository = repository

    async def claim(
        self, scope: ProviderAttemptScope, attempt: ProviderAttempt
    ) -> ProviderAttempt:
        self._validate_claim(scope, attempt)
        run, checkpoint_indices = await self._locked_context(scope)
        self._require_latest(checkpoint_indices, scope.checkpoint_index)
        await self._require_no_collision(scope)
        current = self._current_or_reconcile(run)
        if current == attempt:
            return attempt
        if current is not None:
            raise _reconciliation() from None
        self._write(run, attempt)
        return attempt

    async def mark_failed(self, scope: ProviderAttemptScope) -> ProviderAttempt | None:
        run, checkpoint_indices = await self._locked_context(scope)
        await self._require_no_collision(scope)
        current = self._current_or_reconcile(run)
        if current is None or not self._matches(current, scope):
            return None
        if current.status is ProviderAttemptStatus.FAILED:
            if checkpoint_indices not in {
                (scope.checkpoint_index,),
                (scope.checkpoint_index + 1, scope.checkpoint_index),
            }:
                raise _reconciliation() from None
            return current
        self._require_latest(checkpoint_indices, scope.checkpoint_index)
        failed = current.with_status(ProviderAttemptStatus.FAILED)
        self._write(run, failed)
        return failed

    async def release(self, scope: ProviderAttemptScope) -> bool:
        run, checkpoint_indices = await self._locked_context(scope)
        await self._require_no_collision(scope)
        current = self._current_or_reconcile(run)
        if current is None or not self._matches(current, scope):
            return False
        self._require_latest(checkpoint_indices, scope.checkpoint_index)
        if current.status is not ProviderAttemptStatus.CLAIMED:
            return False
        self._write(run, None)
        return True

    async def recover_failed(self, scope: ProviderAttemptScope) -> bool:
        run, checkpoint_indices = await self._locked_context(scope)
        await self._require_no_collision(scope)
        current = self._current_or_reconcile(run)
        if current is None or not self._matches(current, scope):
            return False
        if checkpoint_indices != (
            scope.checkpoint_index + 1,
            scope.checkpoint_index,
        ):
            raise _reconciliation() from None
        if current.status is not ProviderAttemptStatus.FAILED:
            raise _reconciliation() from None
        self._write(run, None)
        return True

    async def acknowledge_no_write(self, scope: ProviderAttemptScope) -> bool:
        run, checkpoint_indices = await self._locked_context(scope)
        await self._require_no_collision(scope)
        current = self._current_or_reconcile(run)
        if (
            current is None
            or not self._matches(current, scope)
            or current.status is not ProviderAttemptStatus.CLAIMED
        ):
            raise _reconciliation() from None
        self._require_latest(checkpoint_indices, scope.checkpoint_index)
        self._write(run, None)
        return True

    async def _locked_context(
        self, scope: ProviderAttemptScope
    ) -> tuple[WorkflowRun, tuple[int, ...]]:
        self._validate_scope(scope)
        failed = False
        try:
            await self._repository.chapter(scope.project_id, scope.chapter_id, lock=True)
            lock_keys = (
                f"provider-attempt-key:{scope.operation_key}",
                f"provider-attempt-token:{scope.attempt_id}",
            )
            for key in lock_keys:
                await self._session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": key},
                )
            run = await self._repository.run(
                scope.project_id,
                scope.chapter_id,
                scope.workflow_run_id,
                lock=True,
            )
            checkpoint_indices = await self._latest_checkpoint_indices(
                scope.workflow_run_id
            )
        except Exception:
            failed = True
        if failed:
            raise _reconciliation() from None
        return run, checkpoint_indices

    async def _latest_checkpoint_indices(self, run_id: UUID) -> tuple[int, ...]:
        checkpoints = list(
            await self._session.scalars(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == run_id)
                .order_by(WorkflowCheckpoint.checkpoint_index.desc())
                .limit(2)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        return tuple(checkpoint.checkpoint_index for checkpoint in checkpoints)

    async def _require_no_collision(self, scope: ProviderAttemptScope) -> None:
        token = str(scope.attempt_id)
        failed = False
        try:
            rows = list(
                await self._session.scalars(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.id != scope.workflow_run_id,
                        WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
                        or_(
                            WorkflowRun.metadata_["provider_attempt"]["attempt_id"].astext
                            == token,
                            WorkflowRun.metadata_["provider_attempt"]["key"].astext
                            == scope.operation_key,
                        ),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        except Exception:
            failed = True
            rows = []
        if failed or rows:
            raise _reconciliation() from None

    @staticmethod
    def _validate_scope(scope: ProviderAttemptScope) -> None:
        if type(scope) is not ProviderAttemptScope:
            raise _invalid() from None

    @staticmethod
    def _require_latest(actual: tuple[int, ...], expected: int) -> None:
        if not actual or actual[0] != expected:
            raise _reconciliation() from None

    @staticmethod
    def _validate_claim(scope: ProviderAttemptScope, attempt: ProviderAttempt) -> None:
        ProviderAttemptStore._validate_scope(scope)
        if (
            type(attempt) is not ProviderAttempt
            or attempt.status is not ProviderAttemptStatus.CLAIMED
            or not ProviderAttemptStore._matches(attempt, scope)
        ):
            raise _invalid() from None

    @staticmethod
    def _current_or_reconcile(run: WorkflowRun) -> ProviderAttempt | None:
        metadata = run.metadata_
        if type(metadata) is not dict or "provider_attempt" not in metadata:
            raise _reconciliation() from None
        payload = metadata["provider_attempt"]
        if payload is None:
            return None
        current = ProviderAttempt.from_payload(payload)
        if current is None:
            raise _reconciliation() from None
        return current

    @staticmethod
    def _matches(attempt: ProviderAttempt, scope: ProviderAttemptScope) -> bool:
        return (
            attempt.attempt_id == scope.attempt_id
            and attempt.kind is scope.kind
            and attempt.operation_key == scope.operation_key
            and attempt.checkpoint_index == scope.checkpoint_index
        )

    @staticmethod
    def _write(run: WorkflowRun, attempt: ProviderAttempt | None) -> None:
        metadata = dict(run.metadata_)
        metadata["provider_attempt"] = attempt.to_payload() if attempt is not None else None
        run.metadata_ = metadata


__all__ = ("ProviderAttemptScope", "ProviderAttemptStore")
