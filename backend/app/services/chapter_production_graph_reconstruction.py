"""Read-only PostgreSQL reconstruction of trusted LangGraph scheduler input."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.contracts import (
    GRAPH_ID,
    GRAPH_VERSION,
    GraphError,
    GraphState,
    ResumeReason,
    parse_graph_state,
)
from app.models import WorkflowCheckpoint, WorkflowRun, WorkflowType

_RUNTIME_KEYS = frozenset({"scheduler_kind", "graph_id", "graph_version"})
_LANGGRAPH_KIND = "langgraph"
_CURSOR_BY_STATUS = {
    "DRAFTING": "draft",
    "AUTHOR_REVISION": "author_revision",
    "EDITOR_REVIEW": "editor_review",
    "REVIEW_REVISION": "corrective_revision",
    "CHIEF_FINAL_REVIEW": "chief_editor_review",
    "LORE_FINAL_REVIEW": "lore_review",
    "REVISION_READY": "mark_revision_ready",
    "ARCHIVE_UPDATE": "finalize",
    "COMPLETED": "complete",
    "CANCELLED": "cancelled",
    "FAILED": "reconcile",
}

_AUTHOR_GATE_STATUS = frozenset({"AUTHOR_REVISION"})
_AWAITING_STATUSES = frozenset(
    {
        "AUTHOR_REVISION",
        "EDITOR_REVIEW",
        "REVIEW_REVISION",
        "CHIEF_FINAL_REVIEW",
        "LORE_FINAL_REVIEW",
    }
)


def _invalid() -> GraphError:
    return GraphError()


def _strict_runtime(metadata: object) -> tuple[str, str]:
    if type(metadata) is not dict:
        raise _invalid() from None
    runtime = metadata.get("chapter_production_runtime")
    if type(runtime) is not dict or set(runtime) != _RUNTIME_KEYS:
        raise _invalid() from None
    if (
        runtime["scheduler_kind"] != _LANGGRAPH_KIND
        or runtime["graph_id"] != GRAPH_ID
        or runtime["graph_version"] != GRAPH_VERSION
    ):
        raise _invalid() from None
    return runtime["graph_id"], runtime["graph_version"]


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if type(value) is UUID:
        return value
    if type(value) is str:
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError):
            raise _invalid() from None
        if str(parsed) != value:
            raise _invalid() from None
        return parsed
    try:
        parsed = UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise _invalid() from None
    if str(parsed) != str(value) or getattr(value, "int", 0) == 0:
        raise _invalid() from None
    return parsed


def _cursor_for(status: str, awaiting_user: bool) -> str:
    if status not in _CURSOR_BY_STATUS:
        raise _invalid() from None
    if awaiting_user and status not in _AWAITING_STATUSES:
        raise _invalid() from None
    if status in _AUTHOR_GATE_STATUS and awaiting_user:
        return "await_author_action"
    return _CURSOR_BY_STATUS[status]


async def reconstruct_scheduler_input(
    session: AsyncSession,
    workflow_run_id: UUID,
    *,
    resume_reason: str = ResumeReason.NEW.value,
) -> GraphState:
    """Rebuild the closed scheduler input from PostgreSQL without writing anything."""
    run = await session.get(WorkflowRun, workflow_run_id)
    if run is None or run.workflow_type != WorkflowType.CHAPTER_PRODUCTION.value:
        raise _invalid() from None
    graph_id, graph_version = _strict_runtime(run.metadata_)
    checkpoints = (
        await session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == workflow_run_id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .limit(2)
        )
    ).all()
    if not checkpoints:
        raise _invalid() from None
    checkpoint = checkpoints[0]
    if len(checkpoints) > 1 and (
        checkpoints[1].checkpoint_index == checkpoint.checkpoint_index
        or checkpoints[1].checkpoint_index != checkpoint.checkpoint_index - 1
    ):
        raise _invalid() from None
    if type(checkpoint.state_json) is not dict:
        raise _invalid() from None
    state_json = checkpoint.state_json
    return parse_graph_state(
        {
            "workflow_run_id": run.id,
            "graph_id": graph_id,
            "graph_version": graph_version,
            "cursor": _cursor_for(run.status, run.awaiting_user),
            "workflow_checkpoint_index": checkpoint.checkpoint_index,
            "invocation_id": uuid4(),
            "attempt_id": _optional_uuid(state_json.get("attempt_id")),
            "claim_id": _optional_uuid(state_json.get("claim_id")),
            "action_request_id": _optional_uuid(state_json.get("action_request_id")),
            "resume_reason": resume_reason,
        }
    )


__all__ = ["reconstruct_scheduler_input"]
