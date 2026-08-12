from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    Project,
    User,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentService
from app.services.initial_run_bootstrap import InitialRunBootstrap


pytestmark = pytest.mark.integration


async def _approved(
    session: AsyncSession, workspace: Path
) -> tuple[Project, Chapter, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"owner-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"bootstrap-{uuid4().hex}",
        title="Bootstrap",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id,
        chapter_number=2,
        title="Bootstrap",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()
    outline = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline",
        path=f"chapters/{chapter.id}-outline.md",
        content="# Arrival\n\nReach the gate.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
        change_summary="Approved outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    return project, chapter, owner


def _bootstrap(engine: object) -> InitialRunBootstrap:
    return InitialRunBootstrap(
        ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine)), True  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_zero_creates_exact_pristine_run_without_touching_caller(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)
    try:
        run_id = await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        )
        assert pending in async_session.new
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, run_id)
            assert run is not None
            assert run.next_node is None
            assert run.metadata_["provider_attempt"] is None
            checkpoints = list(
                await session.scalars(
                    select(WorkflowCheckpoint).where(
                        WorkflowCheckpoint.workflow_run_id == run_id
                    )
                )
            )
            assert len(checkpoints) == 1
            assert checkpoints[0].checkpoint_index == 0
            assert checkpoints[0].state_json["document_id"] is None
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_replay_and_concurrent_start_reuse_one_exact_run(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        first, second = await asyncio.gather(
            _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=owner.id
            ),
            _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=owner.id
            ),
        )
        assert first == second
        assert await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        ) == first
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkflowRun).where(
                    WorkflowRun.chapter_id == chapter.id
                )
            ) == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_documented_legacy_metadata_is_normalized_on_exact_reuse(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        run_id = await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            run = await session.get(WorkflowRun, run_id)
            assert run is not None
            run.metadata_ = {
                key: value
                for key, value in run.metadata_.items()
                if key != "reviewer_claim"
            }
            await session.commit()
        assert await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        ) == run_id
        async with maker() as session:
            run = await session.get(WorkflowRun, run_id)
            assert run is not None and run.metadata_["reviewer_claim"] is None
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "corruption",
    ("metadata", "missing", "duplicate", "candidate", "next_node"),
)
async def test_malformed_existing_evidence_fails_closed(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        run_id = await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            run = await session.get(WorkflowRun, run_id)
            checkpoint = await session.scalar(
                select(WorkflowCheckpoint).where(
                    WorkflowCheckpoint.workflow_run_id == run_id
                )
            )
            assert run is not None and checkpoint is not None
            if corruption == "metadata":
                run.metadata_ = {"contract_version": "chapter-production-v2"}
            elif corruption == "missing":
                await session.delete(checkpoint)
            elif corruption == "duplicate":
                session.add(
                    WorkflowCheckpoint(
                        workflow_run_id=run_id,
                        checkpoint_index=1,
                        node_name=checkpoint.node_name,
                        state_json=dict(checkpoint.state_json),
                    )
                )
            elif corruption == "candidate":
                checkpoint.state_json = {
                    **checkpoint.state_json,
                    "document_id": str(uuid4()),
                    "document_version_id": str(uuid4()),
                    "content_hash": "a" * 64,
                }
            else:
                run.next_node = "drafting"
            await session.commit()
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            await _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=owner.id
            )
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_wrong_owner_and_active_other_operation_fail_before_new_run(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    outsider = User(username=f"other-{uuid4().hex}", display_name="Other")
    async_session.add(outsider)
    await async_session.commit()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        with pytest.raises(ChapterProductionV2ValidationError):
            await _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=outsider.id
            )
        first = await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            fresh_chapter = await session.get(Chapter, chapter.id)
            assert fresh_chapter is not None
            outline = await session.get(Document, fresh_chapter.current_outline_document_id)
            assert outline is not None
            await DocumentService(session).write_document(
                document_id=outline.id,
                content="# Arrival\n\nReach the changed gate.\n",
                source=DocumentSource.USER,
                expected_current_version_id=outline.current_version_id,
                actor_user_id=owner.id,
                change_summary="Change approved outline.",
            )
        with pytest.raises(ChapterProductionV2ValidationError):
            await _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=owner.id
            )
        assert first is not None
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("duplicate", "cross_scope"))
async def test_duplicate_or_cross_scope_exact_operation_fails_closed(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path / "first")
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        run_id = await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            run = await session.get(WorkflowRun, run_id)
            assert run is not None
            target_project_id = project.id
            target_chapter_id = chapter.id
            if corruption == "cross_scope":
                other_project, other_chapter, _ = await _approved(
                    session, tmp_path / "second"
                )
                target_project_id = other_project.id
                target_chapter_id = other_chapter.id
            session.add(
                WorkflowRun(
                    project_id=target_project_id,
                    chapter_id=target_chapter_id,
                    workflow_type=run.workflow_type,
                    status=run.status,
                    current_node=run.current_node,
                    next_node=run.next_node,
                    awaiting_user=run.awaiting_user,
                    metadata_=dict(run.metadata_),
                )
            )
            await session.commit()
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            await _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=owner.id
            )
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_commit_ack_loss_keeps_one_durable_run_and_fixed_error(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    original = AsyncSession.commit

    async def commit_then_raise(session: AsyncSession) -> None:
        await original(session)
        raise RuntimeError("PRIVATE acknowledgement")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_raise)
    try:
        with pytest.raises(ChapterProductionV2CommitIndeterminateError) as raised:
            await _bootstrap(engine).start_or_resume(
                project.id, chapter.id, actor_user_id=owner.id
            )
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        monkeypatch.setattr(AsyncSession, "commit", original)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkflowRun).where(
                    WorkflowRun.chapter_id == chapter.id
                )
            ) == 1
            run_id = await session.scalar(
                select(WorkflowRun.id).where(WorkflowRun.chapter_id == chapter.id)
            )
        assert run_id is not None
        assert await _bootstrap(engine).start_or_resume(
            project.id, chapter.id, actor_user_id=owner.id
        ) == run_id
    finally:
        await engine.dispose()
