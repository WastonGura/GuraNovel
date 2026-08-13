"""Pure identity for one claimed initial provider generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError
from app.services.provider_attempt_contracts import (
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderAttemptStatus,
)


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _valid_hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


@dataclass(frozen=True, slots=True, repr=False)
class InitialGenerationScope:
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    actor_user_id: UUID
    operation_key: str
    checkpoint_index: int
    attempt_id: UUID

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_uuid(value)
                for value in (
                    self.project_id, self.chapter_id, self.workflow_run_id,
                    self.actor_user_id, self.attempt_id,
                )
            )
            or not _valid_hash(self.operation_key)
            or type(self.checkpoint_index) is not int
            or self.checkpoint_index < 0
        ):
            raise ChapterProductionV2ValidationError() from None

    def __repr__(self) -> str:
        return "InitialGenerationScope()"


@dataclass(frozen=True, slots=True, repr=False)
class InitialGenerationSnapshot:
    scope: InitialGenerationScope = field(repr=False)
    attempt: ProviderAttempt = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not InitialGenerationScope
            or type(self.attempt) is not ProviderAttempt
            or self.attempt.kind is not ProviderAttemptKind.INITIAL
            or self.attempt.status is not ProviderAttemptStatus.CLAIMED
            or self.attempt.operation_key != self.scope.operation_key
            or self.attempt.checkpoint_index != self.scope.checkpoint_index
            or self.attempt.attempt_id != self.scope.attempt_id
        ):
            raise ChapterProductionV2ValidationError() from None

    def __repr__(self) -> str:
        return "InitialGenerationSnapshot()"


__all__ = ["InitialGenerationScope", "InitialGenerationSnapshot"]
