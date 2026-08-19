"""Closed content-free contracts for the GuraNovel LangGraph runtime foundation."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict
from uuid import UUID

GRAPH_ID = "chapter-production-langgraph"
GRAPH_VERSION = "0"
MAX_CHECKPOINT_INDEX = 2_147_483_647
MAX_RECURSION_LIMIT = 1_000

_STATE_KEYS = frozenset(
    {
        "workflow_run_id",
        "graph_id",
        "graph_version",
        "cursor",
        "workflow_checkpoint_index",
        "invocation_id",
        "attempt_id",
        "claim_id",
        "action_request_id",
        "resume_reason",
    }
)

_METADATA_KEYS = frozenset(
    {
        "workflow_run_id",
        "graph_id",
        "graph_version",
        "cursor",
        "workflow_checkpoint_index",
        "invocation_id",
    }
)


class GraphError(Exception):
    """A fixed, content-free failure at the graph runtime boundary."""

    code = "graph_runtime_invalid"


class Cursor(str, Enum):
    START = "start"
    DRAFT = "draft"
    AWAIT_ACTION = "await_author_action"
    AUTHOR_REVISION = "author_revision"
    EDITOR_REVIEW = "editor_review"
    CHIEF_EDITOR_REVIEW = "chief_editor_review"
    LORE_REVIEW = "lore_review"
    CORRECTIVE_REVISION = "corrective_revision"
    MARK_REVISION_READY = "mark_revision_ready"
    FINALIZE = "finalize"
    RECONCILE = "reconcile"
    COMPLETE = "complete"


class ResumeReason(str, Enum):
    NEW = "new"
    ACTION_RESOLVED = "action-resolved"
    RETRY = "retry"
    RECONCILE = "reconcile"


class OutcomeKind(str, Enum):
    CONTINUE = "continue"
    AWAIT_USER = "await-user"
    RETRYABLE_FAILURE = "retryable-failure"
    RECONCILIATION_REQUIRED = "reconciliation-required"
    CANCELLED = "cancelled"
    COMPLETE = "complete"


class FailureCode(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_PROVIDER_OUTPUT = "invalid_provider_output"
    DOCUMENT_COMMIT_INDETERMINATE = "document_commit_indeterminate"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    ARCHIVE_UNAVAILABLE = "archive_unavailable"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class CompletionCode(str, Enum):
    SUCCESS = "success"


class GraphState(TypedDict):
    workflow_run_id: UUID
    graph_id: str
    graph_version: str
    cursor: str
    workflow_checkpoint_index: int
    invocation_id: UUID
    attempt_id: UUID | None
    claim_id: UUID | None
    action_request_id: UUID | None
    resume_reason: str


def _invalid() -> GraphError:
    return GraphError()


def _canonical_uuid(value: object, *, optional: bool = False) -> UUID | None:
    if value is None and optional:
        return None
    if type(value) is UUID:
        parsed = value
    elif type(value) is str:
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError):
            raise _invalid() from None
        if str(parsed) != value:
            raise _invalid() from None
    else:
        raise _invalid() from None
    if parsed.int == 0:
        raise _invalid() from None
    return parsed


def _enum_value(value: object, enum_type: type[Enum]) -> str:
    if type(value) is str:
        if value not in enum_type._value2member_map_:  # type: ignore[attr-defined]
            raise _invalid() from None
        return value
    if isinstance(value, enum_type):
        return value.value
    raise _invalid() from None


def _exact_string(value: object, expected: str) -> str:
    if type(value) is not str or value != expected:
        raise _invalid() from None
    return value


def _bounded_index(value: object) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise _invalid() from None
    if not 0 <= value <= MAX_CHECKPOINT_INDEX:
        raise _invalid() from None
    return value


def parse_graph_state(value: object) -> GraphState:
    """Return a canonical closed graph state or fail closed."""
    if type(value) is not dict or set(value) != _STATE_KEYS:
        raise _invalid() from None
    return {
        "workflow_run_id": _canonical_uuid(value["workflow_run_id"]),
        "graph_id": _exact_string(value["graph_id"], GRAPH_ID),
        "graph_version": _exact_string(value["graph_version"], GRAPH_VERSION),
        "cursor": _enum_value(value["cursor"], Cursor),
        "workflow_checkpoint_index": _bounded_index(value["workflow_checkpoint_index"]),
        "invocation_id": _canonical_uuid(value["invocation_id"]),
        "attempt_id": _canonical_uuid(value["attempt_id"], optional=True),
        "claim_id": _canonical_uuid(value["claim_id"], optional=True),
        "action_request_id": _canonical_uuid(value["action_request_id"], optional=True),
        "resume_reason": _enum_value(value["resume_reason"], ResumeReason),
    }


def parse_graph_outcome(value: object) -> dict[str, Any]:
    """Return a canonical closed typed outcome or fail closed."""
    if type(value) is not dict:
        raise _invalid() from None
    kind = value.get("kind")
    if kind == OutcomeKind.CONTINUE.value:
        if set(value) != {"kind", "next_cursor"}:
            raise _invalid() from None
        return {"kind": kind, "next_cursor": _enum_value(value["next_cursor"], Cursor)}
    if kind == OutcomeKind.AWAIT_USER.value:
        if set(value) != {"kind", "action_request_id"}:
            raise _invalid() from None
        return {
            "kind": kind,
            "action_request_id": _canonical_uuid(value["action_request_id"]),
        }
    if kind == OutcomeKind.RETRYABLE_FAILURE.value:
        if set(value) != {"kind", "failure_code"}:
            raise _invalid() from None
        return {
            "kind": kind,
            "failure_code": _enum_value(value["failure_code"], FailureCode),
        }
    if kind == OutcomeKind.RECONCILIATION_REQUIRED.value:
        if set(value) != {"kind", "failure_code"}:
            raise _invalid() from None
        return {
            "kind": kind,
            "failure_code": _enum_value(value["failure_code"], FailureCode),
        }
    if kind == OutcomeKind.CANCELLED.value:
        if set(value) != {"kind"}:
            raise _invalid() from None
        return {"kind": kind}
    if kind == OutcomeKind.COMPLETE.value:
        if set(value) != {"kind", "completion_code"}:
            raise _invalid() from None
        return {
            "kind": kind,
            "completion_code": _enum_value(value["completion_code"], CompletionCode),
        }
    raise _invalid() from None


def sanitize_checkpoint_payload(value: object) -> GraphState:
    """The checkpointer payload is exactly the closed graph state allowlist."""
    return parse_graph_state(value)


def sanitize_config(value: object) -> dict[str, Any]:
    """Accept only server-built configurable.thread_id and a bounded recursion limit."""
    if type(value) is not dict or set(value) != {"configurable", "recursion_limit"}:
        raise _invalid() from None
    configurable = value["configurable"]
    if type(configurable) is not dict or set(configurable) != {"thread_id"}:
        raise _invalid() from None
    thread_id = _canonical_uuid(configurable["thread_id"])
    recursion_limit = value["recursion_limit"]
    if (
        type(recursion_limit) is not int
        or isinstance(recursion_limit, bool)
        or not 1 <= recursion_limit <= MAX_RECURSION_LIMIT
    ):
        raise _invalid() from None
    return {
        "configurable": {"thread_id": str(thread_id)},
        "recursion_limit": recursion_limit,
    }


def sanitize_metadata(value: object) -> dict[str, Any]:
    """Return only the safe framework metadata subset or fail closed."""
    if type(value) is not dict or set(value) != _METADATA_KEYS:
        raise _invalid() from None
    return {
        "workflow_run_id": _canonical_uuid(value["workflow_run_id"]),
        "graph_id": _exact_string(value["graph_id"], GRAPH_ID),
        "graph_version": _exact_string(value["graph_version"], GRAPH_VERSION),
        "cursor": _enum_value(value["cursor"], Cursor),
        "workflow_checkpoint_index": _bounded_index(value["workflow_checkpoint_index"]),
        "invocation_id": _canonical_uuid(value["invocation_id"]),
    }


__all__ = [
    "GRAPH_ID",
    "GRAPH_VERSION",
    "CompletionCode",
    "Cursor",
    "FailureCode",
    "GraphError",
    "GraphState",
    "OutcomeKind",
    "ResumeReason",
    "parse_graph_outcome",
    "parse_graph_state",
    "sanitize_checkpoint_payload",
    "sanitize_config",
    "sanitize_metadata",
]
