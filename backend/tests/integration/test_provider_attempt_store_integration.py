from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Chapter, Project, User, WorkflowCheckpoint, WorkflowRun, WorkflowType
from app.services.chapter_production_repository import ChapterProductionRepository
from app.services.chapter_production_v2_contracts import ChapterProductionV2ReconciliationError
from app.services.provider_attempt_contracts import (
    ProviderAttempt,
    ProviderAttemptStatus,
)


pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CONTRACT_VERSION = "chapter-production-v2"
INACTIVE_STATUSES = frozenset({"COMPLETED", "CANCELLED"})


def _module():
    return importlib.import_module("app.services.provider_attempt_store")


def _store(session: AsyncSession):
    return _module().ProviderAttemptStore(
        session,
        ChapterProductionRepository(
            session,
            contract_version=CONTRACT_VERSION,
            inactive_run_statuses=INACTIVE_STATUSES,
        ),
    )


async def _run_fixture(
    session: AsyncSession, workspace: Path, *, checkpoint_index: int = 3
) -> tuple[Project, Chapter, WorkflowRun]:
    owner = User(username=f"attempt-owner-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"attempt-project-{uuid4().hex}",
        title="Attempt Store",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1, status="OUTLINE_APPROVED")
    session.add(chapter)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="DRAFTING",
        current_node="DRAFTING",
        awaiting_user=False,
        metadata_={"contract_version": CONTRACT_VERSION, "provider_attempt": None},
    )
    session.add(run)
    await session.flush()
    session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=checkpoint_index,
            node_name="DRAFTING",
            state_json={"status": "DRAFTING"},
        )
    )
    await session.commit()
    return project, chapter, run


def _attempt(*, key: str, checkpoint_index: int, attempt_id: UUID | None = None):
    return ProviderAttempt.initial(
        attempt_id=attempt_id or uuid4(),
        operation_key=key,
        checkpoint_index=checkpoint_index,
    )


def _scope(project: Project, chapter: Chapter, run: WorkflowRun, attempt: ProviderAttempt):
    return _module().ProviderAttemptScope(
        project_id=UUID(str(project.id)),
        chapter_id=UUID(str(chapter.id)),
        workflow_run_id=UUID(str(run.id)),
        kind=attempt.kind,
        operation_key=attempt.operation_key,
        checkpoint_index=attempt.checkpoint_index,
        attempt_id=attempt.attempt_id,
    )


async def _fresh_attempt(session: AsyncSession, run_id: UUID) -> ProviderAttempt | None:
    run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    assert run is not None
    return ProviderAttempt.from_payload(run.metadata_.get("provider_attempt"))


async def test_claim_is_idempotent_across_commit_ack_and_rejects_competing_token(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    attempt = _attempt(key="a" * 64, checkpoint_index=3)
    scope = _scope(project, chapter, run, attempt)

    assert await _store(async_session).claim(scope, attempt) == attempt
    await async_session.commit()
    assert await _store(async_session).claim(scope, attempt) == attempt

    competing = _attempt(key=attempt.operation_key, checkpoint_index=3)
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await _store(async_session).claim(_scope(project, chapter, run, competing), competing)
    assert await _fresh_attempt(async_session, run.id) == attempt


async def test_fail_recover_and_late_a_results_preserve_exact_b_generation(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    first = _attempt(key="b" * 64, checkpoint_index=3)
    first_scope = _scope(project, chapter, run, first)
    await _store(async_session).claim(first_scope, first)
    failed = await _store(async_session).mark_failed(first_scope)
    assert failed == first.with_status(ProviderAttemptStatus.FAILED)

    second = _attempt(key=first.operation_key, checkpoint_index=3)
    async_session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=4,
            node_name="FAILED",
            state_json={"status": "FAILED"},
        )
    )
    await async_session.flush()
    assert await _store(async_session).recover_failed(first_scope) is True
    async_session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=5,
            node_name="DRAFTING",
            state_json={"status": "DRAFTING"},
        )
    )
    second = ProviderAttempt.initial(
        attempt_id=second.attempt_id,
        operation_key=second.operation_key,
        checkpoint_index=5,
    )
    second_scope = _scope(project, chapter, run, second)
    assert await _store(async_session).claim(second_scope, second) == second
    assert await _store(async_session).mark_failed(first_scope) is None
    assert await _store(async_session).release(first_scope) is False
    assert await _fresh_attempt(async_session, run.id) == second
    assert await _store(async_session).release(second_scope) is True
    assert await _fresh_attempt(async_session, run.id) is None


async def test_acknowledge_no_write_is_strict_and_clears_only_exact_claim(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    attempt = _attempt(key="c" * 64, checkpoint_index=3)
    await _store(async_session).claim(_scope(project, chapter, run, attempt), attempt)
    wrong = _attempt(key=attempt.operation_key, checkpoint_index=3)
    with pytest.raises(ChapterProductionV2ReconciliationError):
        await _store(async_session).acknowledge_no_write(
            _scope(project, chapter, run, wrong)
        )
    assert await _store(async_session).acknowledge_no_write(
        _scope(project, chapter, run, attempt)
    )
    assert await _fresh_attempt(async_session, run.id) is None


@pytest.mark.parametrize("corruption", ["missing-checkpoint", "malformed", "wrong-scope"])
async def test_durable_cardinality_scope_and_shape_corruption_fail_closed(
    async_session: AsyncSession, tmp_path: Path, corruption: str
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    attempt = _attempt(key="d" * 64, checkpoint_index=3)
    if corruption == "missing-checkpoint":
        checkpoint = await async_session.scalar(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.workflow_run_id == run.id)
        )
        assert checkpoint is not None
        await async_session.delete(checkpoint)
    elif corruption == "malformed":
        run.metadata_ = {
            "contract_version": CONTRACT_VERSION,
            "provider_attempt": {"attempt_id": "PRIVATE"},
        }
    else:
        foreign = Project(
            slug=f"attempt-foreign-{uuid4().hex}",
            title="Foreign",
            workspace_root=str(tmp_path / "foreign"),
        )
        async_session.add(foreign)
        await async_session.flush()
        run.project_id = foreign.id
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ReconciliationError) as error:
        await _store(async_session).claim(_scope(project, chapter, run, attempt), attempt)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert "PRIVATE" not in repr(error.value) + str(error.value)


async def test_cross_run_token_or_key_collision_is_not_hidden(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    attempt = _attempt(key="e" * 64, checkpoint_index=3)
    sibling = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="FAILED",
        current_node="FAILED",
        awaiting_user=False,
        metadata_={"contract_version": CONTRACT_VERSION, "provider_attempt": attempt.to_payload()},
    )
    async_session.add(sibling)
    await async_session.commit()

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await _store(async_session).claim(_scope(project, chapter, run, attempt), attempt)
    assert await _fresh_attempt(async_session, run.id) is None


async def test_authoritative_read_refreshes_stale_identity_before_release(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    first = _attempt(key="f" * 64, checkpoint_index=3)
    second = _attempt(key=first.operation_key, checkpoint_index=3)
    await _store(async_session).claim(_scope(project, chapter, run, first), first)
    await async_session.commit()
    cached = await async_session.get(WorkflowRun, run.id)
    assert cached is run
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as writer:
            await writer.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run.id)
                .values(metadata_={
                    "contract_version": CONTRACT_VERSION,
                    "provider_attempt": second.to_payload(),
                })
            )
            await writer.commit()
        assert await _store(async_session).release(_scope(project, chapter, run, first)) is False
        assert await _fresh_attempt(async_session, run.id) == second
    finally:
        await async_session.rollback()
        await engine.dispose()


async def test_concurrent_same_operation_claim_has_one_durable_winner(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, run = await _run_fixture(async_session, tmp_path)
    await async_session.commit()
    first = _attempt(key="1" * 64, checkpoint_index=3)
    second = _attempt(key=first.operation_key, checkpoint_index=3)
    first_scope = _scope(project, chapter, run, first)
    second_scope = _scope(project, chapter, run, second)

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def claim(scope, attempt):
        async with sessions() as session:
            try:
                await _store(session).claim(scope, attempt)
                await session.commit()
                return "claimed"
            except ChapterProductionV2ReconciliationError:
                await session.rollback()
                return "rejected"

    try:
        results = await asyncio.gather(
            claim(first_scope, first),
            claim(second_scope, second),
        )
        assert sorted(results) == ["claimed", "rejected"]
        stored = await _fresh_attempt(async_session, run.id)
        assert stored in {first, second}
    finally:
        await engine.dispose()


async def test_store_round_trips_all_three_exact_attempt_shapes(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    target = uuid4()
    attempts = (
        ProviderAttempt.initial(
            attempt_id=uuid4(), operation_key="2" * 64, checkpoint_index=3
        ),
        ProviderAttempt.feedback(
            attempt_id=uuid4(),
            operation_key="3" * 64,
            checkpoint_index=3,
            source_document_id=uuid4(),
            source_version_id=uuid4(),
            action_request_id=uuid4(),
            target_segment_ids=(target,),
            feedback_hash="4" * 64,
        ),
        ProviderAttempt.corrective_revision(
            attempt_id=uuid4(),
            operation_key="5" * 64,
            checkpoint_index=3,
            source_document_id=uuid4(),
            source_version_id=uuid4(),
            target_segment_ids=(target,),
            report_ids=(uuid4(),),
            report_input_hash="6" * 64,
        ),
    )
    for index, attempt in enumerate(attempts, start=1):
        project, chapter, run = await _run_fixture(
            async_session, tmp_path / str(index)
        )
        scope = _scope(project, chapter, run, attempt)
        assert await _store(async_session).claim(scope, attempt) == attempt
        await async_session.flush()
        assert await _fresh_attempt(async_session, run.id) == attempt
        assert await _store(async_session).release(scope) is True
