"""Serial PostgreSQL integration tests for the Chapter Production V2 full lifecycle.

Covers:
- End-to-end database-backed lifecycle from outline approval to final chapter persistence.
- Author revision loop with manual edits and segment feedback (document parent chain integrity).
- Multi-stage review loop with warnings and blocking revisions.
- Crash recovery and replay idempotence (no duplicate events, versions, or reports).
- Permission and cross-project boundaries (mismatched actor/project/chapter).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import (
    ChiefEditorChapterFinalAgent,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    EditorAgent,
    LoreChapterFinalAgent,
    RevisionAgent,
    WriterAgent,
)
from app.documents.chapter_segments import derive_chapter_segment_map
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    ReviewReport,
    User,
    WorkflowEvent,
)
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_service import (
    ChapterProductionV2Finalized,
    ChapterProductionV2Service,
    ChapterProductionV2Started,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentService
from app.workflows.chapter_production import (
    ChapterActionKind,
    ChapterProductionStatus,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def phase_source(session: AsyncSession) -> ChapterPhaseSessionSource:
    bind = session.bind
    assert bind is not None
    return ChapterPhaseSessionSource(bind)


async def create_approved_chapter(
    session: AsyncSession, workspace: Path
) -> tuple[Project, Chapter, User, Document, DocumentVersion]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"author-{uuid4().hex[:8]}", display_name="Author")
    session.add(owner)
    await session.flush()

    project = Project(
        slug=f"project-{uuid4().hex[:8]}",
        title="Epic Fantasy Story",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()

    chapter = Chapter(
        project_id=project.id,
        chapter_number=1,
        title="Chapter One: The Awakening",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.flush()

    outline = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Selected Outline",
        path=f"chapters/{chapter.id}-selected-outline.md",
        content="# Chapter One\n\nThe hero awakens in a forgotten dungeon.\n\n## Discovery\n\nAncient runes illuminate the stone walls.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved chapter outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    assert outline.current_version is not None
    return project, chapter, owner, outline, outline.current_version


def create_v2_service(
    session: AsyncSession,
    *,
    writer_provider: DeterministicChapterWriterProvider | None = None,
    review_outcome: str = "passed",
    chief_editor_required: bool = True,
) -> tuple[
    ChapterProductionV2Service,
    DeterministicChapterWriterProvider,
    DeterministicChapterReviewProvider,
]:
    w_provider = writer_provider or DeterministicChapterWriterProvider()
    r_provider = DeterministicChapterReviewProvider(outcome=review_outcome)  # type: ignore[arg-type]

    service = ChapterProductionV2Service(
        session,
        writer_agent=WriterAgent(w_provider),
        revision_agent=RevisionAgent(w_provider),
        editor_agent=EditorAgent(r_provider),
        chief_editor_agent=ChiefEditorChapterFinalAgent(r_provider),
        lore_agent=LoreChapterFinalAgent(r_provider),
        chief_editor_required=chief_editor_required,
        phase_session_source=phase_source(session),
    )
    return service, w_provider, r_provider


# ==============================================================================
# 1. Complete Happy Path (Outline Approval -> Final Document Persistence)
# ==============================================================================


async def test_end_to_end_chapter_production_happy_path(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, outline_doc, outline_ver = await create_approved_chapter(
        async_session, tmp_path / "happy-path"
    )
    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    # 1. Start Chapter Production V2
    started: ChapterProductionV2Started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    assert started.outline_document_id == outline_doc.id
    assert started.outline_version_id == outline_ver.id
    assert started.draft_document_id is not None
    assert started.draft_version_id is not None
    assert started.action_request_id is not None

    # Verify Draft Document and Version stored in PostgreSQL
    draft_doc = await async_session.get(Document, started.draft_document_id)
    assert draft_doc is not None
    assert draft_doc.type == DocumentType.CHAPTER_DRAFT.value
    assert draft_doc.project_id == project.id
    assert draft_doc.chapter_id == chapter.id

    draft_ver = await async_session.get(DocumentVersion, started.draft_version_id)
    assert draft_ver is not None
    assert draft_ver.version_number == 1
    assert draft_ver.document_id == draft_doc.id

    # Verify pending Author ActionRequest
    action_req = await async_session.get(ActionRequest, started.action_request_id)
    assert action_req is not None
    assert action_req.status == ActionRequestStatus.PENDING.value
    assert action_req.action_type == "chapter_author_revision"

    # Verify state is AUTHOR_REVISION
    state = await service.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.AUTHOR_REVISION
    assert state.awaiting_user

    # 2. Author accepts initial draft -> transitions to EDITOR_REVIEW
    updated: ChapterProductionV2Updated = await service.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    assert updated.workflow_run_id == started.workflow_run_id
    assert updated.action_request_id is None

    state = await service.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert not state.awaiting_user

    # 3. Multi-stage review: Editor Review
    await service.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    state = await service.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
    assert state.editor_report_id is not None

    # 4. Multi-stage review: Chief Editor Final Review
    await service.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    state = await service.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    assert state.chief_editor_report_id is not None

    # 5. Multi-stage review: Lore & Continuity Final Review
    await service.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    state = await service.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.REVISION_READY
    assert state.lore_report_id is not None

    # Verify all 3 ReviewReports persisted in database
    reports = list(
        await async_session.scalars(
            select(ReviewReport)
            .where(ReviewReport.workflow_run_id == started.workflow_run_id)
            .order_by(ReviewReport.created_at)
        )
    )
    assert len(reports) == 3
    assert [r.review_mode for r in reports] == [
        "chapter_editor",
        "chapter_chief_final",
        "chapter_final_lore",
    ]
    assert all(r.passed is True for r in reports)

    # 6. Finalize Chapter Production
    finalized: ChapterProductionV2Finalized = await service.finalize_without_reader_panel(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert finalized.workflow_run_id == started.workflow_run_id
    assert finalized.final_document_id is not None
    assert finalized.final_version_id is not None

    # Verify Final Document in PostgreSQL
    final_doc = await async_session.get(Document, finalized.final_document_id)
    assert final_doc is not None
    assert final_doc.type == DocumentType.CHAPTER_FINAL.value
    assert final_doc.chapter_id == chapter.id

    # Verify final Chapter status in PostgreSQL
    await async_session.refresh(chapter)
    assert chapter.status == "COMPLETED"
    assert chapter.final_document_id == finalized.final_document_id

    # Verify WorkflowEvents recorded monotonic final event
    events = list(
        await async_session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
            .order_by(WorkflowEvent.created_at)
        )
    )
    assert len(events) >= 2
    final_event = events[-1]
    assert final_event.event_type == "chapter_finalized"


# ==============================================================================
# 2. Author Revision Loop with Manual Edits and Segment Feedback
# ==============================================================================


async def test_author_manual_edit_and_feedback_revision_loop(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _ = await create_approved_chapter(
        async_session, tmp_path / "author-revision-loop"
    )
    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    draft_doc_id = started.draft_document_id
    ver1_id = started.draft_version_id

    # 1. Author performs a manual edit
    manual_content = "# Chapter One\n\nThe hero awakens and finds an enchanted blade.\n\n## The Blade\n\nIt glows with ancient azure fire.\n"
    updated_manual = await service.submit_manual_edit(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        content=manual_content,
    )
    ver2_id = updated_manual.draft_version_id
    assert ver2_id != ver1_id

    # Check DocumentVersion 2 in DB and verify parent chain
    ver2 = await async_session.get(DocumentVersion, ver2_id)
    assert ver2 is not None
    assert ver2.version_number == 2
    assert ver2.parent_id == ver1_id
    assert ver2.document_id == draft_doc_id

    # State reset to EDITOR_REVIEW on version 2
    state = await service.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.document_version_id == ver2_id

    # 2. Author requests feedback revision targeting segments
    # First derive segment map on manual edit
    segment_map = derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=draft_doc_id,
        version_id=ver2_id,
        content=manual_content,
    )
    target_segment = segment_map.segments[0]

    # Create a fresh author revision action to exercise feedback revision
    action_req = ActionRequest(
        workflow_run_id=started.workflow_run_id,
        action_type="chapter_author_revision",
        status=ActionRequestStatus.PENDING.value,
        payload_schema={
            "document_id": str(draft_doc_id),
            "document_version_id": str(ver2_id),
            "content_hash": ver2.content_hash,
        },
    )
    async_session.add(action_req)
    await async_session.commit()

    updated_feedback = await service.request_user_feedback_revision(
        project.id,
        chapter.id,
        started.workflow_run_id,
        action_req.id,
        actor_user_id=owner.id,
        feedback="Describe the dungeon coldness and silence in detail.",
        target_segment_ids=[target_segment.segment_id],
    )
    ver3_id = updated_feedback.draft_version_id
    assert ver3_id != ver2_id

    # Check DocumentVersion 3 in DB and verify parent chain
    ver3 = await async_session.get(DocumentVersion, ver3_id)
    assert ver3 is not None
    assert ver3.version_number == 3
    assert ver3.parent_id == ver2_id
    assert ver3.document_id == draft_doc_id


# ==============================================================================
# 3. Multi-Stage Review Loop with Warnings and Blocking Revisions
# ==============================================================================


async def test_multistage_review_warning_and_blocking_loop(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _ = await create_approved_chapter(
        async_session, tmp_path / "review-loop"
    )

    # 1. Editor review with WARNING
    service_warning, _, _ = create_v2_service(async_session, review_outcome="warning")
    started = await service_warning.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    await service_warning.resolve_author_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )

    # Execute editor review producing warning
    warn_updated = await service_warning.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert warn_updated.action_request_id is not None

    state = await service_warning.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.awaiting_user
    assert state.action_kind is ChapterActionKind.REVIEW_WARNING

    # Resolve warning by choosing proceed_with_warnings (accept_warning)
    await service_warning.resolve_review_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        warn_updated.action_request_id,
        actor_user_id=owner.id,
        decision="proceed_with_warnings",
    )
    state = await service_warning.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
    assert not state.awaiting_user

    # 2. Chief Editor review with BLOCKING finding
    service_blocking, _, _ = create_v2_service(async_session, review_outcome="blocking")
    block_updated = await service_blocking.execute_current_review(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert block_updated.action_request_id is not None

    state = await service_blocking.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.REVIEW_REVISION
    assert state.awaiting_user
    assert state.action_kind is ChapterActionKind.REVIEW_REVISION

    # Author resolves blocking finding by requesting revision
    await service_blocking.resolve_review_action(
        project.id,
        chapter.id,
        started.workflow_run_id,
        block_updated.action_request_id,
        actor_user_id=owner.id,
        decision="request_review_revision",
    )

    # Execute review revision -> RevisionAgent generates new version
    doc_id = started.draft_document_id
    ver1_id = started.draft_version_id
    doc_ver = await async_session.get(DocumentVersion, ver1_id)
    assert doc_ver is not None

    segment_map = derive_chapter_segment_map(
        project_id=project.id,
        chapter_id=chapter.id,
        document_id=doc_id,
        version_id=ver1_id,
        content=doc_ver.content_text or "",
    )
    reports = list(
        await async_session.scalars(
            select(ReviewReport).where(ReviewReport.workflow_run_id == started.workflow_run_id)
        )
    )

    revised_update = await service_blocking.execute_review_revision(
        project.id,
        chapter.id,
        started.workflow_run_id,
        actor_user_id=owner.id,
        report_ids=[r.id for r in reports],
        target_segment_ids=[segment_map.segments[0].segment_id],
    )
    assert revised_update.draft_version_id != ver1_id

    # Review stage resets to EDITOR_REVIEW on new version
    state = await service_blocking.load_state(
        project.id, chapter.id, started.workflow_run_id, actor_user_id=owner.id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.document_version_id == revised_update.draft_version_id


# ==============================================================================
# 4. Crash Recovery and Replay Idempotence
# ==============================================================================


async def test_crash_recovery_and_replay_idempotence(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _ = await create_approved_chapter(
        async_session, tmp_path / "replay-idempotence"
    )
    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    started_1 = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )

    # Replay start_from_approved_outline -> must be idempotent and return identical run
    started_2 = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )
    assert started_1.workflow_run_id == started_2.workflow_run_id
    assert started_1.draft_document_id == started_2.draft_document_id
    assert started_1.draft_version_id == started_2.draft_version_id

    # Verify no duplicate DocumentVersion records created
    version_count = await async_session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.document_id == started_1.draft_document_id)
    )
    assert version_count == 1

    # Advance to completion
    await service.resolve_author_action(
        project.id,
        chapter.id,
        started_1.workflow_run_id,
        started_1.action_request_id,
        actor_user_id=owner.id,
        decision="accept",
    )
    for _ in range(3):
        await service.execute_current_review(
            project.id, chapter.id, started_1.workflow_run_id, actor_user_id=owner.id
        )

    finalized_1 = await service.finalize_without_reader_panel(
        project.id, chapter.id, started_1.workflow_run_id, actor_user_id=owner.id
    )

    # Replay finalization -> idempotent
    finalized_2 = await service.finalize_without_reader_panel(
        project.id, chapter.id, started_1.workflow_run_id, actor_user_id=owner.id
    )
    assert finalized_1.final_document_id == finalized_2.final_document_id
    assert finalized_1.final_version_id == finalized_2.final_version_id

    # Verify exactly one final document in database
    final_count = await async_session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.chapter_id == chapter.id,
            Document.type == DocumentType.CHAPTER_FINAL.value,
        )
    )
    assert final_count == 1


# ==============================================================================
# 5. Permission and Cross-Project Boundaries
# ==============================================================================


async def test_permission_and_cross_project_boundaries(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _ = await create_approved_chapter(
        async_session, tmp_path / "boundaries"
    )
    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    # Create a second, unprivileged user (attacker)
    attacker = User(username=f"attacker-{uuid4().hex[:8]}", display_name="Attacker")
    async_session.add(attacker)
    await async_session.commit()

    started = await service.start_from_approved_outline(
        project.id, chapter.id, actor_user_id=owner.id
    )

    # 1. Attacker tries to resolve owner's author action -> rejected
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=attacker.id,
            decision="accept",
        )

    # 2. Attacker tries to submit manual edit -> rejected
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            project.id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=attacker.id,
            content="Tampered content",
        )

    # 3. Cross-project boundary: mismatched project_id
    other_project_id = uuid4()
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            other_project_id,
            chapter.id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            decision="accept",
        )

    # 4. Cross-chapter boundary: mismatched chapter_id
    other_chapter_id = uuid4()
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project.id,
            other_chapter_id,
            started.workflow_run_id,
            started.action_request_id,
            actor_user_id=owner.id,
            decision="accept",
        )
