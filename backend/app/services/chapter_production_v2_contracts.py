"""Dependency-light contracts for Chapter Production V2 orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.core.errors import AppError


CONTRACT_VERSION = "chapter-production-v2"
REVIEWER_CLAIM_STATUS_CLAIMED = "claimed"
REVIEWER_CLAIM_STATUS_FAILED = "failed"
REVIEW_WARNING_ACTION_TYPE = "chapter_review_warning"
REVIEW_REVISION_ACTION_TYPE = "chapter_review_revision"


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def valid_nonzero_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.int != 0 and str(parsed) == value


def valid_sha256(value: object) -> bool:
    return _valid_sha256(value)


def new_attempt_id() -> str:
    """Create a content-free provider-attempt generation identifier."""

    return str(uuid4())


def safe_cancelled_error(_: BaseException) -> asyncio.CancelledError:
    """Return a cancellation signal that cannot disclose provider exception data."""

    return asyncio.CancelledError()


class ChapterProductionV2ValidationError(AppError):
    """A fixed, content-free failure at the V2 orchestration boundary."""

    code = "chapter_production_v2_invalid"
    default_message = "Chapter production input is invalid."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2ProviderError(AppError):
    """A provider failure whose text and causal chain are intentionally fixed."""

    status_code = 503
    code = "chapter_production_v2_provider_failed"
    default_message = "Chapter drafting failed safely."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2ReviewProviderError(AppError):
    """A fixed advisory-review provider failure with no untrusted causal data."""

    status_code = 503
    code = "chapter_production_v2_review_provider_failed"
    default_message = "Chapter review failed safely."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2CommitIndeterminateError(AppError):
    status_code = 500
    code = "chapter_production_v2_commit_indeterminate"
    default_message = "Chapter drafting requires reconciliation before retrying."

    def __init__(self) -> None:
        super().__init__()


class ChapterProductionV2ReconciliationError(AppError):
    status_code = 409
    code = "chapter_production_v2_reconciliation_required"
    default_message = "Chapter production requires explicit reconciliation."

    def __init__(self) -> None:
        super().__init__()


@dataclass(frozen=True, slots=True)
class ChapterProductionV2Started:
    workflow_run_id: UUID
    action_request_id: UUID
    outline_document_id: UUID
    outline_version_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID


@dataclass(frozen=True, slots=True)
class ChapterProductionV2Updated:
    workflow_run_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID
    action_request_id: UUID | None


@dataclass(frozen=True, slots=True)
class ChapterProductionV2Finalized:
    workflow_run_id: UUID
    final_document_id: UUID
    final_version_id: UUID


@dataclass(frozen=True, slots=True)
class ChapterDraftPhaseScope:
    """Pure scope shared by future draft phase snapshots."""

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    actor_user_id: UUID

    def __post_init__(self) -> None:
        if not all(
            _valid_uuid(value)
            for value in (
                self.project_id,
                self.chapter_id,
                self.workflow_run_id,
                self.actor_user_id,
            )
        ):
            raise ChapterProductionV2ValidationError() from None


@dataclass(frozen=True, slots=True)
class ChapterDraftSourceSnapshot:
    """Pure source values that cannot retain database authority."""

    scope: ChapterDraftPhaseScope
    checkpoint_index: int
    source_document_id: UUID
    source_version_id: UUID
    source_content_hash: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ChapterDraftPhaseScope
            or type(self.checkpoint_index) is not int
            or not 0 <= self.checkpoint_index <= 2_147_483_647
            or not _valid_uuid(self.source_document_id)
            or not _valid_uuid(self.source_version_id)
            or not _valid_sha256(self.source_content_hash)
            or type(self.content) is not str
        ):
            raise ChapterProductionV2ValidationError() from None


__all__ = [
    "ChapterDraftPhaseScope",
    "ChapterDraftSourceSnapshot",
    "ChapterProductionV2CommitIndeterminateError",
    "ChapterProductionV2Finalized",
    "ChapterProductionV2ProviderError",
    "ChapterProductionV2ReconciliationError",
    "ChapterProductionV2ReviewProviderError",
    "ChapterProductionV2Started",
    "ChapterProductionV2Updated",
    "ChapterProductionV2ValidationError",
]
