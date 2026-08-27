from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ActionRequest, Document, DocumentVersion, WorkflowCheckpoint, WorkflowRun
from app.models.reader_panel import ReaderPanelSession, ReaderRun
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.services.reader_panel_service import ReaderPanelInvalidStateError, ReaderPanelService
from app.workflows.chapter_production import ChapterProductionStatus
from app.workflows.reader_panel import PanelMode, ReaderPanelStatus, get_mode_preset_config
from tests.integration.test_chapter_production_v2_review_service import (
    review_ready_chapter,
    run_id,
)


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _enter_ready(
    session: AsyncSession, tmp_path: Path, mode: PanelMode
) -> tuple[object, object, object, ChapterProductionV2Service, UUID]:
    project, chapter, owner, service, *_ = await review_ready_chapter(session, tmp_path)
    service._reader_panel_mode = mode
    service._reader_panel = ReaderPanelService(session)
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
        )
    state = await service.load_state(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.REVISION_READY
    return project, chapter, owner, service, workflow_run_id


async def test_off_preserves_ready_with_zero_panel_side_effects(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, workflow_run_id = await _enter_ready(
        async_session, tmp_path, PanelMode.OFF
    )

    assert (
        await async_session.scalar(
            select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.project_id == project.id,
                WorkflowRun.chapter_id == chapter.id,
                WorkflowRun.workflow_type == "reader_panel",
            )
        )
        == 0
    )
    assert await async_session.scalar(select(func.count()).select_from(ReaderPanelSession)) == 0
    assert await async_session.scalar(select(func.count()).select_from(ReaderRun)) == 0
    ready_checkpoints = await async_session.scalar(
        select(func.count()).select_from(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == workflow_run_id,
            WorkflowCheckpoint.node_name == ChapterProductionStatus.REVISION_READY.value,
        )
    )
    assert ready_checkpoints == 1
    await service.finalize_without_reader_panel(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )


@pytest.mark.parametrize("mode", [PanelMode.QUICK, PanelMode.STANDARD, PanelMode.PANEL])
async def test_enabled_ready_launch_is_exact_independent_and_restart_safe(
    async_session: AsyncSession, tmp_path: Path, mode: PanelMode
) -> None:
    project, chapter, owner, service, workflow_run_id = await _enter_ready(
        async_session, tmp_path / mode.value, mode
    )
    panel = await async_session.scalar(
        select(ReaderPanelSession).where(
            ReaderPanelSession.project_id == project.id,
            ReaderPanelSession.chapter_id == chapter.id,
        )
    )
    assert panel is not None
    panel_run = await async_session.get(WorkflowRun, panel.workflow_run_id)
    chapter_run = await async_session.get(WorkflowRun, workflow_run_id)
    document = await async_session.get(Document, panel.document_id)
    preset = get_mode_preset_config(mode)
    assert panel_run is not None and chapter_run is not None and document is not None
    assert panel.mode == mode.value
    assert panel.config_snapshot["reader_count"] == preset.reader_count
    assert panel.config_snapshot["max_ballot_issues"] == preset.max_ballot_issues
    assert panel.config_snapshot["max_discussion_issues"] == preset.max_discussion_issues
    assert panel.config_snapshot["max_rounds_per_issue"] == preset.max_rounds_per_issue
    assert panel.config_snapshot["min_valid_readers"] == preset.min_valid_readers
    assert document.current_version_id == panel.document_version_id
    assert chapter_run.status == ChapterProductionStatus.REVISION_READY.value
    assert chapter_run.current_node == ChapterProductionStatus.REVISION_READY.value
    assert project.current_workflow_id != panel.workflow_run_id

    ready_pair = next(
        pair
        for pair in await service._validated_ready_pairs_locked(chapter_run)
        if pair.state.document_version_id == str(panel.document_version_id)
    )
    replay = await ReaderPanelService(async_session).initialize_from_revision_ready(
        chapter_workflow_run=chapter_run,
        ready_pair=ready_pair,
        mode=PanelMode.PANEL,
    )
    assert replay.session_id == panel.id
    assert (
        await async_session.scalar(
            select(func.count()).select_from(ReaderPanelSession).where(
                ReaderPanelSession.project_id == project.id,
                ReaderPanelSession.chapter_id == chapter.id,
            )
        )
        == 1
    )

    finalized = await service.finalize_without_reader_panel(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    durable_panel = await async_session.get(
        ReaderPanelSession, panel.id, populate_existing=True
    )
    assert finalized.workflow_run_id == workflow_run_id
    assert durable_panel is not None
    assert durable_panel.status == ReaderPanelStatus.INDEPENDENT_READING.value


async def test_manual_start_coexists_and_new_version_only_marks_automatic_panel_stale(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(async_session, tmp_path)
    workflow_run_id = run_id(chapter)
    service._reader_panel_mode = PanelMode.QUICK
    service._reader_panel = ReaderPanelService(async_session)
    for _ in range(2):
        await service.execute_current_review(
            project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
        )
    state = await service.load_state(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    manual = await ReaderPanelService(async_session).initialize_session(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=UUID(state.document_id),
        document_version_id=UUID(state.document_version_id),
        mode=PanelMode.QUICK,
    )
    await service.execute_current_review(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    panels = list(
        await async_session.scalars(
            select(ReaderPanelSession).where(
                ReaderPanelSession.project_id == project.id,
                ReaderPanelSession.chapter_id == chapter.id,
            )
        )
    )
    assert len(panels) == 2
    automatic = next(item for item in panels if item.id != manual.session_id)

    document = await async_session.get(Document, automatic.document_id)
    source = await async_session.get(DocumentVersion, automatic.document_version_id)
    assert document is not None and source is not None
    replacement = DocumentVersion(
        id=uuid4(),
        document_id=document.id,
        version_number=source.version_number + 1,
        source=source.source,
        content_hash="f" * 64,
        byte_size=1,
        word_count=1,
        file_path=f"{source.file_path}.stale-test",
        metadata_={"segments": {"S001": "x"}},
    )
    async_session.add(replacement)
    await async_session.flush()
    document.current_version_id = replacement.id
    await async_session.commit()

    detail = await ReaderPanelService(async_session).get_scoped_session(
        project.id, chapter.id, automatic.id
    )
    assert detail["stale"] is True
    assert detail["status"] == ReaderPanelStatus.INDEPENDENT_READING.value


async def test_two_ready_consumers_create_exactly_one_automatic_panel(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, *_ = await review_ready_chapter(async_session, tmp_path)
    workflow_run_id = run_id(chapter)
    for _ in range(2):
        await service.execute_current_review(
            project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
        )
    engine = async_session.bind
    assert engine is not None
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def consume() -> object:
        async with sessions() as db:
            concurrent = ChapterProductionV2Service(
                db,
                writer_agent=service.writer_agent,
                revision_agent=service.revision_agent,
                editor_agent=service.editor_agent,
                chief_editor_agent=service.chief_editor_agent,
                lore_agent=service.lore_agent,
                chief_editor_required=service.chief_editor_required,
                phase_session_source=service._phase_session_source,
                reader_panel_mode=PanelMode.QUICK,
            )
            return await concurrent.execute_current_review(
                project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
            )

    outcomes = await asyncio.gather(consume(), consume(), return_exceptions=True)
    assert any(not isinstance(item, BaseException) for item in outcomes)
    assert (
        await async_session.scalar(
            select(func.count()).select_from(ReaderPanelSession).where(
                ReaderPanelSession.project_id == project.id,
                ReaderPanelSession.chapter_id == chapter.id,
            )
        )
        == 1
    )


async def test_duplicate_exact_ready_claim_fails_closed(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, service, workflow_run_id = await _enter_ready(
        async_session, tmp_path, PanelMode.QUICK
    )
    panel = await async_session.scalar(
        select(ReaderPanelSession).where(ReaderPanelSession.chapter_id == chapter.id)
    )
    chapter_run = await async_session.get(WorkflowRun, workflow_run_id)
    original_run = await async_session.get(WorkflowRun, panel.workflow_run_id) if panel else None
    assert panel is not None and chapter_run is not None and original_run is not None
    duplicate = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type="reader_panel",
        status="running",
        metadata_=dict(original_run.metadata_),
    )
    async_session.add(duplicate)
    await async_session.flush()
    pair = next(
        item
        for item in await service._validated_ready_pairs_locked(chapter_run)
        if item.state.document_version_id == str(panel.document_version_id)
    )
    with pytest.raises(ReaderPanelInvalidStateError):
        await ReaderPanelService(async_session).initialize_from_revision_ready(
            chapter_workflow_run=chapter_run,
            ready_pair=pair,
            mode=PanelMode.QUICK,
        )


async def test_cancelled_automatic_panel_cannot_change_or_block_ready_finalization(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, chapter_service, workflow_run_id = await _enter_ready(
        async_session, tmp_path, PanelMode.QUICK
    )
    panel = await async_session.scalar(
        select(ReaderPanelSession).where(ReaderPanelSession.chapter_id == chapter.id)
    )
    assert panel is not None
    document = await async_session.get(Document, panel.document_id)
    assert document is not None
    version_id = document.current_version_id
    checkpoint_count = await async_session.scalar(
        select(func.count()).select_from(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == workflow_run_id
        )
    )
    action_count = await async_session.scalar(select(func.count()).select_from(ActionRequest))

    cancelled = await ReaderPanelService(async_session).cancel_session(session_id=panel.id)

    assert cancelled.status == ReaderPanelStatus.CANCELLED.value
    assert document.current_version_id == version_id
    assert (
        await async_session.scalar(
            select(func.count()).select_from(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == workflow_run_id
            )
        )
        == checkpoint_count
    )
    assert (
        await async_session.scalar(
            select(func.count()).select_from(ActionRequest)
        )
        == action_count
    )

    finalized = await chapter_service.finalize_without_reader_panel(
        project.id, chapter.id, workflow_run_id, actor_user_id=owner.id
    )
    assert finalized.workflow_run_id == workflow_run_id
    assert document.current_version_id == version_id
