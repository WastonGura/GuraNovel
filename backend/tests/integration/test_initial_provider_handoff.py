from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.chapter_writer_agents import WriterAgent
from app.agents.chapter_writer_fakes import DeterministicChapterWriterProvider
from app.llm import ProviderTimeoutError
from app.models import (
    ActionRequest,
    Chapter,
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
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentService
from app.services import initial_provider_handoff as handoff_module
from app.services.initial_provider_handoff import (
    InitialProviderHandoff,
    InitialProviderResult,
)
from app.services.initial_run_bootstrap import InitialRunBootstrap


pytestmark = pytest.mark.integration


async def _approved(
    session: AsyncSession, workspace: Path
) -> tuple[Project, Chapter, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"v3-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"v3-{uuid4().hex}", title="V3", workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id, chapter_number=1, title="V3", status="OUTLINE_APPROVED"
    )
    session.add(chapter)
    await session.commit()
    outline = await DocumentService(session).create_document(
        project_id=project.id, chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline", path=f"chapters/{chapter.id}-outline.md",
        content="# Arrival\n\nReach the gate.\n", source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent", change_summary="Approved outline.",
    )
    chapter.current_outline_document_id = outline.id
    await session.commit()
    return project, chapter, owner


class _FailOnce(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        if self.calls == 1:
            raise ProviderTimeoutError()
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class _FailTwice(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        if self.calls <= 2:
            raise ProviderTimeoutError()
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class _Inspecting(DeterministicChapterWriterProvider):
    def __init__(self, lease: ChapterPhaseSessionLease) -> None:
        self.lease = lease
        self.calls = 0

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        assert self.lease._sessions
        assert all(not session.in_transaction() for session in self.lease._sessions)
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


class _Cancelling(DeterministicChapterWriterProvider):
    async def draft_initial(self, request: object, profile: object) -> object:
        raise asyncio.CancelledError("PRIVATE")


class _Blocking(DeterministicChapterWriterProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def draft_initial(self, request: object, profile: object) -> object:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().draft_initial(request, profile)  # type: ignore[arg-type]


def _handoff(engine: object, provider: object) -> InitialProviderHandoff:
    lease = ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine))  # type: ignore[arg-type]
    return InitialProviderHandoff(lease, WriterAgent(provider), True)


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("metadata_bool", "checkpoint_bool"))
async def test_json_numeric_boolean_pollution_fails_before_provider(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    lease = ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine))
    run_id = await InitialRunBootstrap(lease, True).start_or_resume(
        project.id, chapter.id, actor_user_id=owner.id
    )
    provider = _FailOnce()
    handoff = InitialProviderHandoff(lease, WriterAgent(provider), True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, run_id)
            checkpoint = await session.scalar(select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == run_id
            ))
            assert run is not None and checkpoint is not None
            if corruption == "metadata_bool":
                await session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id)
                    .values(metadata_={**run.metadata_, "chief_editor_required": 1})
                )
            else:
                await session.execute(
                    update(WorkflowCheckpoint)
                    .where(WorkflowCheckpoint.id == checkpoint.id)
                    .values(state_json={**checkpoint.state_json, "awaiting_user": 0})
                )
            await session.commit()
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, run_id)
            checkpoint = await session.scalar(select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == run_id
            ))
            assert run is not None and checkpoint is not None
            polluted = (
                run.metadata_["chief_editor_required"]
                if corruption == "metadata_bool"
                else checkpoint.state_json["awaiting_user"]
            )
            assert type(polluted) is int
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert provider.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_success_is_session_free_and_writes_no_candidate_or_action(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    pending = User(username=f"pending-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)
    try:
        lease = ChapterPhaseSessionLease(ChapterPhaseSessionSource(engine))
        provider = _Inspecting(lease)
        handoff = InitialProviderHandoff(lease, WriterAgent(provider), True)
        result = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert type(result) is InitialProviderResult
        assert provider.calls == 1
        assert pending in async_session.new
        assert "Reach the gate" not in repr(result)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, result.workflow_run_id)
            assert run is not None and run.status == "DRAFTING"
            assert run.metadata_["provider_attempt"]["status"] == "claimed"
            assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 0
            assert await session.scalar(
                select(func.count()).select_from(DocumentVersion).where(
                    DocumentVersion.workflow_run_id == run.id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(WorkflowCheckpoint).where(
                    WorkflowCheckpoint.workflow_run_id == run.id
                )
            ) == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_request_construction_failure_is_fixed_before_provider(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _FailOnce()
    handoff = _handoff(engine, provider)

    def fail_request(*_args: object) -> object:
        raise ValueError("PRIVATE request details")

    monkeypatch.setattr(handoff_module._InitialEvidencePhase, "request", fail_request)
    try:
        with pytest.raises(ChapterProductionV2ProviderError) as raised:
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        assert provider.calls == 0
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.chapter_id == chapter.id)
            )
            assert run is not None and run.metadata_["provider_attempt"] is None
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_repeated_provider_failures_recover_with_fresh_generations(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _FailTwice()
    handoff = _handoff(engine, provider)
    try:
        for _ in range(2):
            with pytest.raises(ChapterProductionV2ProviderError):
                await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        result = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert provider.calls == 3
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            checkpoints = list(await session.scalars(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == result.workflow_run_id)
                .order_by(WorkflowCheckpoint.checkpoint_index)
            ))
            assert [item.checkpoint_index for item in checkpoints] == [0, 1, 2, 3, 4]
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_cancellation_releases_exact_attempt_and_preserves_state(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    handoff = _handoff(engine, _Cancelling())
    try:
        with pytest.raises(asyncio.CancelledError) as raised:
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert raised.value.args == ()
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.chapter_id == chapter.id)
            )
            assert run is not None and run.status == "DRAFTING"
            assert run.metadata_["provider_attempt"] is None
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_claim_commit_ack_loss_never_calls_provider(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _FailOnce()
    handoff = _handoff(engine, provider)
    original = handoff_module._commit

    async def commit_then_raise(session: AsyncSession) -> None:
        await original(session)
        raise ChapterProductionV2CommitIndeterminateError()

    monkeypatch.setattr(handoff_module, "_commit", commit_then_raise)
    try:
        with pytest.raises(ChapterProductionV2CommitIndeterminateError):
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert provider.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_failure_commit_ack_loss_is_durable_and_restartable(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _FailOnce()
    handoff = _handoff(engine, provider)
    original = handoff_module._commit
    commits = 0

    async def lose_failure_ack(session: AsyncSession) -> None:
        nonlocal commits
        commits += 1
        await original(session)
        if commits == 2:
            raise ChapterProductionV2CommitIndeterminateError()

    monkeypatch.setattr(handoff_module, "_commit", lose_failure_ack)
    try:
        with pytest.raises(ChapterProductionV2CommitIndeterminateError):
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert provider.calls == 1
        monkeypatch.setattr(handoff_module, "_commit", original)
        result = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert type(result) is InitialProviderResult
        assert provider.calls == 2
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_concurrent_execute_calls_provider_once(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _Blocking()
    handoff = _handoff(engine, provider)
    first = asyncio.create_task(
        handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
    )
    try:
        await provider.entered.wait()
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert provider.calls == 1
        provider.release.set()
        assert type(await first) is InitialProviderResult
    finally:
        provider.release.set()
        if not first.done():
            await first
        await engine.dispose()


@pytest.mark.anyio
async def test_late_generation_ack_cannot_clear_current_generation(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    handoff = _handoff(engine, DeterministicChapterWriterProvider())
    try:
        first = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        await handoff.acknowledge_no_write(first.generation)
        second = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        with pytest.raises(ChapterProductionV2ReconciliationError):
            await handoff.acknowledge_no_write(first.generation)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(WorkflowRun, second.workflow_run_id)
            assert run is not None
            assert run.metadata_["provider_attempt"]["attempt_id"] == str(
                second.generation.attempt.attempt_id
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_wrong_owner_fails_before_run_or_provider(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, _ = await _approved(async_session, tmp_path)
    outsider = User(username=f"outsider-{uuid4().hex}", display_name="Outsider")
    async_session.add(outsider)
    await async_session.commit()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _FailOnce()
    handoff = _handoff(engine, provider)
    try:
        with pytest.raises(ChapterProductionV2ValidationError):
            await handoff.execute(project.id, chapter.id, actor_user_id=outsider.id)
        assert provider.calls == 0
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkflowRun).where(
                    WorkflowRun.chapter_id == chapter.id
                )
            ) == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_failed_attempt_with_foreign_canonical_key_fails_before_provider(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = _FailOnce()
    handoff = _handoff(engine, provider)
    try:
        with pytest.raises(ChapterProductionV2ProviderError):
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.chapter_id == chapter.id)
            )
            assert run is not None
            attempt = dict(run.metadata_["provider_attempt"])
            attempt["key"] = "f" * 64
            run.metadata_ = {**run.metadata_, "provider_attempt": attempt}
            await session.commit()

        with pytest.raises(ChapterProductionV2ReconciliationError):
            await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        assert provider.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_non_null_run_next_node_fails_before_provider(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    project, chapter, owner = await _approved(async_session, tmp_path)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    provider = DeterministicChapterWriterProvider()
    handoff = _handoff(engine, provider)
    try:
        result = await handoff.execute(project.id, chapter.id, actor_user_id=owner.id)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            run = await session.get(WorkflowRun, result.workflow_run_id)
            assert run is not None
            run.next_node = "drafting"
            await session.commit()

        with pytest.raises(ChapterProductionV2ReconciliationError):
            await handoff.acknowledge_no_write(result.generation)
    finally:
        await engine.dispose()
