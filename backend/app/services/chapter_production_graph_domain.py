"""Content-free async scheduling seam over the stable Chapter Production V2 facade."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import (
    ChapterFailureCode,
    ChapterProductionStatus,
)

_CURSOR_BY_STATUS = {
    ChapterProductionStatus.DRAFTING: "draft",
    ChapterProductionStatus.AUTHOR_REVISION: "author_revision",
    ChapterProductionStatus.EDITOR_REVIEW: "editor_review",
    ChapterProductionStatus.REVIEW_REVISION: "corrective_revision",
    ChapterProductionStatus.CHIEF_FINAL_REVIEW: "chief_editor_review",
    ChapterProductionStatus.LORE_FINAL_REVIEW: "lore_review",
    ChapterProductionStatus.REVISION_READY: "mark_revision_ready",
    ChapterProductionStatus.ARCHIVE_UPDATE: "finalize",
    ChapterProductionStatus.FAILED: "reconcile",
}
_CURSORS = frozenset(
    {
        "reconstruct",
        "draft",
        "await_author_action",
        "author_revision",
        "editor_review",
        "chief_editor_review",
        "lore_review",
        "corrective_revision",
        "mark_revision_ready",
        "finalize",
        "reconcile",
    }
)
_REVIEW_CURSORS = frozenset(
    {"editor_review", "chief_editor_review", "lore_review"}
)
_RECONCILIATION_FAILURES = frozenset(
    {
        ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
        ChapterFailureCode.RECONCILIATION_REQUIRED,
    }
)
_RETRYABLE_FAILURES = frozenset(
    {
        ChapterFailureCode.PROVIDER_UNAVAILABLE.value,
        ChapterFailureCode.PROVIDER_TIMEOUT.value,
        ChapterFailureCode.INVALID_PROVIDER_OUTPUT.value,
        ChapterFailureCode.PERSISTENCE_UNAVAILABLE.value,
        ChapterFailureCode.ARCHIVE_UNAVAILABLE.value,
    }
)
_RECONCILIATION_FAILURE_CODES = frozenset(
    failure.value for failure in _RECONCILIATION_FAILURES
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _canonical_uuid(value: object) -> UUID:
    parsed: UUID | None = None
    try:
        if isinstance(value, (str, bytes)):
            raise ValueError
        raw_int = value.int  # type: ignore[attr-defined]
        raw_text = str(value)
        parsed = UUID(raw_text)
        if (
            type(raw_int) is not int
            or raw_int == 0
            or parsed.int != raw_int
            or str(parsed) != raw_text
        ):
            parsed = None
            raise ValueError
    except Exception:
        pass
    if parsed is None:
        raise _invalid() from None
    return parsed


@dataclass(frozen=True, slots=True)
class ChapterProductionInvocationContext:
    """Server-composed authority kept outside graph state and checkpoints."""

    project_id: UUID
    chapter_id: UUID
    actor_user_id: UUID

    def __post_init__(self) -> None:
        for field in ("project_id", "chapter_id", "actor_user_id"):
            object.__setattr__(self, field, _canonical_uuid(getattr(self, field)))


@dataclass(frozen=True, slots=True)
class ChapterProductionSchedulingResult:
    """Closed mechanical result for a future async graph-node adapter."""

    kind: str
    next_cursor: str | None = None
    action_request_id: UUID | None = None
    failure_code: str | None = None
    completion_code: str | None = None

    def __post_init__(self) -> None:
        fields = (
            self.next_cursor,
            self.action_request_id,
            self.failure_code,
            self.completion_code,
        )
        valid = {
            "continue": self.next_cursor in _CURSORS and fields[1:] == (None, None, None),
            "await-user": (
                type(self.action_request_id) is UUID
                and self.action_request_id.int != 0
                and fields[0] is None
                and fields[2:] == (None, None)
            ),
            "retryable-failure": (
                self.failure_code in _RETRYABLE_FAILURES
                and fields[:2] == (None, None)
                and fields[3] is None
            ),
            "reconciliation-required": (
                self.failure_code in _RECONCILIATION_FAILURE_CODES
                and fields[:2] == (None, None)
                and fields[3] is None
            ),
            "cancelled": fields == (None, None, None, None),
            "complete": fields == (None, None, None, "success"),
        }.get(self.kind, False)
        if not valid:
            raise _invalid() from None


def _continue(cursor: str) -> ChapterProductionSchedulingResult:
    return ChapterProductionSchedulingResult(kind="continue", next_cursor=cursor)


def _result_for_state(state: object) -> ChapterProductionSchedulingResult:
    status = getattr(state, "status", None)
    if getattr(state, "awaiting_user", None) is True:
        try:
            action_id = UUID(getattr(state, "action_request_id"))
        except (AttributeError, TypeError, ValueError):
            raise ChapterProductionV2ReconciliationError() from None
        if action_id.int == 0:
            raise ChapterProductionV2ReconciliationError() from None
        return ChapterProductionSchedulingResult(
            kind="await-user", action_request_id=action_id
        )
    if status is ChapterProductionStatus.COMPLETED:
        return ChapterProductionSchedulingResult(
            kind="complete", completion_code="success"
        )
    if status is ChapterProductionStatus.CANCELLED:
        return ChapterProductionSchedulingResult(kind="cancelled")
    if status is ChapterProductionStatus.FAILED:
        failure = getattr(state, "failure_code", None)
        if not isinstance(failure, ChapterFailureCode):
            raise ChapterProductionV2ReconciliationError() from None
        return ChapterProductionSchedulingResult(
            kind=(
                "reconciliation-required"
                if failure in _RECONCILIATION_FAILURES
                else "retryable-failure"
            ),
            failure_code=failure.value,
        )
    try:
        return _continue(_CURSOR_BY_STATUS[status])
    except (KeyError, TypeError):
        raise ChapterProductionV2ReconciliationError() from None


def _scope(
    context: ChapterProductionInvocationContext, workflow_run_id: UUID
) -> dict[str, UUID]:
    if type(context) is not ChapterProductionInvocationContext:
        raise _invalid() from None
    return {
        "project_id": context.project_id,
        "chapter_id": context.chapter_id,
        "workflow_run_id": _canonical_uuid(workflow_run_id),
        "actor_user_id": context.actor_user_id,
    }


async def advance_chapter_production(
    service: object,
    *,
    context: ChapterProductionInvocationContext,
    workflow_run_id: UUID,
    cursor: str,
) -> ChapterProductionSchedulingResult:
    """Freshly validate authority, then call at most one existing facade entry."""

    if type(cursor) is not str or cursor not in _CURSORS:
        raise _invalid() from None
    scope = _scope(context, workflow_run_id)
    state = await service.load_state(  # type: ignore[attr-defined]
        **scope, require_langgraph_runtime=True
    )
    if cursor == "reconstruct" and state.status is ChapterProductionStatus.FAILED:
        # PostgreSQL reconstruction owns recovery routing: known failures enter
        # the existing reconcile facade instead of retrying a provider here.
        return _continue("reconcile")
    if cursor == "reconcile" and state.status is ChapterProductionStatus.FAILED:
        try:
            recovered = await service.reconcile_indeterminate(**scope)  # type: ignore[attr-defined]
            return _result_for_state(recovered)
        except ChapterProductionV2ReconciliationError:
            if getattr(state, "document_id", None) is None:
                return _continue("draft")
            raise
    current = _result_for_state(state)
    if current.kind == "await-user":
        await service.validate_scheduling_action(  # type: ignore[attr-defined]
            **scope,
            action_request_id=current.action_request_id,
            action_kind=state.action_kind,
        )
        return current
    if current.kind != "continue" or cursor == "reconstruct":
        return current
    if cursor == "finalize" and state.status is ChapterProductionStatus.REVISION_READY:
        pass
    elif cursor != current.next_cursor:
        return current

    if cursor == "draft":
        await service._schedule_drafting(**scope)  # type: ignore[attr-defined]
    elif cursor in _REVIEW_CURSORS:
        await service.execute_current_review(**scope)  # type: ignore[attr-defined]
    elif cursor == "finalize":
        await service.finalize_without_reader_panel(**scope)  # type: ignore[attr-defined]
    elif cursor == "mark_revision_ready":
        return _continue("finalize")
    else:
        return ChapterProductionSchedulingResult(
            kind="reconciliation-required",
            failure_code=ChapterFailureCode.RECONCILIATION_REQUIRED.value,
        )
    return _result_for_state(
        await service.load_state(  # type: ignore[attr-defined]
            **scope, require_langgraph_runtime=True
        )
    )


__all__ = [
    "ChapterProductionInvocationContext",
    "ChapterProductionSchedulingResult",
    "advance_chapter_production",
]
