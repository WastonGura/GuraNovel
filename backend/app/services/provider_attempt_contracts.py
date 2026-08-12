"""Pure mechanical contracts for Chapter Production V2 provider attempts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)


CONTRACT_VERSION = "chapter-production-v2"
_FIELDS = {
    "attempt_id",
    "key",
    "kind",
    "checkpoint_index",
    "source_document_id",
    "source_version_id",
    "action_request_id",
    "target_segment_ids",
    "feedback_hash",
    "report_ids",
    "report_input_hash",
    "status",
}


class ProviderAttemptKind(str, Enum):
    INITIAL = "initial"
    FEEDBACK = "feedback"
    CORRECTIVE_REVISION = "review"


class ProviderAttemptStatus(str, Enum):
    CLAIMED = "claimed"
    FAILED = "failed"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _valid_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _uuid(value: object) -> UUID | None:
    if type(value) is not str or len(value) > 36:
        return None
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None
    return parsed if parsed.int != 0 and str(parsed) == value else None


def _uuid_tuple(value: object, *, limit: int) -> tuple[UUID, ...] | None:
    if type(value) is not list or len(value) > limit:
        return None
    parsed = tuple(_uuid(item) for item in value)
    if any(item is None for item in parsed):
        return None
    result = tuple(item for item in parsed if item is not None)
    return result if len(result) == len(set(result)) else None


def _nonzero_uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _valid_references(values: tuple[UUID, ...], *, limit: int) -> bool:
    return (
        type(values) is tuple
        and 0 < len(values) <= limit
        and all(_nonzero_uuid(value) for value in values)
        and len(values) == len(set(values))
    )


@dataclass(frozen=True, slots=True, repr=False)
class ProviderAttempt:
    attempt_id: UUID
    operation_key: str
    kind: ProviderAttemptKind
    checkpoint_index: int
    source_document_id: UUID | None = field(default=None, repr=False)
    source_version_id: UUID | None = field(default=None, repr=False)
    action_request_id: UUID | None = field(default=None, repr=False)
    target_segment_ids: tuple[UUID, ...] = field(default=(), repr=False)
    feedback_hash: str | None = field(default=None, repr=False)
    report_ids: tuple[UUID, ...] = field(default=(), repr=False)
    report_input_hash: str | None = field(default=None, repr=False)
    status: ProviderAttemptStatus = ProviderAttemptStatus.CLAIMED

    def __post_init__(self) -> None:
        if not self._is_valid():
            raise _invalid() from None

    def __repr__(self) -> str:
        return "ProviderAttempt()"

    def _is_valid(self) -> bool:
        if (
            not _nonzero_uuid(self.attempt_id)
            or not _valid_hash(self.operation_key)
            or type(self.kind) is not ProviderAttemptKind
            or type(self.status) is not ProviderAttemptStatus
            or type(self.checkpoint_index) is not int
            or self.checkpoint_index < 0
        ):
            return False
        if self.kind is ProviderAttemptKind.INITIAL:
            return self._initial_shape()
        if not (
            _nonzero_uuid(self.source_document_id)
            and _nonzero_uuid(self.source_version_id)
            and _valid_references(self.target_segment_ids, limit=64)
        ):
            return False
        if self.kind is ProviderAttemptKind.FEEDBACK:
            return self._feedback_shape()
        return self._corrective_shape()

    def _initial_shape(self) -> bool:
        return (
            self.source_document_id is None
            and self.source_version_id is None
            and self.action_request_id is None
            and self.target_segment_ids == ()
            and self.feedback_hash is None
            and self.report_ids == ()
            and self.report_input_hash is None
        )

    def _feedback_shape(self) -> bool:
        return (
            _nonzero_uuid(self.action_request_id)
            and _valid_hash(self.feedback_hash)
            and self.report_ids == ()
            and self.report_input_hash is None
        )

    def _corrective_shape(self) -> bool:
        return (
            self.action_request_id is None
            and self.feedback_hash is None
            and _valid_references(self.report_ids, limit=16)
            and _valid_hash(self.report_input_hash)
        )

    @classmethod
    def initial(
        cls, *, attempt_id: UUID, operation_key: str, checkpoint_index: int
    ) -> Self:
        return cls(attempt_id, operation_key, ProviderAttemptKind.INITIAL, checkpoint_index)

    @classmethod
    def feedback(
        cls,
        *,
        attempt_id: UUID,
        operation_key: str,
        checkpoint_index: int,
        source_document_id: UUID,
        source_version_id: UUID,
        action_request_id: UUID,
        target_segment_ids: tuple[UUID, ...],
        feedback_hash: str,
    ) -> Self:
        return cls(
            attempt_id,
            operation_key,
            ProviderAttemptKind.FEEDBACK,
            checkpoint_index,
            source_document_id,
            source_version_id,
            action_request_id,
            target_segment_ids,
            feedback_hash,
        )

    @classmethod
    def corrective_revision(
        cls,
        *,
        attempt_id: UUID,
        operation_key: str,
        checkpoint_index: int,
        source_document_id: UUID,
        source_version_id: UUID,
        target_segment_ids: tuple[UUID, ...],
        report_ids: tuple[UUID, ...],
        report_input_hash: str,
    ) -> Self:
        return cls(
            attempt_id=attempt_id,
            operation_key=operation_key,
            kind=ProviderAttemptKind.CORRECTIVE_REVISION,
            checkpoint_index=checkpoint_index,
            source_document_id=source_document_id,
            source_version_id=source_version_id,
            target_segment_ids=target_segment_ids,
            report_ids=report_ids,
            report_input_hash=report_input_hash,
        )

    def with_status(self, status: ProviderAttemptStatus) -> Self:
        if (
            type(status) is not ProviderAttemptStatus
            or (
                self.status is ProviderAttemptStatus.FAILED
                and status is ProviderAttemptStatus.CLAIMED
            )
        ):
            raise _invalid() from None
        return replace(self, status=status)

    def to_payload(self) -> dict[str, object]:
        return {
            "attempt_id": str(self.attempt_id),
            "key": self.operation_key,
            "kind": self.kind.value,
            "checkpoint_index": self.checkpoint_index,
            "source_document_id": (
                str(self.source_document_id) if self.source_document_id is not None else None
            ),
            "source_version_id": (
                str(self.source_version_id) if self.source_version_id is not None else None
            ),
            "action_request_id": (
                str(self.action_request_id) if self.action_request_id is not None else None
            ),
            "target_segment_ids": [str(item) for item in self.target_segment_ids],
            "feedback_hash": self.feedback_hash,
            "report_ids": [str(item) for item in self.report_ids],
            "report_input_hash": self.report_input_hash,
            "status": self.status.value,
        }

    @classmethod
    def from_payload(cls, payload: object) -> Self | None:
        if type(payload) is not dict:
            return None
        keys = tuple(payload)
        if any(type(key) is not str for key in keys) or set(keys) != _FIELDS:
            return None
        if type(payload.get("kind")) is not str or type(payload.get("status")) is not str:
            return None
        try:
            attempt_id = _uuid(payload.get("attempt_id"))
            kind = ProviderAttemptKind(payload.get("kind"))
            status = ProviderAttemptStatus(payload.get("status"))
            targets = _uuid_tuple(payload.get("target_segment_ids"), limit=64)
            reports = _uuid_tuple(payload.get("report_ids"), limit=16)
            optional_values = tuple(
                payload.get(field)
                for field in (
                    "source_document_id",
                    "source_version_id",
                    "action_request_id",
                )
            )
            optional_ids = tuple(_optional_uuid(value) for value in optional_values)
            if (
                attempt_id is None
                or targets is None
                or reports is None
                or any(
                    value is not None and parsed is None
                    for value, parsed in zip(optional_values, optional_ids, strict=True)
                )
            ):
                return None
            return cls(
                attempt_id=attempt_id,
                operation_key=payload.get("key"),
                kind=kind,
                checkpoint_index=payload.get("checkpoint_index"),
                source_document_id=optional_ids[0],
                source_version_id=optional_ids[1],
                action_request_id=optional_ids[2],
                target_segment_ids=targets,
                feedback_hash=payload.get("feedback_hash"),
                report_ids=reports,
                report_input_hash=payload.get("report_input_hash"),
                status=status,
            )
        except (TypeError, ValueError, ChapterProductionV2ValidationError):
            return None


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else _uuid(value)


def new_attempt_id() -> UUID:
    """Return the fresh UUID token that identifies one ABA generation."""

    return uuid4()


def same_generation(attempt: ProviderAttempt, attempt_id: UUID, operation_key: str) -> bool:
    return (
        type(attempt) is ProviderAttempt
        and type(attempt_id) is UUID
        and _valid_hash(operation_key)
        and attempt.attempt_id == attempt_id
        and attempt.operation_key == operation_key
    )


def initial_operation_key(
    *,
    project_id: UUID,
    chapter_id: UUID,
    outline_document_id: UUID,
    outline_version_id: UUID,
    outline_content_hash: str,
    segmenter_version: str,
) -> str:
    if (
        not all(
            _nonzero_uuid(value)
            for value in (
                project_id,
                chapter_id,
                outline_document_id,
                outline_version_id,
            )
        )
        or not _valid_hash(outline_content_hash)
        or type(segmenter_version) is not str
        or segmenter_version != "markdown-v1"
    ):
        raise _invalid() from None
    return _sha256(
        ":".join(
            (
                CONTRACT_VERSION,
                str(project_id),
                str(chapter_id),
                str(outline_document_id),
                str(outline_version_id),
                outline_content_hash,
                segmenter_version,
            )
        )
    )


def feedback_operation_key(
    *,
    workflow_run_id: UUID,
    action_request_id: UUID,
    source_version_id: UUID,
    target_segment_ids: tuple[UUID, ...],
    feedback_hash: str,
) -> str:
    if (
        not all(
            _nonzero_uuid(value)
            for value in (workflow_run_id, action_request_id, source_version_id)
        )
        or not _valid_references(target_segment_ids, limit=64)
        or not _valid_hash(feedback_hash)
    ):
        raise _invalid() from None
    return _sha256(
        ":".join(
            (
                CONTRACT_VERSION,
                str(workflow_run_id),
                str(action_request_id),
                str(source_version_id),
                ProviderAttemptKind.FEEDBACK.value,
                *(str(item) for item in target_segment_ids),
                feedback_hash,
            )
        )
    )


def corrective_revision_operation_key(
    *,
    workflow_run_id: UUID,
    source_version_id: UUID,
    report_ids: tuple[UUID, ...],
    target_segment_ids: tuple[UUID, ...],
    report_input_hash: str,
) -> str:
    if (
        not all(_nonzero_uuid(value) for value in (workflow_run_id, source_version_id))
        or not _valid_references(report_ids, limit=16)
        or not _valid_references(target_segment_ids, limit=64)
        or not _valid_hash(report_input_hash)
    ):
        raise _invalid() from None
    return _sha256(
        ":".join(
            (
                CONTRACT_VERSION,
                str(workflow_run_id),
                str(source_version_id),
                *(str(item) for item in report_ids),
                "targets",
                *(str(item) for item in target_segment_ids),
                "report-input",
                report_input_hash,
            )
        )
    )


__all__ = [
    "ProviderAttempt",
    "ProviderAttemptKind",
    "ProviderAttemptStatus",
    "corrective_revision_operation_key",
    "feedback_operation_key",
    "initial_operation_key",
    "new_attempt_id",
    "same_generation",
    "ChapterProductionV2ValidationError",
]
