from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import (
    ChiefEditorChapterFinalAgent,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    EditorAgent,
    LoreChapterFinalAgent,
    WriterAgent,
)
from app.graph import GRAPH_ID, GRAPH_VERSION, chapter_production_topology
from app.models import (
    Chapter,
    DocumentSource,
    DocumentType,
    Project,
    User,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_graph_reconstruction import (
    reconstruct_scheduler_input,
)
from app.services.chapter_production_runtime import (
    chapter_production_langgraph_pin,
    chapter_production_runtime_pin,
)
from app.services.chapter_production_v2_service import (
    ChapterProductionV2Finalized,
    ChapterProductionV2Service,
    ChapterProductionV2Started,
)
from app.services.document_service import DocumentService
from app.workflows.chapter_production import ChapterProductionStatus

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _create_test_project_and_chapter(
    session: AsyncSession,
    workspace: Path,
    chapter_number: int = 1,
) -> tuple[Project, Chapter, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"owner-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()

    project = Project(
        slug=f"langgraph-parity-{uuid4().hex}",
        title="LangGraph Parity Project",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()

    chapter = Chapter(
        project_id=project.id,
        chapter_number=chapter_number,
        title=f"Chapter {chapter_number}",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()

    doc_service = DocumentService(session)
    outline = await doc_service.create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Approved outline",
        path=f"chapters/{chapter.id}-approved-outline.md",
        content="# Arrival\n\nReach the gate.\n\n## Warning\n\nHear the cost.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
    )
    await doc_service.create_document(
        project_id=project.id,
        document_type=DocumentType.STYLE_GUIDE,
        title="Style guide",
        path="style/style-guide.md",
        content="# Style\n\nRestrained prose.\n",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    await doc_service.create_document(
        project_id=project.id,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="World overview",
        path="world/overview.md",
        content="# Boundary\n\nThe gate has a cost.\n",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()

    return project, chapter, owner


def _build_service(
    session: AsyncSession,
    *,
    chief_editor_required: bool = True,
) -> ChapterProductionV2Service:
    assert session.bind is not None
    return ChapterProductionV2Service(
        session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
        editor_agent=EditorAgent(DeterministicChapterReviewProvider(outcome="passed")),
        chief_editor_agent=ChiefEditorChapterFinalAgent(
            DeterministicChapterReviewProvider(outcome="passed")
        ),
        lore_agent=LoreChapterFinalAgent(
            DeterministicChapterReviewProvider(outcome="passed")
        ),
        chief_editor_required=chief_editor_required,
        phase_session_source=ChapterPhaseSessionSource(session.bind),
    )


# ============================================================================
# 1. Full End-to-End Chapter Drafting -> Revision -> Review -> Finalization
# ============================================================================


async def test_full_end_to_end_chapter_production_with_langgraph_pin(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    project, chapter, owner = await _create_test_project_and_chapter(
        async_session, tmp_path / "e2e"
    )
    service = _build_service(async_session)

    # 1. Start from approved outline
    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    assert isinstance(started, ChapterProductionV2Started)

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()
    assert run.status == ChapterProductionStatus.AUTHOR_REVISION.value
    assert run.awaiting_user is True

    # 2. Author accept action
    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ChapterProductionStatus.EDITOR_REVIEW.value
    assert run.awaiting_user is False

    # 3. Editor review -> Chief final review
    await service.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ChapterProductionStatus.CHIEF_FINAL_REVIEW.value

    # 4. Chief review -> Lore final review
    await service.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ChapterProductionStatus.LORE_FINAL_REVIEW.value

    # 5. Lore review -> Revision ready
    await service.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ChapterProductionStatus.REVISION_READY.value

    # 6. Finalize without reader panel -> Complete
    finalized = await service.finalize_without_reader_panel(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert isinstance(finalized, ChapterProductionV2Finalized)

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ChapterProductionStatus.COMPLETED.value
    assert run.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()

    # Verify checkpoints continuity
    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
            .order_by(WorkflowCheckpoint.checkpoint_index)
        )
    )
    indices = [cp.checkpoint_index for cp in checkpoints]
    assert indices == list(range(len(checkpoints)))
    assert len(checkpoints) >= 6


# ============================================================================
# 2. Monotonic Event Sequencing: WorkflowEvent.event_sequence is 1, 2, 3, ...
# ============================================================================


async def test_monotonic_event_sequencing_across_lifecycle(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    project, chapter, owner = await _create_test_project_and_chapter(
        async_session, tmp_path / "events"
    )
    service = _build_service(async_session)

    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    for _ in range(3):
        await service.execute_current_review(
            project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
        )
    await service.finalize_without_reader_panel(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )

    events = list(
        await async_session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
            .order_by(WorkflowEvent.event_sequence)
        )
    )
    assert len(events) >= 5
    sequences = [event.event_sequence for event in events]
    assert sequences == list(range(1, len(events) + 1))
    assert all(event.workflow_run_id == started.workflow_run_id for event in events)
    assert all(event.event_type is not None for event in events)


# ============================================================================
# 3. Restart at Each Checkpoint Index Across Distinct Sessions
# ============================================================================


async def test_restart_at_each_checkpoint_across_distinct_sessions(
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Step 1: Initial creation in Session 1
        async with session_factory() as session:
            project, chapter, owner = await _create_test_project_and_chapter(
                session, tmp_path / "restart"
            )
            service = _build_service(session)
            started = await service.start_from_approved_outline(
                project.id, chapter.id, actor_user_id=owner.id
            )
            run_id = started.workflow_run_id
            action_id = started.action_request_id
            proj_id = project.id
            chap_id = chapter.id
            owner_id = owner.id

        # Step 2: Restart in Session 2 -> Verify reconstruction & resolve author action
        async with session_factory() as session:
            state = await reconstruct_scheduler_input(session, run_id)
            assert state["workflow_run_id"] == run_id
            assert state["cursor"] == "await_author_action"
            assert state["action_request_id"] == action_id

            service = _build_service(session)
            await service.resolve_author_action(
                proj_id, chap_id, run_id, action_id, actor_user_id=owner_id, decision="accept"
            )

        # Step 3: Restart in Session 3 -> Verify reconstruction & execute Editor review
        async with session_factory() as session:
            state = await reconstruct_scheduler_input(session, run_id)
            assert state["workflow_run_id"] == run_id
            assert state["cursor"] == "editor_review"

            service = _build_service(session)
            await service.execute_current_review(
                proj_id, chap_id, run_id, actor_user_id=owner_id
            )

        # Step 4: Restart in Session 4 -> Verify reconstruction & execute Chief review
        async with session_factory() as session:
            state = await reconstruct_scheduler_input(session, run_id)
            assert state["workflow_run_id"] == run_id
            assert state["cursor"] == "chief_editor_review"

            service = _build_service(session)
            await service.execute_current_review(
                proj_id, chap_id, run_id, actor_user_id=owner_id
            )

        # Step 5: Restart in Session 5 -> Verify reconstruction & execute Lore review
        async with session_factory() as session:
            state = await reconstruct_scheduler_input(session, run_id)
            assert state["workflow_run_id"] == run_id
            assert state["cursor"] == "lore_review"

            service = _build_service(session)
            await service.execute_current_review(
                proj_id, chap_id, run_id, actor_user_id=owner_id
            )

        # Step 6: Restart in Session 6 -> Verify reconstruction & Finalize
        async with session_factory() as session:
            state = await reconstruct_scheduler_input(session, run_id)
            assert state["workflow_run_id"] == run_id
            assert state["cursor"] == "mark_revision_ready"

            service = _build_service(session)
            await service.finalize_without_reader_panel(
                proj_id, chap_id, run_id, actor_user_id=owner_id
            )

        # Step 7: Restart in Session 7 -> Verify final completed reconstruction
        async with session_factory() as session:
            state = await reconstruct_scheduler_input(session, run_id)
            assert state["workflow_run_id"] == run_id
            assert state["cursor"] == "complete"

    finally:
        await engine.dispose()


# ============================================================================
# 4. Rollback Isolation: Mixed LangGraph-Pinned and Service-Pinned Runs
# ============================================================================


async def test_rollback_isolation_with_coexisting_pinned_runs(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Create LangGraph-pinned Run 1 with GRAPH_ENABLED=True
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    project_1, chapter_1, owner_1 = await _create_test_project_and_chapter(
        async_session, tmp_path / "mixed-lg", chapter_number=1
    )
    service_1 = _build_service(async_session)
    started_1 = await service_1.start_from_approved_outline(
        project_1.id, chapter_1.id, actor_user_id=owner_1.id
    )

    # 2. Create Service-pinned Run 2 with GRAPH_ENABLED=False
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    project_2, chapter_2, owner_2 = await _create_test_project_and_chapter(
        async_session, tmp_path / "mixed-svc", chapter_number=2
    )
    service_2 = _build_service(async_session)
    started_2 = await service_2.start_from_approved_outline(
        project_2.id, chapter_2.id, actor_user_id=owner_2.id
    )

    run_1 = await async_session.get(WorkflowRun, started_1.workflow_run_id)
    run_2 = await async_session.get(WorkflowRun, started_2.workflow_run_id)
    assert run_1 is not None and run_2 is not None
    assert run_1.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()
    assert run_2.metadata_["chapter_production_runtime"] == chapter_production_runtime_pin()

    # 3. Switch GRAPH_ENABLED to False: Run 1 must still reconstruct and dispatch as LangGraph
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    state_1 = await reconstruct_scheduler_input(async_session, started_1.workflow_run_id)
    assert state_1["graph_id"] == GRAPH_ID
    assert state_1["graph_version"] == GRAPH_VERSION

    # 4. Resolve author actions on both runs
    await service_1.resolve_author_action(
        project_1.id,
        chapter_1.id,
        started_1.workflow_run_id,
        started_1.action_request_id,
        actor_user_id=owner_1.id,
        decision="accept",
    )
    await service_2.resolve_author_action(
        project_2.id,
        chapter_2.id,
        started_2.workflow_run_id,
        started_2.action_request_id,
        actor_user_id=owner_2.id,
        decision="accept",
    )

    # 5. Verify both runs advanced cleanly without mutating their pinned runtimes
    run_1_updated = await async_session.get(WorkflowRun, started_1.workflow_run_id)
    run_2_updated = await async_session.get(WorkflowRun, started_2.workflow_run_id)
    assert run_1_updated is not None and run_2_updated is not None
    assert run_1_updated.status == ChapterProductionStatus.EDITOR_REVIEW.value
    assert run_2_updated.status == ChapterProductionStatus.EDITOR_REVIEW.value
    assert run_1_updated.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()
    assert run_2_updated.metadata_["chapter_production_runtime"] == chapter_production_runtime_pin()
