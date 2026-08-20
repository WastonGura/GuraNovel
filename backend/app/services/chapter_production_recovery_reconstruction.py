"""PostgreSQL-authoritative reconstruction of Chapter Production V2 state."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import (
    Document,
    DocumentType,
    DocumentVersion,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_production_recovery_shared import (
    _ATTEMPT_STATUS_CLAIMED,
    _invalid,
)
from app.services.chapter_production_runtime import chapter_production_langgraph_pin
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.services.review_revision_saga import _reconciliation_candidates
from app.services.revision_readiness_store import _ReviewStateReferences
from app.workflows.chapter_production import (
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
)


async def locked_state(
    service: object, run: WorkflowRun
) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
    service._run_metadata(run)
    checkpoints = list(
        await service.session.scalars(
            select(WorkflowCheckpoint)
            .execution_options(populate_existing=True)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .limit(2)
            .with_for_update()
        )
    )
    if not checkpoints:
        raise _invalid()
    checkpoint = checkpoints[0]
    if len(checkpoints) == 2 and (
        checkpoint.checkpoint_index != checkpoints[1].checkpoint_index + 1
    ):
        raise _invalid()
    payload = checkpoint.state_json
    finalized_statuses = {
        ChapterProductionStatus.REVISION_READY.value,
        ChapterProductionStatus.ARCHIVE_UPDATE.value,
        ChapterProductionStatus.COMPLETED.value,
    }
    finalized = type(payload) is dict and (
        payload.get("status") in finalized_statuses
        or (
            payload.get("status") == ChapterProductionStatus.FAILED.value
            and payload.get("failed_from_status") in finalized_statuses
        )
    )
    try:
        if not finalized:
            state = ChapterProductionState.from_checkpoint(payload)
            state.validate_persistence_binding(
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type,
                run_status=run.status,
                run_current_node=run.current_node,
                run_awaiting_user=run.awaiting_user,
                checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                checkpoint_node_name=checkpoint.node_name,
            )
            return state, checkpoint
        return await _locked_finalized_state(service, run, checkpoint, payload)
    except (ChapterProductionValidationError, KeyError, TypeError, ValueError):
        raise _invalid() from None


async def _locked_finalized_state(
    service: object,
    run: WorkflowRun,
    checkpoint: WorkflowCheckpoint,
    payload: dict[str, object],
) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
    if run.project_id is None or run.chapter_id is None:
        raise ChapterProductionValidationError("Finalized scope is incomplete.")
    references = _ReviewStateReferences(
        review_policy_version=payload["review_policy_version"],
        chief_editor_required=payload["chief_editor_required"],
        editor_report_id=payload["editor_report_id"],
        chief_editor_report_id=payload["chief_editor_report_id"],
        lore_report_id=payload["lore_report_id"],
    )
    document_id = UUID(payload["document_id"])
    version_id = UUID(payload["document_version_id"])
    document = await service.session.scalar(
        select(Document)
        .options(selectinload(Document.project), selectinload(Document.current_version))
        .execution_options(populate_existing=True)
        .where(
            Document.id == document_id,
            Document.project_id == run.project_id,
            Document.chapter_id == run.chapter_id,
            Document.type == DocumentType.CHAPTER_DRAFT.value,
            Document.current_version_id == version_id,
        )
        .with_for_update()
    )
    version = await service.session.scalar(
        select(DocumentVersion)
        .execution_options(populate_existing=True)
        .where(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document_id,
            DocumentVersion.content_hash == payload["content_hash"],
        )
        .with_for_update()
    )
    if document is None or version is None:
        raise ChapterProductionValidationError("Finalized version is stale.")
    policy, editor, chief, lore = await service._live_review_bindings_locked(
        run=run,
        state=references,
        document=document,
        version=version,
    )
    state = ChapterProductionState.from_finalized_checkpoint(
        payload,
        policy=policy,
        workflow_run_id=str(run.id),
        chapter_id=str(run.chapter_id),
        run_workflow_type=run.workflow_type,
        run_status=run.status,
        run_current_node=run.current_node,
        run_awaiting_user=run.awaiting_user,
        checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
        checkpoint_node_name=checkpoint.node_name,
        document_id=str(document.id),
        current_document_version_id=str(version.id),
        version_content_hash=version.content_hash,
        editor_report=editor,
        chief_editor_report=chief,
        lore_report=lore,
    )
    await service._validate_existing_ready_pair_locked(
        run=run,
        state=state,
        policy=policy,
        document=document,
        version=version,
        editor=editor,
        chief=chief,
        lore=lore,
    )
    return state, checkpoint


async def load_state(
    service: object,
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    actor_user_id: UUID,
    require_langgraph_runtime: bool = False,
) -> ChapterProductionState:
    """Load only the exact latest V2 checkpoint and validate its run projection."""

    try:
        if type(require_langgraph_runtime) is not bool:
            raise _invalid() from None
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        await service._require_project_owner(project_id, actor_user_id)
        await service._chapter(project_id, chapter_id, lock=False)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=False)
        metadata = service._run_metadata(run)
        if require_langgraph_runtime and metadata.get(
            "chapter_production_runtime"
        ) != chapter_production_langgraph_pin():
            raise _invalid() from None
        state, _ = await locked_state(service, run)
        await service.session.commit()
        return state
    except ChapterProductionV2ValidationError:
        await service._rollback()
        raise
    except Exception:
        await service._rollback()
        raise _invalid() from None


async def reconcile_review_route(
    service: object,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    *,
    actor_user_id: UUID,
) -> ChapterProductionState:
    service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
    await service._require_project_owner(project_id, actor_user_id)
    await service._chapter(project_id, chapter_id, lock=True)
    run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
    state, _ = await locked_state(service, run)
    if state.status is not ChapterProductionStatus.EDITOR_REVIEW:
        raise ChapterProductionV2ReconciliationError()
    candidates = await _reconciliation_candidates(
        service, run,
        parent_version_id=(
            UUID(state.document_version_id)
            if state.document_version_id is not None
            else None
        ),
    )
    if len(candidates) > 1 or candidates:
        raise ChapterProductionV2ReconciliationError()
    attempt = service._run_metadata(run)["provider_attempt"]
    if type(attempt) is dict and attempt.get("status") == _ATTEMPT_STATUS_CLAIMED:
        raise ChapterProductionV2ReconciliationError()
    if state.document_id is None:
        raise ChapterProductionV2ReconciliationError()
    canonical = await service.session.scalar(
        select(Document).where(
            Document.id == UUID(state.document_id),
            Document.project_id == project_id,
            Document.chapter_id == chapter_id,
            Document.current_version_id == UUID(state.document_version_id),
        ).with_for_update()
    )
    if canonical is None:
        raise ChapterProductionV2ReconciliationError()
    await service.session.commit()
    return state


__all__ = [
    "_locked_finalized_state",
    "load_state",
    "locked_state",
    "reconcile_review_route",
]
