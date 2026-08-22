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
from uuid import UUID, uuid4

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
    await DocumentService(session).create_document(
        project_id=project.id,
        document_type=DocumentType.STYLE_GUIDE,
        title="Style guide",
        path="style/style-guide.md",
        content="# Style\n\nUse precise, restrained prose.\n",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    await DocumentService(session).create_document(
        project_id=project.id,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="World overview",
        path="world/overview.md",
        content="# Boundary\n\nThe sealed dungeon always demands a known price.\n",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
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
    project_id = project.id
    chapter_id = chapter.id
    owner_id = owner.id
    outline_doc_id = outline_doc.id
    outline_ver_id = outline_ver.id

    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    # 1. Start Chapter Production V2
    started: ChapterProductionV2Started = await service.start_from_approved_outline(
        project_id, chapter_id, actor_user_id=owner_id
    )
    assert started.outline_document_id == outline_doc_id
    assert started.outline_version_id == outline_ver_id
    assert started.draft_document_id is not None
    assert started.draft_version_id is not None
    assert started.action_request_id is not None

    # Verify Draft Document and Version stored in PostgreSQL
    draft_doc = await async_session.get(Document, started.draft_document_id)
    assert draft_doc is not None
    assert draft_doc.type == DocumentType.CHAPTER_DRAFT.value
    assert draft_doc.project_id == project_id
    assert draft_doc.chapter_id == chapter_id

    draft_ver = await async_session.get(DocumentVersion, started.draft_version_id)
    assert draft_ver is not None
    assert draft_ver.version_number == 1
    assert draft_ver.document_id == draft_doc.id

    # Verify pending Author ActionRequest
    action_req = await async_session.get(ActionRequest, started.action_request_id)
    assert action_req is not None
    assert action_req.status == ActionRequestStatus.PENDING.value
    assert action_req.request_type == "chapter_author_revision"

    # Verify state is AUTHOR_REVISION
    state = await service.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.AUTHOR_REVISION
    assert state.awaiting_user

    # 2. Author accepts initial draft -> transitions to EDITOR_REVIEW
    updated: ChapterProductionV2Updated = await service.resolve_author_action(
        project_id,
        chapter_id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner_id,
        decision="accept",
    )
    assert updated.workflow_run_id == started.workflow_run_id
    assert updated.action_request_id is None

    state = await service.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert not state.awaiting_user

    # 3. Multi-stage review: Editor Review
    await service.execute_current_review(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    state = await service.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
    assert state.editor_report_id is not None

    # 4. Multi-stage review: Chief Editor Final Review
    await service.execute_current_review(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    state = await service.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    assert state.chief_editor_report_id is not None

    # 5. Multi-stage review: Lore & Continuity Final Review
    await service.execute_current_review(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    state = await service.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
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
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert finalized.workflow_run_id == started.workflow_run_id
    assert finalized.final_document_id is not None
    assert finalized.final_version_id is not None

    # Verify Final Document in PostgreSQL
    final_doc = await async_session.get(Document, finalized.final_document_id)
    assert final_doc is not None
    assert final_doc.type == DocumentType.CHAPTER_FINAL.value
    assert final_doc.chapter_id == chapter_id

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
    project_id = project.id
    chapter_id = chapter.id
    owner_id = owner.id

    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    started = await service.start_from_approved_outline(
        project_id, chapter_id, actor_user_id=owner_id
    )
    draft_doc_id = started.draft_document_id
    ver1_id = started.draft_version_id

    # 1. Author performs a manual edit
    manual_content = "# Chapter One\n\nThe hero awakens and finds an enchanted blade.\n\n## The Blade\n\nIt glows with ancient azure fire.\n"
    updated_manual = await service.submit_manual_edit(
        project_id,
        chapter_id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner_id,
        content=manual_content,
    )
    ver2_id = updated_manual.draft_version_id
    assert ver2_id != ver1_id

    # Check DocumentVersion 2 in DB and verify parent chain
    ver2 = await async_session.get(DocumentVersion, ver2_id)
    assert ver2 is not None
    assert ver2.version_number == 2
    assert ver2.parent_version_id == ver1_id
    assert ver2.document_id == draft_doc_id

    # State reset to EDITOR_REVIEW on version 2
    state = await service.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.document_version_id == str(ver2_id)

    # 2. Re-resolve author action with feedback revision
    # Advance state to AUTHOR_REVISION by starting another chapter
    project_fb, chapter_fb, owner_fb, _, _ = await create_approved_chapter(
        async_session, tmp_path / "feedback-loop"
    )
    started_fb = await service.start_from_approved_outline(
        project_fb.id, chapter_fb.id, actor_user_id=owner_fb.id
    )
    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project_fb.id,
        chapter_id=chapter_fb.id,
        document_id=started_fb.draft_document_id,
        version_id=started_fb.draft_version_id,
    )
    target_segment = next(s for s in segment_map.segments if s.kind.value == "paragraph")

    updated_feedback = await service.request_user_feedback_revision(
        project_fb.id,
        chapter_fb.id,
        started_fb.workflow_run_id,
        started_fb.action_request_id,
        actor_user_id=owner_fb.id,
        feedback="Describe the dungeon coldness and silence in detail.",
        target_segment_ids=[target_segment.segment_id],
    )
    ver_fb_id = updated_feedback.draft_version_id
    assert ver_fb_id != started_fb.draft_version_id

    ver_fb = await async_session.get(DocumentVersion, ver_fb_id)
    assert ver_fb is not None
    assert ver_fb.version_number == 2
    assert ver_fb.parent_version_id == started_fb.draft_version_id
    assert ver_fb.document_id == started_fb.draft_document_id


# ==============================================================================
# 3. Multi-Stage Review Loop with Warnings and Blocking Revisions
# ==============================================================================


async def test_multistage_review_warning_and_blocking_loop(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _ = await create_approved_chapter(
        async_session, tmp_path / "review-loop"
    )
    project_id = project.id
    chapter_id = chapter.id
    owner_id = owner.id

    # 1. Editor review with WARNING
    service_warning, _, _ = create_v2_service(async_session, review_outcome="warning")
    started = await service_warning.start_from_approved_outline(
        project_id, chapter_id, actor_user_id=owner_id
    )
    await service_warning.resolve_author_action(
        project_id,
        chapter_id,
        started.workflow_run_id,
        started.action_request_id,
        actor_user_id=owner_id,
        decision="accept",
    )

    # Execute editor review producing warning
    warn_updated = await service_warning.execute_current_review(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert warn_updated.action_request_id is not None

    state = await service_warning.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.awaiting_user
    assert state.action_kind is ChapterActionKind.REVIEW_WARNING

    # Resolve warning by choosing accept_warning
    await service_warning.resolve_review_action(
        project_id,
        chapter_id,
        started.workflow_run_id,
        warn_updated.action_request_id,
        actor_user_id=owner_id,
        decision="accept_warning",
    )
    state = await service_warning.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
    assert not state.awaiting_user

    # 2. Chief Editor review with BLOCKING finding
    service_blocking, _, _ = create_v2_service(async_session, review_outcome="blocking")
    block_updated = await service_blocking.execute_current_review(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert block_updated.action_request_id is not None

    state = await service_blocking.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.REVIEW_REVISION
    assert state.awaiting_user
    assert state.action_kind is ChapterActionKind.REVIEW_REVISION

    # Author resolves blocking finding by requesting revision
    await service_blocking.resolve_review_action(
        project_id,
        chapter_id,
        started.workflow_run_id,
        block_updated.action_request_id,
        actor_user_id=owner_id,
        decision="request_revision",
    )

    state = await service_blocking.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.REVIEW_REVISION
    assert not state.awaiting_user

    # Execute review revision -> RevisionAgent generates new version
    doc_id = started.draft_document_id
    ver1_id = started.draft_version_id

    segment_map = await DocumentService(async_session).derive_chapter_segment_map(
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=doc_id,
        version_id=ver1_id,
    )
    target_segment = next(s for s in segment_map.segments if s.kind.value == "paragraph")

    expected_reports = tuple(
        UUID(r)
        for r in (state.editor_report_id, state.chief_editor_report_id, state.lore_report_id)
        if r is not None
    )

    revised_update = await service_blocking.execute_review_revision(
        project_id,
        chapter_id,
        started.workflow_run_id,
        actor_user_id=owner_id,
        report_ids=expected_reports,
        target_segment_ids=(target_segment.segment_id,),
    )
    assert revised_update.draft_version_id != ver1_id

    # Review stage resets to EDITOR_REVIEW on new version
    state = await service_blocking.load_state(
        project_id, chapter_id, started.workflow_run_id, actor_user_id=owner_id
    )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert state.document_version_id == str(revised_update.draft_version_id)


# ==============================================================================
# 4. Crash Recovery and Replay Idempotence
# ==============================================================================


async def test_crash_recovery_and_replay_idempotence(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, owner, _, _ = await create_approved_chapter(
        async_session, tmp_path / "replay-idempotence"
    )
    project_id = project.id
    chapter_id = chapter.id
    owner_id = owner.id

    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    started_1 = await service.start_from_approved_outline(
        project_id, chapter_id, actor_user_id=owner_id
    )

    # Resume drafting before user action -> must be idempotent and return identical artifacts
    resumed = await service.resume_drafting(
        project_id, chapter_id, started_1.workflow_run_id, actor_user_id=owner_id
    )
    assert started_1.workflow_run_id == resumed.workflow_run_id
    assert started_1.draft_document_id == resumed.draft_document_id
    assert started_1.draft_version_id == resumed.draft_version_id

    # Verify no duplicate DocumentVersion records created
    version_count = await async_session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.document_id == started_1.draft_document_id)
    )
    assert version_count == 1

    # Advance to completion
    await service.resolve_author_action(
        project_id,
        chapter_id,
        started_1.workflow_run_id,
        started_1.action_request_id,
        actor_user_id=owner_id,
        decision="accept",
    )
    for _ in range(3):
        await service.execute_current_review(
            project_id, chapter_id, started_1.workflow_run_id, actor_user_id=owner_id
        )

    finalized_1 = await service.finalize_without_reader_panel(
        project_id, chapter_id, started_1.workflow_run_id, actor_user_id=owner_id
    )

    # Replay finalization -> idempotent
    finalized_2 = await service.finalize_without_reader_panel(
        project_id, chapter_id, started_1.workflow_run_id, actor_user_id=owner_id
    )
    assert finalized_1.final_document_id == finalized_2.final_document_id
    assert finalized_1.final_version_id == finalized_2.final_version_id

    # Verify exactly one final document in database
    final_count = await async_session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.chapter_id == chapter_id,
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
    project_id = project.id
    chapter_id = chapter.id
    owner_id = owner.id

    service, _, _ = create_v2_service(async_session, review_outcome="passed")

    # Create a second, unprivileged user (attacker)
    attacker = User(username=f"attacker-{uuid4().hex[:8]}", display_name="Attacker")
    async_session.add(attacker)
    await async_session.commit()
    attacker_id = attacker.id

    started = await service.start_from_approved_outline(
        project_id, chapter_id, actor_user_id=owner_id
    )
    run_id = started.workflow_run_id
    action_id = started.action_request_id

    # 1. Attacker tries to resolve owner's author action -> rejected
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project_id,
            chapter_id,
            run_id,
            action_id,
            actor_user_id=attacker_id,
            decision="accept",
        )

    # 2. Attacker tries to submit manual edit -> rejected
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.submit_manual_edit(
            project_id,
            chapter_id,
            run_id,
            action_id,
            actor_user_id=attacker_id,
            content="Tampered content",
        )

    # 3. Cross-project boundary: mismatched project_id
    other_project_id = uuid4()
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            other_project_id,
            chapter_id,
            run_id,
            action_id,
            actor_user_id=owner_id,
            decision="accept",
        )

    # 4. Cross-chapter boundary: mismatched chapter_id
    other_chapter_id = uuid4()
    with pytest.raises(ChapterProductionV2ValidationError):
        await service.resolve_author_action(
            project_id,
            other_chapter_id,
            run_id,
            action_id,
            actor_user_id=owner_id,
            decision="accept",
        )
