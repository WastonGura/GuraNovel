from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    ReviewMode,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowRun,
    WorkflowType,
)
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterFailureReconciliationBinding,
    ChapterFailureReconciliationOutcome,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
    ChapterReviewBinding,
    ChapterReviewOutcome,
    ChapterReviewStage,
    LegacyChapterProductionSnapshot,
)


CONTENT_HASH = "a" * 64
NEW_CONTENT_HASH = "b" * 64
POLICY = "chapter-quality-v1"


@asynccontextmanager
async def fresh_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def report_binding(report: ReviewReport, stage: ChapterReviewStage) -> ChapterReviewBinding:
    assert report.workflow_run_id is not None
    assert report.chapter_id is not None
    assert report.target_document_id is not None
    assert report.target_version_id is not None
    return ChapterReviewBinding(
        report_id=str(report.id),
        stage=stage,
        workflow_run_id=str(report.workflow_run_id),
        chapter_id=str(report.chapter_id),
        document_id=str(report.target_document_id),
        document_version_id=str(report.target_version_id),
        review_mode=report.review_mode,
        reviewer_agent_role=report.reviewer_agent_role,
        passed=report.passed,
    )


def action_binding(
    action: ActionRequest,
    kind: ChapterActionKind,
    *,
    pending_count: int,
    document: Document,
    current_version: DocumentVersion,
) -> ChapterActionBinding:
    assert action.chapter_id is not None
    assert document.current_version_id == current_version.id
    target = action.metadata_
    return ChapterActionBinding(
        action_request_id=str(action.id),
        workflow_run_id=str(action.workflow_run_id),
        chapter_id=str(action.chapter_id),
        request_type=action.request_type,
        kind=kind,
        status=ActionRequestStatus(action.status),
        pending_count=pending_count,
        document_id=target["document_id"],
        document_version_id=target["document_version_id"],
        content_hash=target["content_hash"],
        current_document_id=str(document.id),
        current_document_version_id=str(current_version.id),
        current_content_hash=current_version.content_hash,
    )


def failure_reconciliation_binding(
    state: ChapterProductionState,
    outcome: ChapterFailureReconciliationOutcome,
    document: Document,
    current_version: DocumentVersion,
) -> ChapterFailureReconciliationBinding:
    assert document.chapter_id == UUID(state.chapter_id)
    assert document.current_version_id == current_version.id
    assert state.failure_code is not None
    return ChapterFailureReconciliationBinding(
        workflow_run_id=state.chapter_workflow_run_id,
        chapter_id=state.chapter_id,
        failure_code=state.failure_code,
        outcome=outcome,
        document_id=str(document.id),
        current_document_version_id=str(current_version.id),
        current_content_hash=current_version.content_hash,
    )


async def seed_ready_checkpoint(
    session: AsyncSession,
) -> tuple[UUID, UUID, UUID, UUID, ChapterProductionState]:
    project = Project(
        slug=f"chapter-v2-{uuid4()}",
        title="Chapter V2 checkpoint test",
        workspace_root="/tmp/guranovel-chapter-v2",
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1, title="A durable chapter")
    session.add(chapter)
    await session.flush()
    run = WorkflowRun(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status=ChapterProductionStatus.DRAFTING.value,
        current_node="drafting",
        awaiting_user=False,
    )
    session.add(run)
    await session.flush()
    document = Document(
        project_id=project.id,
        chapter_id=chapter.id,
        type=DocumentType.CHAPTER_DRAFT.value,
        title="Chapter draft",
        path="chapters/chapter_001/draft.md",
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        source=DocumentSource.WRITER_AGENT.value,
        agent_role="writer_agent",
        workflow_run_id=run.id,
        content_hash=CONTENT_HASH,
        byte_size=10,
        word_count=2,
        file_path="chapters/chapter_001/draft.md",
    )
    session.add(version)
    await session.flush()
    document.current_version_id = version.id

    reports = {
        ChapterReviewStage.EDITOR: ReviewReport(
            project_id=project.id,
            chapter_id=chapter.id,
            workflow_run_id=run.id,
            review_mode=ReviewMode.CHAPTER_EDITOR.value,
            reviewer_agent_role="editor_agent",
            target_document_id=document.id,
            target_version_id=version.id,
            passed=True,
            summary="passed",
        ),
        ChapterReviewStage.CHIEF_EDITOR: ReviewReport(
            project_id=project.id,
            chapter_id=chapter.id,
            workflow_run_id=run.id,
            review_mode=ReviewMode.CHAPTER_CHIEF_FINAL.value,
            reviewer_agent_role="chief_editor_agent",
            target_document_id=document.id,
            target_version_id=version.id,
            passed=True,
            summary="passed",
        ),
        ChapterReviewStage.LORE: ReviewReport(
            project_id=project.id,
            chapter_id=chapter.id,
            workflow_run_id=run.id,
            review_mode=ReviewMode.CHAPTER_FINAL_LORE.value,
            reviewer_agent_role="lore_agent",
            target_document_id=document.id,
            target_version_id=version.id,
            passed=True,
            summary="passed",
        ),
    }
    session.add_all(list(reports.values()))
    await session.flush()

    author_action = ActionRequest(
        workflow_run_id=run.id,
        project_id=project.id,
        chapter_id=chapter.id,
        request_type="chapter_author_revision",
        status=ActionRequestStatus.PENDING.value,
        prompt="",
        options=[],
        metadata_={
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "content_hash": version.content_hash,
        },
    )
    session.add(author_action)
    await session.flush()
    live_author_action = action_binding(
        author_action,
        ChapterActionKind.AUTHOR_REVISION,
        pending_count=1,
        document=document,
        current_version=version,
    )
    state = ChapterProductionState.initial(
        chapter_workflow_run_id=str(run.id),
        chapter_id=str(chapter.id),
        review_policy_version=POLICY,
        chief_editor_required=True,
    ).submit_draft(
        document_id=str(document.id),
        document_version_id=str(version.id),
        content_hash=CONTENT_HASH,
        action=live_author_action,
    ).resolve_action(
        action=live_author_action,
        decision=ChapterActionDecision.ACCEPT,
    )
    author_action.status = ActionRequestStatus.APPROVED.value
    author_action.user_decision = ChapterActionDecision.ACCEPT.value
    for stage in (
        ChapterReviewStage.EDITOR,
        ChapterReviewStage.CHIEF_EDITOR,
        ChapterReviewStage.LORE,
    ):
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=report_binding(reports[stage], stage),
        )
    assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    state = state.finalize_revision_ready(
        document_id=str(document.id),
        current_document_version_id=str(document.current_version_id),
        version_content_hash=version.content_hash,
        editor_report=report_binding(
            reports[ChapterReviewStage.EDITOR], ChapterReviewStage.EDITOR
        ),
        chief_editor_report=report_binding(
            reports[ChapterReviewStage.CHIEF_EDITOR], ChapterReviewStage.CHIEF_EDITOR
        ),
        lore_report=report_binding(
            reports[ChapterReviewStage.LORE], ChapterReviewStage.LORE
        ),
    )
    assert state.status is ChapterProductionStatus.REVISION_READY
    run.status = state.status.value
    run.current_node = state.current_node
    run.awaiting_user = state.awaiting_user
    session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=0,
            node_name=state.current_node,
            state_json=state.to_checkpoint(),
        )
    )
    await session.commit()
    return run.id, chapter.id, document.id, version.id, state


async def latest_checkpoint(
    session: AsyncSession, run_id: UUID
) -> WorkflowCheckpoint:
    checkpoint = await session.scalar(
        select(WorkflowCheckpoint)
        .where(WorkflowCheckpoint.workflow_run_id == run_id)
        .order_by(WorkflowCheckpoint.checkpoint_index.desc())
    )
    assert checkpoint is not None
    return checkpoint


@pytest.mark.integration
@pytest.mark.anyio
async def test_checkpoint_restart_round_trip_revalidates_live_ready_references_and_continues(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    run_id, chapter_id, document_id, version_id, expected = await seed_ready_checkpoint(
        async_session
    )

    async with fresh_session(integration_database_url) as restarted:
        run = await restarted.get(WorkflowRun, run_id)
        document = await restarted.get(Document, document_id)
        version = await restarted.get(DocumentVersion, version_id)
        checkpoint = await latest_checkpoint(restarted, run_id)
        assert run is not None and document is not None and version is not None
        reports = {
            report.review_mode: report
            for report in await restarted.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        }
        restored = ChapterProductionState.from_revision_ready_checkpoint(
            checkpoint.state_json,
            workflow_run_id=str(run.id),
            chapter_id=str(run.chapter_id),
            run_workflow_type=run.workflow_type,
            run_status=run.status,
            run_current_node=run.current_node,
            run_awaiting_user=run.awaiting_user,
            checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
            checkpoint_node_name=checkpoint.node_name,
            document_id=str(document.id),
            current_document_version_id=str(document.current_version_id),
            version_content_hash=version.content_hash,
            editor_report=report_binding(
                reports[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
            ),
            chief_editor_report=report_binding(
                reports[ReviewMode.CHAPTER_CHIEF_FINAL.value],
                ChapterReviewStage.CHIEF_EDITOR,
            ),
            lore_report=report_binding(
                reports[ReviewMode.CHAPTER_FINAL_LORE.value], ChapterReviewStage.LORE
            ),
        )
        assert restored == expected

        archived = restored.begin_archive_update()
        run.status = archived.status.value
        run.current_node = archived.current_node
        run.awaiting_user = archived.awaiting_user
        restarted.add(
            WorkflowCheckpoint(
                workflow_run_id=run.id,
                checkpoint_index=1,
                node_name=archived.current_node,
                state_json=archived.to_checkpoint(),
            )
        )
        await restarted.commit()

    async with fresh_session(integration_database_url) as resumed_again:
        run = await resumed_again.get(WorkflowRun, run_id)
        document = await resumed_again.get(Document, document_id)
        version = await resumed_again.get(DocumentVersion, version_id)
        checkpoint = await latest_checkpoint(resumed_again, run_id)
        reports = {
            report.review_mode: report
            for report in await resumed_again.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        }
        assert (
            run is not None
            and run.chapter_id == chapter_id
            and document is not None
            and version is not None
        )
        restored = ChapterProductionState.from_revision_ready_checkpoint(
            checkpoint.state_json,
            workflow_run_id=str(run.id),
            chapter_id=str(run.chapter_id),
            run_workflow_type=run.workflow_type,
            run_status=run.status,
            run_current_node=run.current_node,
            run_awaiting_user=run.awaiting_user,
            checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
            checkpoint_node_name=checkpoint.node_name,
            document_id=str(document.id),
            current_document_version_id=str(document.current_version_id),
            version_content_hash=version.content_hash,
            editor_report=report_binding(
                reports[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
            ),
            chief_editor_report=report_binding(
                reports[ReviewMode.CHAPTER_CHIEF_FINAL.value],
                ChapterReviewStage.CHIEF_EDITOR,
            ),
            lore_report=report_binding(
                reports[ReviewMode.CHAPTER_FINAL_LORE.value], ChapterReviewStage.LORE
            ),
        )
        assert restored.status is ChapterProductionStatus.ARCHIVE_UPDATE
        assert checkpoint.checkpoint_index == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_ready_lineage_failure_restart_requires_full_live_reconciliation_before_restore(
    async_session: AsyncSession,
    integration_database_url: str,
) -> None:
    run_id, _, document_id, version_id, ready = await seed_ready_checkpoint(
        async_session
    )
    run = await async_session.get(WorkflowRun, run_id)
    assert run is not None
    failed = ready.fail(ChapterFailureCode.RECONCILIATION_REQUIRED)
    run.status = failed.status.value
    run.current_node = failed.current_node
    run.awaiting_user = failed.awaiting_user
    async_session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=1,
            node_name=failed.current_node,
            state_json=failed.to_checkpoint(),
        )
    )
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        reloaded_run = await restarted.get(WorkflowRun, run_id)
        document = await restarted.get(Document, document_id)
        version = await restarted.get(DocumentVersion, version_id)
        checkpoint = await latest_checkpoint(restarted, run_id)
        reports = {
            report.review_mode: report
            for report in await restarted.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        }
        assert reloaded_run is not None and document is not None and version is not None
        editor = report_binding(
            reports[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
        )
        chief = report_binding(
            reports[ReviewMode.CHAPTER_CHIEF_FINAL.value],
            ChapterReviewStage.CHIEF_EDITOR,
        )
        lore = report_binding(
            reports[ReviewMode.CHAPTER_FINAL_LORE.value], ChapterReviewStage.LORE
        )
        restored_failed = ChapterProductionState.from_revision_ready_checkpoint(
            checkpoint.state_json,
            workflow_run_id=str(reloaded_run.id),
            chapter_id=str(reloaded_run.chapter_id),
            run_workflow_type=reloaded_run.workflow_type,
            run_status=reloaded_run.status,
            run_current_node=reloaded_run.current_node,
            run_awaiting_user=reloaded_run.awaiting_user,
            checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
            checkpoint_node_name=checkpoint.node_name,
            document_id=str(document.id),
            current_document_version_id=str(document.current_version_id),
            version_content_hash=version.content_hash,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=lore,
        )
        proof = failure_reconciliation_binding(
            restored_failed,
            ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED,
            document,
            version,
        )
        with pytest.raises(ChapterProductionValidationError):
            restored_failed.reconcile_failure(binding=proof)
        restored_ready = restored_failed.reconcile_failure(
            binding=proof,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=lore,
        )
        assert restored_ready == ready
        reloaded_run.status = restored_ready.status.value
        reloaded_run.current_node = restored_ready.current_node
        reloaded_run.awaiting_user = restored_ready.awaiting_user
        restarted.add(
            WorkflowCheckpoint(
                workflow_run_id=reloaded_run.id,
                checkpoint_index=2,
                node_name=restored_ready.current_node,
                state_json=restored_ready.to_checkpoint(),
            )
        )
        await restarted.commit()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("canonical_edit", [False, True])
async def test_ordinary_finalized_failure_recovery_is_live_bound_after_restart(
    async_session: AsyncSession,
    integration_database_url: str,
    canonical_edit: bool,
) -> None:
    run_id, _, document_id, version_id, ready = await seed_ready_checkpoint(
        async_session
    )
    run = await async_session.get(WorkflowRun, run_id)
    assert run is not None
    failed = ready.fail(ChapterFailureCode.ARCHIVE_UNAVAILABLE)
    run.status = failed.status.value
    run.current_node = failed.current_node
    run.awaiting_user = failed.awaiting_user
    async_session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=1,
            node_name=failed.current_node,
            state_json=failed.to_checkpoint(),
        )
    )
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        reloaded_run = await restarted.get(WorkflowRun, run_id)
        document = await restarted.get(Document, document_id)
        version_v1 = await restarted.get(DocumentVersion, version_id)
        checkpoint = await latest_checkpoint(restarted, run_id)
        reports = {
            report.review_mode: report
            for report in await restarted.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        }
        assert (
            reloaded_run is not None
            and document is not None
            and version_v1 is not None
        )
        editor = report_binding(
            reports[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
        )
        chief = report_binding(
            reports[ReviewMode.CHAPTER_CHIEF_FINAL.value],
            ChapterReviewStage.CHIEF_EDITOR,
        )
        lore = report_binding(
            reports[ReviewMode.CHAPTER_FINAL_LORE.value], ChapterReviewStage.LORE
        )
        restored_failed = ChapterProductionState.from_revision_ready_checkpoint(
            checkpoint.state_json,
            workflow_run_id=str(reloaded_run.id),
            chapter_id=str(reloaded_run.chapter_id),
            run_workflow_type=reloaded_run.workflow_type,
            run_status=reloaded_run.status,
            run_current_node=reloaded_run.current_node,
            run_awaiting_user=reloaded_run.awaiting_user,
            checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
            checkpoint_node_name=checkpoint.node_name,
            document_id=str(document.id),
            current_document_version_id=str(document.current_version_id),
            version_content_hash=version_v1.content_hash,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=lore,
        )
        with pytest.raises(ChapterProductionValidationError, match="live readiness"):
            restored_failed.recover()

        current_version = version_v1
        if canonical_edit:
            version_v2 = DocumentVersion(
                document_id=document.id,
                version_number=2,
                parent_version_id=version_v1.id,
                source=DocumentSource.USER.value,
                content_hash=NEW_CONTENT_HASH,
                byte_size=11,
                word_count=2,
                file_path=version_v1.file_path,
            )
            restarted.add(version_v2)
            await restarted.flush()
            document.current_version_id = version_v2.id
            current_version = version_v2

        if canonical_edit:
            with pytest.raises(ChapterProductionValidationError):
                restored_failed.recover_finalized(
                    document_id=str(document.id),
                    current_document_version_id=str(document.current_version_id),
                    version_content_hash=current_version.content_hash,
                    editor_report=editor,
                    chief_editor_report=chief,
                    lore_report=lore,
                )
            assert reloaded_run.status == ChapterProductionStatus.FAILED.value
            assert checkpoint.state_json == failed.to_checkpoint()
        else:
            recovered = restored_failed.recover_finalized(
                document_id=str(document.id),
                current_document_version_id=str(document.current_version_id),
                version_content_hash=current_version.content_hash,
                editor_report=editor,
                chief_editor_report=chief,
                lore_report=lore,
            )
            assert recovered == ready


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario",
    [
        "pending_author_action",
        "review_revision",
        "failed_drafting_committed_author_gate",
        "failed_provider_unavailable",
        "failed_document_commit_indeterminate",
        "failed_persistence_unavailable",
        "failed_reconciliation_required",
    ],
)
async def test_non_finalized_checkpoint_restart_round_trip_preserves_live_state(
    async_session: AsyncSession,
    integration_database_url: str,
    scenario: str,
) -> None:
    project = Project(
        slug=f"chapter-v2-restart-{uuid4()}",
        title="Chapter V2 non-finalized restart test",
        workspace_root="/tmp/guranovel-chapter-v2-restart",
    )
    async_session.add(project)
    await async_session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1)
    async_session.add(chapter)
    await async_session.flush()
    run = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status=ChapterProductionStatus.DRAFTING.value,
        current_node="drafting",
        awaiting_user=False,
    )
    document = Document(
        project_id=project.id,
        chapter_id=chapter.id,
        type=DocumentType.CHAPTER_DRAFT.value,
        title="Restart draft",
        path="chapters/chapter_001/restart.md",
    )
    async_session.add_all([run, document])
    await async_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        source=DocumentSource.WRITER_AGENT.value,
        agent_role="writer_agent",
        workflow_run_id=run.id,
        content_hash=CONTENT_HASH,
        byte_size=10,
        word_count=2,
        file_path="chapters/chapter_001/restart.md",
    )
    async_session.add(version)
    await async_session.flush()
    document.current_version_id = version.id
    author_action = ActionRequest(
        workflow_run_id=run.id,
        project_id=project.id,
        chapter_id=chapter.id,
        request_type="chapter_author_revision",
        status=ActionRequestStatus.PENDING.value,
        prompt="",
        options=[],
        metadata_={
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "content_hash": version.content_hash,
        },
    )
    async_session.add(author_action)
    await async_session.flush()
    live_author = action_binding(
        author_action,
        ChapterActionKind.AUTHOR_REVISION,
        pending_count=1,
        document=document,
        current_version=version,
    )
    state = ChapterProductionState.initial(
        chapter_workflow_run_id=str(run.id),
        chapter_id=str(chapter.id),
        review_policy_version=POLICY,
        chief_editor_required=True,
    ).submit_draft(
        document_id=str(document.id),
        document_version_id=str(version.id),
        content_hash=version.content_hash,
        action=live_author,
    )

    if scenario == "failed_drafting_committed_author_gate":
        state = state.resolve_action(
            action=live_author,
            decision=ChapterActionDecision.REQUEST_REVISION,
        )
        author_action.status = ActionRequestStatus.REVISED.value
        author_action.user_decision = ChapterActionDecision.REQUEST_REVISION.value
    elif scenario != "pending_author_action":
        state = state.resolve_action(
            action=live_author,
            decision=ChapterActionDecision.ACCEPT,
        )
        author_action.status = ActionRequestStatus.APPROVED.value
        author_action.user_decision = ChapterActionDecision.ACCEPT.value
    if scenario == "review_revision":
        report = ReviewReport(
            project_id=project.id,
            chapter_id=chapter.id,
            workflow_run_id=run.id,
            review_mode=ReviewMode.CHAPTER_EDITOR.value,
            reviewer_agent_role="editor_agent",
            target_document_id=document.id,
            target_version_id=version.id,
            passed=False,
            summary="blocking",
        )
        review_action = ActionRequest(
            workflow_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            request_type="chapter_review_revision",
            status=ActionRequestStatus.PENDING.value,
            prompt="",
            options=[],
            metadata_={
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "content_hash": version.content_hash,
            },
        )
        async_session.add_all([report, review_action])
        await async_session.flush()
        state = state.record_review(
            outcome=ChapterReviewOutcome.BLOCKING,
            review=report_binding(report, ChapterReviewStage.EDITOR),
            action=action_binding(
                review_action,
                ChapterActionKind.REVIEW_REVISION,
                pending_count=1,
                document=document,
                current_version=version,
            ),
        )
    elif scenario == "failed_drafting_committed_author_gate":
        state = state.fail(ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE)
        committed_version = DocumentVersion(
            document_id=document.id,
            version_number=2,
            parent_version_id=version.id,
            source=DocumentSource.USER.value,
            content_hash=CONTENT_HASH,
            byte_size=11,
            word_count=2,
            file_path=version.file_path,
        )
        async_session.add(committed_version)
        await async_session.flush()
        document.current_version_id = committed_version.id
        reconciliation_action = ActionRequest(
            workflow_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            request_type="chapter_author_revision",
            status=ActionRequestStatus.PENDING.value,
            prompt="",
            options=[],
            metadata_={
                "document_id": str(document.id),
                "document_version_id": str(committed_version.id),
                "content_hash": committed_version.content_hash,
            },
        )
        async_session.add(reconciliation_action)
        await async_session.flush()
    elif scenario.startswith("failed_"):
        failure_code = {
            "failed_provider_unavailable": ChapterFailureCode.PROVIDER_UNAVAILABLE,
            "failed_document_commit_indeterminate": (
                ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE
            ),
            "failed_persistence_unavailable": ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
            "failed_reconciliation_required": ChapterFailureCode.RECONCILIATION_REQUIRED,
        }[scenario]
        state = state.fail(failure_code)
        if scenario == "failed_document_commit_indeterminate":
            committed_version = DocumentVersion(
                document_id=document.id,
                version_number=2,
                parent_version_id=version.id,
                source=DocumentSource.USER.value,
                content_hash=CONTENT_HASH,
                byte_size=11,
                word_count=2,
                file_path=version.file_path,
            )
            async_session.add(committed_version)
            await async_session.flush()
            document.current_version_id = committed_version.id

    run.status = state.status.value
    run.current_node = state.current_node
    run.awaiting_user = state.awaiting_user
    async_session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=0,
            node_name=state.current_node,
            state_json=state.to_checkpoint(),
        )
    )
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        reloaded_run = await restarted.get(WorkflowRun, run.id)
        reloaded_document = await restarted.get(Document, document.id)
        reloaded_version = await restarted.get(DocumentVersion, version.id)
        checkpoint = await latest_checkpoint(restarted, run.id)
        assert (
            reloaded_run is not None
            and reloaded_run.chapter_id is not None
            and reloaded_document is not None
            and reloaded_version is not None
        )
        reloaded_current_version = await restarted.get(
            DocumentVersion, reloaded_document.current_version_id
        )
        assert reloaded_current_version is not None
        if scenario in {
            "failed_document_commit_indeterminate",
            "failed_drafting_committed_author_gate",
        }:
            assert reloaded_current_version.id != reloaded_version.id
            assert reloaded_current_version.content_hash == reloaded_version.content_hash
        restored = ChapterProductionState.from_checkpoint(checkpoint.state_json)
        restored.validate_persistence_binding(
            workflow_run_id=str(reloaded_run.id),
            chapter_id=str(reloaded_run.chapter_id),
            run_workflow_type=reloaded_run.workflow_type,
            run_status=reloaded_run.status,
            run_current_node=reloaded_run.current_node,
            run_awaiting_user=reloaded_run.awaiting_user,
            checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
            checkpoint_node_name=checkpoint.node_name,
        )
        assert restored == state
        if restored.awaiting_user:
            pending_actions = list(
                await restarted.scalars(
                    select(ActionRequest).where(
                        ActionRequest.workflow_run_id == run.id,
                        ActionRequest.status == ActionRequestStatus.PENDING.value,
                    )
                )
            )
            assert len(pending_actions) == 1
            assert restored.action_kind is not None
            live_action = action_binding(
                pending_actions[0],
                restored.action_kind,
                pending_count=len(pending_actions),
                document=reloaded_document,
                current_version=reloaded_version,
            )
            decision = (
                ChapterActionDecision.ACCEPT
                if restored.action_kind is ChapterActionKind.AUTHOR_REVISION
                else ChapterActionDecision.REQUEST_REVISION
            )
            assert not restored.resolve_action(
                action=live_action,
                decision=decision,
            ).awaiting_user
        else:
            assert restored.status is ChapterProductionStatus.FAILED
            if scenario == "failed_drafting_committed_author_gate":
                with pytest.raises(
                    ChapterProductionValidationError, match="reconciliation"
                ):
                    restored.recover()
                pending_actions = list(
                    await restarted.scalars(
                        select(ActionRequest).where(
                            ActionRequest.workflow_run_id == run.id,
                            ActionRequest.status == ActionRequestStatus.PENDING.value,
                        )
                    )
                )
                assert len(pending_actions) == 1
                reconciled = restored.reconcile_failure(
                    binding=failure_reconciliation_binding(
                        restored,
                        ChapterFailureReconciliationOutcome.CANONICAL_VERSION_COMMITTED,
                        reloaded_document,
                        reloaded_current_version,
                    ),
                    action=action_binding(
                        pending_actions[0],
                        ChapterActionKind.AUTHOR_REVISION,
                        pending_count=1,
                        document=reloaded_document,
                        current_version=reloaded_current_version,
                    ),
                )
                assert reconciled.status is ChapterProductionStatus.AUTHOR_REVISION
                assert reconciled.awaiting_user
                assert reconciled.action_request_id == str(pending_actions[0].id)
                assert reconciled.document_version_id == str(
                    reloaded_current_version.id
                )
                reloaded_run.status = reconciled.status.value
                reloaded_run.current_node = reconciled.current_node
                reloaded_run.awaiting_user = reconciled.awaiting_user
                restarted.add(
                    WorkflowCheckpoint(
                        workflow_run_id=reloaded_run.id,
                        checkpoint_index=1,
                        node_name=reconciled.current_node,
                        state_json=reconciled.to_checkpoint(),
                    )
                )
                await restarted.commit()
                return
            if scenario == "failed_provider_unavailable":
                assert restored.recover().status is ChapterProductionStatus.EDITOR_REVIEW
            else:
                with pytest.raises(
                    ChapterProductionValidationError, match="reconciliation"
                ):
                    restored.recover()
                outcome = (
                    ChapterFailureReconciliationOutcome.CANONICAL_VERSION_COMMITTED
                    if scenario == "failed_document_commit_indeterminate"
                    else ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED
                )
                reconciled = restored.reconcile_failure(
                    binding=failure_reconciliation_binding(
                        restored,
                        outcome,
                        reloaded_document,
                        reloaded_current_version,
                    )
                )
                assert reconciled.status is ChapterProductionStatus.EDITOR_REVIEW
                assert reconciled.document_version_id == str(
                    reloaded_document.current_version_id
                )
                reloaded_run.status = reconciled.status.value
                reloaded_run.current_node = reconciled.current_node
                reloaded_run.awaiting_user = reconciled.awaiting_user
                restarted.add(
                    WorkflowCheckpoint(
                        workflow_run_id=reloaded_run.id,
                        checkpoint_index=1,
                        node_name=reconciled.current_node,
                        state_json=reconciled.to_checkpoint(),
                    )
                )
                await restarted.commit()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "decision",
    [
        ChapterActionDecision.ACCEPT,
        ChapterActionDecision.CANCEL,
        ChapterActionDecision.REQUEST_REVISION,
        ChapterActionDecision.SUBMIT_MANUAL_EDIT,
    ],
)
async def test_pending_v1_action_reconciles_to_same_hash_canonical_v2_without_staying_pending(
    async_session: AsyncSession,
    integration_database_url: str,
    decision: ChapterActionDecision,
) -> None:
    project = Project(
        slug=f"chapter-v2-stale-action-{uuid4()}",
        title="Chapter V2 stale action test",
        workspace_root="/tmp/guranovel-chapter-v2-stale-action",
    )
    async_session.add(project)
    await async_session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1)
    async_session.add(chapter)
    await async_session.flush()
    run = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status=ChapterProductionStatus.DRAFTING.value,
        current_node="drafting",
        awaiting_user=False,
    )
    document = Document(
        project_id=project.id,
        chapter_id=chapter.id,
        type=DocumentType.CHAPTER_DRAFT.value,
        title="Stale action draft",
        path="chapters/chapter_001/stale-action.md",
    )
    async_session.add_all([run, document])
    await async_session.flush()
    version_v1 = DocumentVersion(
        document_id=document.id,
        version_number=1,
        source=DocumentSource.WRITER_AGENT.value,
        agent_role="writer_agent",
        workflow_run_id=run.id,
        content_hash=CONTENT_HASH,
        byte_size=10,
        word_count=2,
        file_path="chapters/chapter_001/stale-action.md",
    )
    async_session.add(version_v1)
    await async_session.flush()
    document.current_version_id = version_v1.id
    pending_action = ActionRequest(
        workflow_run_id=run.id,
        project_id=project.id,
        chapter_id=chapter.id,
        request_type="chapter_author_revision",
        status=ActionRequestStatus.PENDING.value,
        prompt="",
        options=[],
        metadata_={
            "document_id": str(document.id),
            "document_version_id": str(version_v1.id),
            "content_hash": version_v1.content_hash,
        },
    )
    async_session.add(pending_action)
    await async_session.flush()
    state = ChapterProductionState.initial(
        chapter_workflow_run_id=str(run.id),
        chapter_id=str(chapter.id),
        review_policy_version=POLICY,
        chief_editor_required=True,
    ).submit_draft(
        document_id=str(document.id),
        document_version_id=str(version_v1.id),
        content_hash=version_v1.content_hash,
        action=action_binding(
            pending_action,
            ChapterActionKind.AUTHOR_REVISION,
            pending_count=1,
            document=document,
            current_version=version_v1,
        ),
    )
    run.status = state.status.value
    run.current_node = state.current_node
    run.awaiting_user = state.awaiting_user
    checkpoint = WorkflowCheckpoint(
        workflow_run_id=run.id,
        checkpoint_index=0,
        node_name=state.current_node,
        state_json=state.to_checkpoint(),
    )
    async_session.add(checkpoint)
    await async_session.flush()
    version_v2 = DocumentVersion(
        document_id=document.id,
        version_number=2,
        parent_version_id=version_v1.id,
        source=DocumentSource.USER.value,
        content_hash=CONTENT_HASH,
        byte_size=11,
        word_count=2,
        file_path=version_v1.file_path,
    )
    async_session.add(version_v2)
    await async_session.flush()
    document.current_version_id = version_v2.id
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        reloaded_run = await restarted.get(WorkflowRun, run.id)
        reloaded_document = await restarted.get(Document, document.id)
        reloaded_v2 = await restarted.get(DocumentVersion, version_v2.id)
        reloaded_action = await restarted.get(ActionRequest, pending_action.id)
        reloaded_checkpoint = await latest_checkpoint(restarted, run.id)
        assert (
            reloaded_run is not None
            and reloaded_document is not None
            and reloaded_v2 is not None
            and reloaded_action is not None
        )
        restored = ChapterProductionState.from_checkpoint(
            reloaded_checkpoint.state_json
        )
        stale_binding = action_binding(
            reloaded_action,
            ChapterActionKind.AUTHOR_REVISION,
            pending_count=1,
            document=reloaded_document,
            current_version=reloaded_v2,
        )
        kwargs = (
            {
                "document_id": str(reloaded_document.id),
                "document_version_id": str(reloaded_v2.id),
                "content_hash": reloaded_v2.content_hash,
            }
            if decision is ChapterActionDecision.SUBMIT_MANUAL_EDIT
            else {}
        )
        with pytest.raises(ChapterProductionValidationError, match="stale"):
            restored.resolve_action(
                action=stale_binding,
                decision=decision,
                **kwargs,
            )
        await restarted.refresh(reloaded_action)
        await restarted.refresh(reloaded_run)
        assert reloaded_action.status == ActionRequestStatus.PENDING.value
        assert reloaded_action.user_decision is None
        assert reloaded_run.status == ChapterProductionStatus.AUTHOR_REVISION.value
        assert reloaded_run.awaiting_user
        assert restored == state
        assert reloaded_checkpoint.state_json == state.to_checkpoint()
        reconciled = restored.reconcile_stale_action(action=stale_binding)
        reloaded_action.status = ActionRequestStatus.CANCELLED.value
        reloaded_run.status = reconciled.status.value
        reloaded_run.current_node = reconciled.current_node
        reloaded_run.awaiting_user = reconciled.awaiting_user
        restarted.add(
            WorkflowCheckpoint(
                workflow_run_id=reloaded_run.id,
                checkpoint_index=1,
                node_name=reconciled.current_node,
                state_json=reconciled.to_checkpoint(),
            )
        )
        await restarted.commit()

    async with fresh_session(integration_database_url) as resumed:
        resumed_run = await resumed.get(WorkflowRun, run.id)
        resumed_action = await resumed.get(ActionRequest, pending_action.id)
        resumed_checkpoint = await latest_checkpoint(resumed, run.id)
        assert resumed_run is not None and resumed_action is not None
        resumed_state = ChapterProductionState.from_checkpoint(
            resumed_checkpoint.state_json
        )
        assert resumed_action.status == ActionRequestStatus.CANCELLED.value
        assert resumed_run.status == ChapterProductionStatus.EDITOR_REVIEW.value
        assert not resumed_run.awaiting_user
        assert resumed_checkpoint.checkpoint_index == 1
        assert resumed_state == reconciled
        assert resumed_state.document_version_id == str(version_v2.id)
        assert resumed_state.content_hash == version_v2.content_hash
        assert resumed_state.action_request_id is None
        assert resumed_state.editor_report_id is None
        assert resumed_state.chief_editor_report_id is None
        assert resumed_state.lore_report_id is None


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "corruption",
    [
        "workflow_type",
        "run_status",
        "run_node",
        "run_waiting",
        "checkpoint_run",
        "checkpoint_node",
        "checkpoint_shape",
    ],
)
async def test_restart_rejects_corrupt_run_or_checkpoint_projection(
    async_session: AsyncSession,
    integration_database_url: str,
    corruption: str,
) -> None:
    run_id, _, document_id, version_id, _ = await seed_ready_checkpoint(async_session)
    run = await async_session.get(WorkflowRun, run_id)
    checkpoint = await latest_checkpoint(async_session, run_id)
    assert run is not None
    checkpoint_id = checkpoint.id
    if corruption == "workflow_type":
        run.workflow_type = WorkflowType.PROJECT_CREATION.value
    elif corruption == "run_status":
        run.status = ChapterProductionStatus.COMPLETED.value
    elif corruption == "run_node":
        run.current_node = "lore_final_review"
    elif corruption == "run_waiting":
        run.awaiting_user = True
    elif corruption == "checkpoint_run":
        other_run = WorkflowRun(
            project_id=run.project_id,
            chapter_id=run.chapter_id,
            workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
            status=run.status,
            current_node=run.current_node,
            awaiting_user=run.awaiting_user,
        )
        async_session.add(other_run)
        await async_session.flush()
        checkpoint.workflow_run_id = other_run.id
    elif corruption == "checkpoint_node":
        checkpoint.node_name = "revision_ready"
    else:
        checkpoint.state_json = {**checkpoint.state_json, "novel_text": "must not persist"}
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        run = await restarted.get(WorkflowRun, run_id)
        checkpoint = await restarted.get(WorkflowCheckpoint, checkpoint_id)
        document = await restarted.get(Document, document_id)
        version = await restarted.get(DocumentVersion, version_id)
        reports = {
            report.review_mode: report
            for report in await restarted.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        }
        assert (
            run is not None
            and run.chapter_id is not None
            and checkpoint is not None
            and document is not None
            and version is not None
        )
        with pytest.raises(ChapterProductionValidationError):
            ChapterProductionState.from_revision_ready_checkpoint(
                checkpoint.state_json,
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type,
                run_status=run.status,
                run_current_node=run.current_node,
                run_awaiting_user=run.awaiting_user,
                checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                checkpoint_node_name=checkpoint.node_name,
                document_id=str(document.id),
                current_document_version_id=str(document.current_version_id),
                version_content_hash=version.content_hash,
                editor_report=report_binding(
                    reports[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
                ),
                chief_editor_report=report_binding(
                    reports[ReviewMode.CHAPTER_CHIEF_FINAL.value],
                    ChapterReviewStage.CHIEF_EDITOR,
                ),
                lore_report=report_binding(
                    reports[ReviewMode.CHAPTER_FINAL_LORE.value], ChapterReviewStage.LORE
                ),
            )


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("report_corruption", ["missing", "cross_scope", "failed"])
async def test_live_orm_review_binding_failure_cannot_reach_or_persist_revision_ready(
    async_session: AsyncSession,
    integration_database_url: str,
    report_corruption: str,
) -> None:
    run_id, _, _, _, ready = await seed_ready_checkpoint(async_session)
    pre_lore = replace(
        ready,
        status=ChapterProductionStatus.LORE_FINAL_REVIEW,
        current_node="lore_final_review",
        lore_report_id=None,
    )
    run = await async_session.get(WorkflowRun, run_id)
    lore_report = await async_session.scalar(
        select(ReviewReport).where(
            ReviewReport.workflow_run_id == run_id,
            ReviewReport.review_mode == ReviewMode.CHAPTER_FINAL_LORE.value,
        )
    )
    assert run is not None and lore_report is not None
    await async_session.execute(
        delete(WorkflowCheckpoint).where(WorkflowCheckpoint.workflow_run_id == run_id)
    )
    run.status = pre_lore.status.value
    run.current_node = pre_lore.current_node
    if report_corruption == "missing":
        await async_session.delete(lore_report)
    elif report_corruption == "cross_scope":
        other_chapter = Chapter(
            project_id=run.project_id,
            chapter_number=2,
            title="Other chapter",
        )
        async_session.add(other_chapter)
        await async_session.flush()
        lore_report.chapter_id = other_chapter.id
    else:
        lore_report.passed = False
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        run = await restarted.get(WorkflowRun, run_id)
        lore_report = await restarted.scalar(
            select(ReviewReport).where(
                ReviewReport.workflow_run_id == run_id,
                ReviewReport.review_mode == ReviewMode.CHAPTER_FINAL_LORE.value,
            )
        )
        assert run is not None
        assert run.status == ChapterProductionStatus.LORE_FINAL_REVIEW.value
        assert await restarted.scalar(
            select(WorkflowCheckpoint.id).where(
                WorkflowCheckpoint.workflow_run_id == run_id
            )
        ) is None
        live_binding = (
            report_binding(lore_report, ChapterReviewStage.LORE)
            if lore_report is not None
            else None
        )
        with pytest.raises(ChapterProductionValidationError):
            pre_lore.record_review(
                outcome=ChapterReviewOutcome.PASSED,
                review=live_binding,  # type: ignore[arg-type]
            )
        assert pre_lore.status is ChapterProductionStatus.LORE_FINAL_REVIEW
        assert pre_lore.lore_report_id is None
        assert await restarted.scalar(
            select(WorkflowCheckpoint.id).where(
                WorkflowCheckpoint.workflow_run_id == run_id
            )
        ) is None


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "corruption",
    ["stale_version", "hash_mismatch", "missing_report", "cross_scope_report", "failed_report"],
)
async def test_live_orm_finalize_failure_cannot_create_a_ready_checkpoint(
    async_session: AsyncSession,
    integration_database_url: str,
    corruption: str,
) -> None:
    run_id, _, document_id, version_id, ready = await seed_ready_checkpoint(async_session)
    pre_ready = replace(
        ready,
        status=ChapterProductionStatus.LORE_FINAL_REVIEW,
        current_node="lore_final_review",
    )
    run = await async_session.get(WorkflowRun, run_id)
    document = await async_session.get(Document, document_id)
    version = await async_session.get(DocumentVersion, version_id)
    lore_report = await async_session.scalar(
        select(ReviewReport).where(
            ReviewReport.workflow_run_id == run_id,
            ReviewReport.review_mode == ReviewMode.CHAPTER_FINAL_LORE.value,
        )
    )
    assert run is not None and document is not None and version is not None
    assert lore_report is not None
    await async_session.execute(
        delete(WorkflowCheckpoint).where(WorkflowCheckpoint.workflow_run_id == run_id)
    )
    run.status = pre_ready.status.value
    run.current_node = pre_ready.current_node
    if corruption == "stale_version":
        newer = DocumentVersion(
            document_id=document.id,
            version_number=2,
            parent_version_id=version.id,
            source=DocumentSource.USER.value,
            content_hash=NEW_CONTENT_HASH,
            byte_size=11,
            word_count=2,
            file_path=version.file_path,
        )
        async_session.add(newer)
        await async_session.flush()
        document.current_version_id = newer.id
    elif corruption == "hash_mismatch":
        version.content_hash = NEW_CONTENT_HASH
    elif corruption == "missing_report":
        await async_session.delete(lore_report)
    elif corruption == "cross_scope_report":
        other_chapter = Chapter(project_id=run.project_id, chapter_number=2)
        async_session.add(other_chapter)
        await async_session.flush()
        lore_report.chapter_id = other_chapter.id
    else:
        lore_report.passed = False
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        run = await restarted.get(WorkflowRun, run_id)
        document = await restarted.get(Document, document_id)
        version = await restarted.get(DocumentVersion, version_id)
        reports = {
            report.review_mode: report
            for report in await restarted.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        }
        assert run is not None and document is not None and version is not None
        lore = reports.get(ReviewMode.CHAPTER_FINAL_LORE.value)
        with pytest.raises(ChapterProductionValidationError):
            pre_ready.finalize_revision_ready(
                document_id=str(document.id),
                current_document_version_id=str(document.current_version_id),
                version_content_hash=version.content_hash,
                editor_report=report_binding(
                    reports[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
                ),
                chief_editor_report=report_binding(
                    reports[ReviewMode.CHAPTER_CHIEF_FINAL.value],
                    ChapterReviewStage.CHIEF_EDITOR,
                ),
                lore_report=(
                    report_binding(lore, ChapterReviewStage.LORE)
                    if lore is not None
                    else None  # type: ignore[arg-type]
                ),
            )
        assert run.status == ChapterProductionStatus.LORE_FINAL_REVIEW.value
        assert await restarted.scalar(
            select(WorkflowCheckpoint.id).where(
                WorkflowCheckpoint.workflow_run_id == run_id
            )
        ) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_new_current_version_makes_old_ready_checkpoint_stale_without_rewriting_history(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    run_id, _, document_id, old_version_id, expected = await seed_ready_checkpoint(async_session)
    document = await async_session.get(Document, document_id)
    assert document is not None
    new_version = DocumentVersion(
        document_id=document.id,
        version_number=2,
        parent_version_id=old_version_id,
        source=DocumentSource.USER.value,
        content_hash=NEW_CONTENT_HASH,
        byte_size=11,
        word_count=2,
        file_path="chapters/chapter_001/draft.md",
    )
    async_session.add(new_version)
    await async_session.flush()
    document.current_version_id = new_version.id
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        document = await restarted.get(Document, document_id)
        old_version = await restarted.get(DocumentVersion, old_version_id)
        reports = list(
            await restarted.scalars(
                select(ReviewReport).where(ReviewReport.workflow_run_id == run_id)
            )
        )
        assert document is not None and old_version is not None and len(reports) == 3
        by_mode = {report.review_mode: report for report in reports}
        with pytest.raises(ChapterProductionValidationError, match="stale"):
            expected.validate_live_readiness(
                document_id=str(document.id),
                current_document_version_id=str(document.current_version_id),
                version_content_hash=old_version.content_hash,
                editor_report=report_binding(
                    by_mode[ReviewMode.CHAPTER_EDITOR.value], ChapterReviewStage.EDITOR
                ),
                chief_editor_report=report_binding(
                    by_mode[ReviewMode.CHAPTER_CHIEF_FINAL.value],
                    ChapterReviewStage.CHIEF_EDITOR,
                ),
                lore_report=report_binding(
                    by_mode[ReviewMode.CHAPTER_FINAL_LORE.value], ChapterReviewStage.LORE
                ),
            )
        checkpoint = await latest_checkpoint(restarted, run_id)
        assert checkpoint.state_json == expected.to_checkpoint()
        with pytest.raises(ChapterProductionValidationError):
            ChapterProductionState.from_checkpoint(checkpoint.state_json)


@pytest.mark.integration
@pytest.mark.anyio
async def test_legacy_v08_run_remains_readable_without_a_v2_checkpoint_or_ready_capability(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    project = Project(
        slug=f"chapter-v08-{uuid4()}",
        title="Legacy chapter test",
        workspace_root="/tmp/guranovel-chapter-v08",
    )
    async_session.add(project)
    await async_session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1)
    async_session.add(chapter)
    await async_session.flush()
    run = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="completed",
        current_node="approval",
        awaiting_user=False,
    )
    async_session.add(run)
    await async_session.commit()

    async with fresh_session(integration_database_url) as restarted:
        legacy_run = await restarted.get(WorkflowRun, run.id)
        assert legacy_run is not None and legacy_run.chapter_id is not None
        checkpoint = await restarted.scalar(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.workflow_run_id == run.id)
        )
        assert checkpoint is None
        legacy = LegacyChapterProductionSnapshot.from_run_projection(
            workflow_run_id=str(legacy_run.id),
            chapter_id=str(legacy_run.chapter_id),
            status=legacy_run.status,
            current_node=legacy_run.current_node,
            awaiting_user=legacy_run.awaiting_user,
        )
        assert not legacy.is_revision_ready
