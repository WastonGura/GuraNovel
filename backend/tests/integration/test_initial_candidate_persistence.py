from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.chapter_writer_agents import WriterAgent
from app.agents.chapter_writer_fakes import DeterministicChapterWriterProvider
from app.models import (
    ActionRequest,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    User,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
)
from app.services.document_service import DocumentService
from app.services.document_service import DocumentCommitIndeterminateError
from app.services.initial_candidate_persistence import (
    InitialCandidateIdentity,
    InitialCandidatePersistence,
)
from app.services.initial_provider_handoff import InitialProviderHandoff


pytestmark = pytest.mark.integration


async def _approved(
    session: AsyncSession, workspace: Path,
) -> tuple[Project, Chapter, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"candidate-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"candidate-{uuid4().hex}", title="Candidate",
        workspace_root=str(workspace), owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id, chapter_number=1,
        title="Candidate", status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()
    outline = await DocumentService(session).create_document(
        project_id=project.id, chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline", path=f"chapters/{chapter.id}-outline.md",
        content="# Arrival\n\nReach the gate.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent", change_summary="Approved outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    return project, chapter, owner


async def _result(
    engine: object, project: Project, chapter: Chapter, owner: User,
):
    lease = ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine))  # type: ignore[arg-type]
    handoff = InitialProviderHandoff(
        lease, WriterAgent(DeterministicChapterWriterProvider()), True
    )
    return lease, await handoff.execute(
        project.id, chapter.id, actor_user_id=owner.id
    )


@pytest.mark.anyio
async def test_persist_and_replay_adopt_one_candidate_without_finalizing(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        persistence = InitialCandidatePersistence(lease, True)
        first = await persistence.persist(result)
        second = await InitialCandidatePersistence(lease, True).persist(result)
        assert type(first) is InitialCandidateIdentity and second == first
        assert "Reach the gate" not in repr(first)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, first.workflow_run_id)
            live_chapter = await session.get(Chapter, chapter.id)
            assert run is not None and run.status == "DRAFTING"
            assert run.metadata_["provider_attempt"]["status"] == "claimed"
            assert live_chapter is not None and live_chapter.current_draft_document_id is None
            assert await session.scalar(select(func.count()).select_from(Document).where(
                Document.type == DocumentType.CHAPTER_DRAFT.value
            )) == 1
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.workflow_run_id == run.id
            )) == 1
            assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 0
            assert await session.scalar(select(func.count()).select_from(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == run.id
            )) == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_commit_ack_replay_adopts_the_single_durable_candidate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        persistence = InitialCandidatePersistence(lease, True)
        import app.services.initial_candidate_persistence as candidate_module

        original = candidate_module._commit
        calls = 0

        async def commit_then_raise(session: AsyncSession) -> None:
            nonlocal calls
            calls += 1
            await original(session)
            if calls == 1:
                raise ChapterProductionV2CommitIndeterminateError()

        monkeypatch.setattr(candidate_module, "_commit", commit_then_raise)
        with pytest.raises(ChapterProductionV2CommitIndeterminateError):
            await persistence.persist(result)
        identity = await persistence.persist(result)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.workflow_run_id == identity.workflow_run_id
            )) == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_duplicate_run_bound_candidate_evidence_fails_closed(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        persistence = InitialCandidatePersistence(lease, True)
        await persistence.persist(result)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            version = await session.scalar(select(DocumentVersion).where(
                DocumentVersion.workflow_run_id == result.workflow_run_id
            ))
            assert version is not None
            duplicate = DocumentVersion(
                document_id=version.document_id, version_number=2,
                parent_version_id=version.id, source=version.source,
                agent_role=version.agent_role, workflow_run_id=version.workflow_run_id,
                content_hash=version.content_hash, byte_size=version.byte_size,
                word_count=version.word_count, file_path=version.file_path,
                snapshot_path=f".versions/{version.document_id}/v0002.md",
                change_summary=version.change_summary, metadata_=dict(version.metadata_),
            )
            session.add(duplicate)
            await session.commit()
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await persistence.persist(result)
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("foreign", "malformed"))
async def test_foreign_or_malformed_candidate_evidence_fails_closed(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        scope = result.generation.scope
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            metadata = {
                "contract_version": "chapter-production-v2",
                "operation_key": scope.operation_key,
            }
            if corruption == "foreign":
                metadata["attempt_id"] = str(scope.attempt_id)
            await DocumentService(session).create_document(
                project_id=project.id, chapter_id=chapter.id,
                document_type=DocumentType.CHAPTER_DRAFT,
                title="Foreign", path=f"chapters/{corruption}-{uuid4()}.md",
                content="# Foreign\n",
                source=DocumentSource.WRITER_AGENT,
                agent_role="writer_agent",
                workflow_run_id=(None if corruption == "foreign" else scope.workflow_run_id),
                change_summary="Foreign candidate evidence.",
                version_metadata=metadata,
            )
            await session.commit()
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await InitialCandidatePersistence(lease, True).persist(result)
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("owner", "outline", "attempt"))
async def test_live_source_or_attempt_drift_writes_no_candidate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if corruption == "owner":
                replacement = User(
                    username=f"replacement-{uuid4().hex}", display_name="Replacement"
                )
                session.add(replacement)
                await session.flush()
                live_project = await session.get(Project, project.id)
                assert live_project is not None
                live_project.owner_id = replacement.id
            elif corruption == "outline":
                live_chapter = await session.get(Chapter, chapter.id)
                assert live_chapter is not None
                await DocumentService(session).write_document(
                    document_id=live_chapter.current_outline_document_id,
                    content="# Changed\n\nA different outline.\n",
                    source=DocumentSource.USER,
                    expected_current_version_id=result.request.approved_outline.version_id,
                    actor_user_id=owner.id,
                    change_summary="Changed approved outline.",
                )
            else:
                run = await session.get(WorkflowRun, result.workflow_run_id)
                assert run is not None
                attempt = {**run.metadata_["provider_attempt"], "key": "f" * 64}
                run.metadata_ = {**run.metadata_, "provider_attempt": attempt}
            await session.commit()
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await InitialCandidatePersistence(lease, True).persist(result)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.workflow_run_id == result.workflow_run_id
            )) == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_stale_generation_cannot_adopt_the_new_generation_candidate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease = ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine))
        handoff = InitialProviderHandoff(
            lease, WriterAgent(DeterministicChapterWriterProvider()), True
        )
        first = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        await handoff.acknowledge_no_write(first.generation)
        second = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert first.generation.attempt.attempt_id != second.generation.attempt.attempt_id
        persistence = InitialCandidatePersistence(lease, True)
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await persistence.persist(first)
        identity = await persistence.persist(second)
        assert identity.attempt_id == second.generation.attempt.attempt_id
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_file_materialization_failure_retries_the_durable_candidate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        persistence = InitialCandidatePersistence(lease, True)
        original = DocumentService.write_staged_files
        calls = 0

        def fail_once(
            service: DocumentService, document: Document,
            writes: tuple[tuple[str, str], ...],
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise DocumentCommitIndeterminateError()
            original(service, document, writes)

        monkeypatch.setattr(DocumentService, "write_staged_files", fail_once)
        with pytest.raises(ChapterProductionV2CommitIndeterminateError):
            await persistence.persist(result)
        identity = await persistence.persist(result)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            document = await session.get(Document, identity.document_id)
            version = await session.get(DocumentVersion, identity.version_id)
            assert document is not None and version is not None
            expected = "\n\n".join(
                segment.content for segment in result.candidate.segments
            ) + "\n"
            assert (tmp_path / document.path).read_text(encoding="utf-8") == expected
            assert (tmp_path / version.snapshot_path).read_text(encoding="utf-8") == expected
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_two_fresh_sessions_persist_one_candidate(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    try:
        lease, result = await _result(engine, project, chapter, owner)
        first, second = await asyncio.gather(
            InitialCandidatePersistence(lease, True).persist(result),
            InitialCandidatePersistence(lease, True).persist(result),
        )
        assert first == second
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.workflow_run_id == result.workflow_run_id
            )) == 1
    finally:
        await engine.dispose()
