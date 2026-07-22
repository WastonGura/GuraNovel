from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import ConflictError, WorkflowStateError
from app.models import (
    ActionRequest,
    Chapter,
    Project,
    User,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.services.project_creation_service import ProjectCreationService
from app.services.project_service import ProjectService
from app.workflows.project_creation import ProjectCreationStatus
from app.workspace import ProjectWorkspace


async def create_project(async_session: AsyncSession, workspace_base: Path) -> Project:
    return await ProjectService(async_session, ProjectWorkspace(workspace_base)).create_project(
        slug=f"project-creation-{workspace_base.name}", title="Project creation test"
    )


async def workflow_counts(
    async_session: AsyncSession, workflow_run_id: object
) -> tuple[int, int, int]:
    return (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == workflow_run_id)
        ),
        await async_session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(ActionRequest.workflow_run_id == workflow_run_id)
        ),
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == workflow_run_id)
        ),
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_persists_initial_safe_checkpoint_for_real_project(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "workspaces")

    result = await ProjectCreationService(async_session).start(project.id)

    run = await async_session.get(WorkflowRun, result.workflow_run_id)
    assert run is not None
    assert run.project_id == project.id
    assert run.workflow_type == WorkflowType.PROJECT_CREATION.value
    assert run.status == ProjectCreationStatus.USER_IDEA.value
    assert run.current_node == "user_idea"
    assert run.awaiting_user is False
    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index)
        )
    )
    assert [
        (checkpoint.checkpoint_index, checkpoint.node_name, checkpoint.state_json)
        for checkpoint in checkpoints
    ] == [
        (
            0,
            "user_idea",
            {
                "version": 1,
                "status": "user_idea",
                "current_node": "user_idea",
                "awaiting_user": False,
                "action_request_id": None,
            },
        )
    ]
    event = await async_session.scalar(
        select(WorkflowEvent).where(WorkflowEvent.workflow_run_id == run.id)
    )
    assert event is not None
    assert event.event_type == "project_creation_started"
    assert event.node_name == "user_idea"
    assert event.message is None
    assert event.payload == {"status": "user_idea"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_rolls_back_when_initial_flush_fails(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "start-flush-failure")
    project_id = project.id
    original_flush = async_session.flush
    original_rollback = async_session.rollback
    rollback_calls = 0

    async def failing_flush(*_: object, **__: object) -> None:
        raise RuntimeError("flush failed")

    async def tracked_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        await original_rollback()

    monkeypatch.setattr(async_session, "flush", failing_flush)
    monkeypatch.setattr(async_session, "rollback", tracked_rollback)
    with pytest.raises(RuntimeError, match="flush failed"):
        await ProjectCreationService(async_session).start(project_id)
    monkeypatch.setattr(async_session, "flush", original_flush)

    assert rollback_calls == 1
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
        )
        == 0
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_request_concept_review_rolls_back_when_action_flush_fails(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "request-flush-failure")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    original_flush = async_session.flush
    original_rollback = async_session.rollback
    rollback_calls = 0

    async def failing_flush(*_: object, **__: object) -> None:
        raise RuntimeError("flush failed")

    async def tracked_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        await original_rollback()

    monkeypatch.setattr(async_session, "flush", failing_flush)
    monkeypatch.setattr(async_session, "rollback", tracked_rollback)
    with pytest.raises(RuntimeError, match="flush failed"):
        await service.request_concept_review(started.workflow_run_id)
    monkeypatch.setattr(async_session, "flush", original_flush)

    assert rollback_calls == 1
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(ActionRequest.workflow_run_id == started.workflow_run_id)
        )
        == 0
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_starts_create_only_one_active_project_creation_run(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project = await create_project(async_session, tmp_path / "concurrent-start")
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def start() -> object:
        async with session_factory() as session:
            return await ProjectCreationService(session).start(project.id)

    try:
        outcomes = await asyncio.gather(*(start() for _ in range(4)), return_exceptions=True)
        successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]

        assert len(successes) == 1
        assert len(failures) == 3
        assert all(isinstance(error, ConflictError) for error in failures)
        async with session_factory() as check_session:
            runs = list(
                await check_session.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.project_id == project.id,
                        WorkflowRun.workflow_type == WorkflowType.PROJECT_CREATION.value,
                    )
                )
            )
            persisted_project = await check_session.get(Project, project.id)
        assert len(runs) == 1
        assert persisted_project is not None
        assert persisted_project.current_workflow_id == runs[0].id
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_concept_review_uses_ordered_checkpoints_and_durable_user_decision(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "review")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)

    waiting = await service.request_concept_review(started.workflow_run_id)
    resumed = await service.resume_concept_review(
        started.workflow_run_id, waiting.action_request_id, "approved"
    )

    assert resumed.status is ProjectCreationStatus.CONCEPT_REVIEWED
    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
            .order_by(WorkflowCheckpoint.checkpoint_index)
        )
    )
    assert [checkpoint.checkpoint_index for checkpoint in checkpoints] == [0, 1, 2]
    assert [checkpoint.state_json["status"] for checkpoint in checkpoints] == [
        "user_idea",
        "concept_options",
        "concept_reviewed",
    ]
    loaded = await service.load_latest_state(started.workflow_run_id)
    assert loaded == resumed
    action = await async_session.get(ActionRequest, waiting.action_request_id)
    assert action is not None
    assert action.status == "approved"
    assert action.user_decision == "approved"


@pytest.mark.integration
@pytest.mark.anyio
async def test_transition_rejects_non_null_next_node_without_durable_mutation(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "next-node")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(next_node="unexpected")
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.request_concept_review(started.workflow_run_id)

    assert await workflow_counts(async_session, started.workflow_run_id) == (1, 0, 1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_transition_rejects_nonterminal_completion_timestamp_without_durable_mutation(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "nonterminal-completed")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(completed_at=datetime.now(UTC))
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.request_concept_review(started.workflow_run_id)

    assert await workflow_counts(async_session, started.workflow_run_id) == (1, 0, 1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_transition_rejects_terminal_state_without_completion_timestamp(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "terminal-uncompleted")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)
    await service.resume_concept_review(
        started.workflow_run_id, waiting.action_request_id, "rejected"
    )
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(completed_at=None)
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.request_concept_review(started.workflow_run_id)

    assert await workflow_counts(async_session, started.workflow_run_id) == (3, 1, 3)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["missing", "wrong_type", "wrong_project", "duplicate"])
async def test_resume_rejects_invalid_waiting_action_without_durable_mutation(
    async_session: AsyncSession, tmp_path: Path, corruption: str
) -> None:
    project = await create_project(async_session, tmp_path / f"waiting-action-{corruption}")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)
    if corruption == "missing":
        await async_session.execute(
            ActionRequest.__table__.delete().where(ActionRequest.id == waiting.action_request_id)
        )
    elif corruption == "wrong_type":
        await async_session.execute(
            ActionRequest.__table__.update()
            .where(ActionRequest.id == waiting.action_request_id)
            .values(request_type="unexpected")
        )
    elif corruption == "wrong_project":
        await async_session.execute(
            ActionRequest.__table__.update()
            .where(ActionRequest.id == waiting.action_request_id)
            .values(project_id=None)
        )
    else:
        async_session.add(
            ActionRequest(
                workflow_run_id=started.workflow_run_id,
                project_id=project.id,
                request_type="project_creation_concept_review",
                status="pending",
                prompt="",
                options=[],
            )
        )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.resume_concept_review(
            started.workflow_run_id, waiting.action_request_id, "approved"
        )

    expected_actions = 0 if corruption == "missing" else 2 if corruption == "duplicate" else 1
    assert await workflow_counts(async_session, started.workflow_run_id) == (2, expected_actions, 2)


@pytest.mark.integration
@pytest.mark.anyio
async def test_complete_rejects_pending_concept_action_for_nonwaiting_state(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "nonwaiting-pending-action")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)
    await service.resume_concept_review(
        started.workflow_run_id, waiting.action_request_id, "approved"
    )
    async_session.add(
        ActionRequest(
            workflow_run_id=started.workflow_run_id,
            project_id=project.id,
            request_type="project_creation_concept_review",
            status="pending",
            prompt="",
            options=[],
        )
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.complete(started.workflow_run_id)

    assert await workflow_counts(async_session, started.workflow_run_id) == (3, 2, 3)


@pytest.mark.integration
@pytest.mark.anyio
async def test_request_concept_review_rejects_mismatched_terminal_run_projection(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "projection-request")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(status="completed")
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.request_concept_review(started.workflow_run_id)

    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(ActionRequest.workflow_run_id == started.workflow_run_id)
        )
        == 0
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_resume_concept_review_rejects_mismatched_run_projection_before_resolving_action(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "projection-resume")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(current_node="user_idea")
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.resume_concept_review(
            started.workflow_run_id, waiting.action_request_id, "approved"
        )

    action = await async_session.get(ActionRequest, waiting.action_request_id)
    assert action is not None
    assert action.status == "pending"
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
        )
        == 2
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
        )
        == 2
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_complete_rejects_mismatched_terminal_run_projection_before_checkpointing(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "projection-complete")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)
    await service.resume_concept_review(
        started.workflow_run_id, waiting.action_request_id, "approved"
    )
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(status="rejected")
    )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="state is inconsistent"):
        await service.complete(started.workflow_run_id)

    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
        )
        == 3
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
        )
        == 3
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_waiting_gate_rejects_regular_transition_and_terminal_run_rejects_bypass(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "gate")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)

    with pytest.raises(WorkflowStateError, match="awaiting a user decision"):
        await service.complete(started.workflow_run_id)

    await service.resume_concept_review(
        started.workflow_run_id, waiting.action_request_id, "rejected"
    )
    before = await workflow_counts(async_session, started.workflow_run_id)
    with pytest.raises(WorkflowStateError):
        await service.request_concept_review(started.workflow_run_id)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    assert run.status == ProjectCreationStatus.REJECTED.value
    assert run.completed_at is not None
    assert await workflow_counts(async_session, started.workflow_run_id) == before


@pytest.mark.integration
@pytest.mark.anyio
async def test_invalid_transition_corrupt_checkpoint_and_events_fail_closed_and_stay_safe(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "safe")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)

    with pytest.raises(WorkflowStateError, match="not allowed"):
        await service.complete(started.workflow_run_id)

    await async_session.execute(
        WorkflowCheckpoint.__table__.update()
        .where(WorkflowCheckpoint.workflow_run_id == started.workflow_run_id)
        .values(state_json={"seed": "secret seed", "status": "unknown"})
    )
    await async_session.commit()
    with pytest.raises(WorkflowStateError, match="checkpoint is invalid"):
        await service.load_latest_state(started.workflow_run_id)

    events = list(
        await async_session.scalars(
            select(WorkflowEvent).where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
        )
    )
    serialized = " ".join(f"{event.message} {event.payload}" for event in events)
    assert "secret seed" not in serialized
    assert all(event.message is None for event in events)
    assert all(set(event.payload) <= {"status", "action_request_id"} for event in events)


@pytest.mark.integration
@pytest.mark.anyio
async def test_legacy_waiting_action_remains_narrowly_readable(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project = await create_project(async_session, tmp_path / "legacy-read")
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)

    read = await service.get_project_creation_run(project.id, started.workflow_run_id)
    assert read.pending_action is not None
    assert read.pending_action.id == waiting.action_request_id
    assert read.pending_action.type == "project_creation_concept_review"
    assert read.pending_action.allowed_decisions == ("approved", "rejected")
    assert read.pending_action.review_severity is None


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "field",
    [
        "chapter_id",
        "user_decision",
        "user_feedback",
        "resolved_by_id",
        "resolved_at",
        "expires_at",
    ],
)
async def test_legacy_waiting_action_corruption_fails_closed_for_read_and_resume(
    async_session: AsyncSession, tmp_path: Path, field: str
) -> None:
    project = await create_project(async_session, tmp_path / field)
    service = ProjectCreationService(async_session)
    started = await service.start(project.id)
    waiting = await service.request_concept_review(started.workflow_run_id)
    action = await async_session.get(ActionRequest, waiting.action_request_id)
    assert action is not None
    user = User(username=f"legacy-corruption-{field}")
    chapter = Chapter(project_id=project.id, chapter_number=1, title="Foreign-looking scope")
    async_session.add_all((user, chapter))
    await async_session.flush()
    values = {
        "chapter_id": chapter.id,
        "user_decision": "approved",
        "user_feedback": "private feedback",
        "resolved_by_id": user.id,
        "resolved_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC),
    }
    setattr(action, field, values[field])
    await async_session.commit()

    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(project.id, started.workflow_run_id)
    with pytest.raises(WorkflowStateError):
        await service.resume_concept_review(
            started.workflow_run_id, waiting.action_request_id, "approved"
        )
