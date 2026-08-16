from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
)
from app.services.document_service import DocumentService
from app.workflows.chapter_production import ChapterProductionState, ChapterProductionStatus
from tests.integration.test_chapter_production_v2_review_service import (
    review_ready_chapter,
    run_id,
)


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _ready_context(
    async_session: AsyncSession, tmp_path: Path
) -> tuple[object, object, object, object, WorkflowRun]:
    project, chapter, owner, service, *_ = await review_ready_chapter(async_session, tmp_path)
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
        )
    run = await async_session.get(WorkflowRun, workflow_run_id)
    assert run is not None
    return project, chapter, owner, service, run


async def _ready_pair_rows(
    session: AsyncSession, run_id: UUID
) -> tuple[WorkflowCheckpoint, WorkflowEvent]:
    checkpoint = await session.scalar(
        select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == run_id,
            WorkflowCheckpoint.node_name == ChapterProductionStatus.REVISION_READY.value,
        )
    )
    event = await session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == run_id,
            WorkflowEvent.event_type == "revision_ready",
        )
    )
    assert checkpoint is not None and event is not None
    return checkpoint, event


async def test_store_enter_creates_exact_pair_for_zero_and_idempotently_reuses_for_one(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, run = await _ready_context(async_session, tmp_path)
    ready_checkpoint, ready_event = await _ready_pair_rows(async_session, run.id)
    lore_checkpoint = await async_session.scalar(
        select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == run.id,
            WorkflowCheckpoint.checkpoint_index == ready_checkpoint.checkpoint_index - 1,
        )
    )
    assert lore_checkpoint is not None
    payload = ready_checkpoint.state_json
    document = await async_session.get(Document, UUID(payload["document_id"]))
    version = await async_session.get(DocumentVersion, UUID(payload["document_version_id"]))
    assert document is not None and version is not None
    lore_state = ChapterProductionState.from_checkpoint(lore_checkpoint.state_json)

    await async_session.delete(ready_event)
    await async_session.delete(ready_checkpoint)
    await async_session.commit()
    run = await async_session.get(WorkflowRun, run.id)
    assert run is not None

    store = service._readiness
    entered = await store.enter(
        run=run,
        checkpoint=lore_checkpoint,
        state=lore_state,
        document=document,
        version=version,
    )
    assert entered.status is ChapterProductionStatus.REVISION_READY
    await async_session.commit()

    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == run.id,
                WorkflowCheckpoint.node_name == ChapterProductionStatus.REVISION_READY.value,
            )
        )
    )
    events = list(
        await async_session.scalars(
            select(WorkflowEvent).where(
                WorkflowEvent.workflow_run_id == run.id,
                WorkflowEvent.event_type == "revision_ready",
            )
        )
    )
    assert len(checkpoints) == len(events) == 1

    reused = await store.enter(
        run=run,
        checkpoint=lore_checkpoint,
        state=lore_state,
        document=document,
        version=version,
    )
    assert reused == entered
    await async_session.commit()
    assert (
        await async_session.scalar(
            select(func.count()).select_from(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == run.id,
                WorkflowCheckpoint.node_name == ChapterProductionStatus.REVISION_READY.value,
            )
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count()).select_from(WorkflowEvent).where(
                WorkflowEvent.workflow_run_id == run.id,
                WorkflowEvent.event_type == "revision_ready",
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_event",
        "missing_checkpoint",
        "duplicate_checkpoint",
        "malformed_marker",
        "hidden_marker",
        "foreign_binding",
        "stale_hash",
        "stale_policy",
    ],
)
async def test_store_validated_pairs_requires_reconciliation_for_cardinality_and_shape_corruption(
    async_session: AsyncSession, tmp_path: Path, corruption: str
) -> None:
    project, chapter, owner, service, run = await _ready_context(
        async_session, tmp_path / corruption
    )
    store = service._readiness
    checkpoint, event = await _ready_pair_rows(async_session, run.id)

    if corruption == "missing_event":
        await async_session.delete(event)
    elif corruption == "missing_checkpoint":
        await async_session.delete(checkpoint)
    elif corruption == "duplicate_checkpoint":
        async_session.add(
            WorkflowCheckpoint(
                workflow_run_id=run.id,
                checkpoint_index=checkpoint.checkpoint_index + 1,
                node_name=checkpoint.node_name,
                state_json=checkpoint.state_json,
            )
        )
    elif corruption == "malformed_marker":
        checkpoint.state_json = {
            "status": ChapterProductionStatus.REVISION_READY.value
        }
    elif corruption == "hidden_marker":
        async_session.add(
            WorkflowCheckpoint(
                workflow_run_id=run.id,
                checkpoint_index=checkpoint.checkpoint_index + 1,
                node_name="DRAFTING",
                state_json={"status": ChapterProductionStatus.REVISION_READY.value},
            )
        )
    elif corruption == "foreign_binding":
        event.payload = {**event.payload, "checkpoint_id": str(uuid4())}
    elif corruption == "stale_hash":
        checkpoint.state_json = {**checkpoint.state_json, "content_hash": "0" * 64}
    else:
        checkpoint.state_json = {
            **checkpoint.state_json,
            "review_policy_version": "chapter-quality-v0",
        }
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await store.validated_pairs(run)


async def test_store_validated_pairs_preserves_complete_historical_ready_pair_for_another_key(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, run = await _ready_context(async_session, tmp_path)
    store = service._readiness
    current = await service.load_state(
        project.id, chapter.id, run.id, actor_user_id=owner.id
    )
    assert current.document_id is not None and current.document_version_id is not None
    content = await DocumentService(async_session).read_version_content(
        UUID(current.document_id), UUID(current.document_version_id)
    )
    historical_document = await DocumentService(async_session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Historical reviewed draft",
        path=f"chapters/{chapter.id}-historical-ready-draft.md",
        content=content,
        source=DocumentSource.SYSTEM,
        workflow_run_id=run.id,
    )
    historical_version = historical_document.current_version
    assert historical_version is not None
    historical_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=historical_document.id,
        version_id=historical_version.id,
    )
    historical_reports: list[ReviewReport] = []
    for mode, role in (
        ("chapter_editor", "editor_agent"),
        ("chapter_chief_final", "chief_editor_agent"),
        ("chapter_final_lore", "lore_agent"),
    ):
        historical_reports.append(
            ReviewReport(
                project_id=project.id,
                chapter_id=chapter.id,
                workflow_run_id=run.id,
                review_mode=mode,
                reviewer_agent_role=role,
                target_document_id=historical_document.id,
                target_version_id=historical_version.id,
                passed=True,
                summary=f"Historical passed {mode} review.",
                blocking_issues=[],
                warnings=[],
                notes=[],
                suggested_actions=[],
                raw_report={
                    "claim_id": str(uuid4()),
                    "contract_version": "chapter-production-v2",
                    "operation_key": uuid4().hex * 2,
                    "request_hash": uuid4().hex * 2,
                    "segment_map_hash": historical_map.map_hash,
                    "segmenter_version": historical_map.segmenter_version,
                },
            )
        )
    async_session.add_all(historical_reports)
    await async_session.flush()
    current_ready = await async_session.scalar(
        select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == run.id,
            WorkflowCheckpoint.node_name == ChapterProductionStatus.REVISION_READY.value,
        )
    )
    marker = await async_session.scalar(
        select(WorkflowCheckpoint)
        .where(
            WorkflowCheckpoint.workflow_run_id == run.id,
            WorkflowCheckpoint.checkpoint_index > 0,
            WorkflowCheckpoint.node_name != ChapterProductionStatus.REVISION_READY.value,
        )
        .order_by(WorkflowCheckpoint.checkpoint_index)
    )
    assert current_ready is not None and marker is not None
    marker.node_name = ChapterProductionStatus.REVISION_READY.value
    marker.state_json = {
        **current_ready.state_json,
        "document_id": str(historical_document.id),
        "document_version_id": str(historical_version.id),
        "content_hash": historical_version.content_hash,
        "editor_report_id": str(historical_reports[0].id),
        "chief_editor_report_id": str(historical_reports[1].id),
        "lore_report_id": str(historical_reports[2].id),
    }
    async_session.add(
        WorkflowEvent(
            workflow_run_id=run.id,
            event_type="revision_ready",
            node_name=ChapterProductionStatus.REVISION_READY.value,
            payload={
                "chapter_id": str(chapter.id),
                "checkpoint_id": str(marker.id),
                "checkpoint_index": marker.checkpoint_index,
                "document_id": str(historical_document.id),
                "document_version_id": str(historical_version.id),
                "content_hash": historical_version.content_hash,
                "review_policy_version": "chapter-quality-v1",
                "status": ChapterProductionStatus.REVISION_READY.value,
            },
        )
    )
    await async_session.commit()

    pairs = await store.validated_pairs(run)
    assert len(pairs) == 2
    keys = {pair.state.semantic_ready_key for pair in pairs}
    assert (str(run.id), str(historical_version.id), "chapter-quality-v1") in keys
    assert (str(run.id), current.document_version_id, "chapter-quality-v1") in keys
