from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import NotFoundError, WorkflowStateError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Project,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.services.project_maintenance_foundation_service import (
    ProjectMaintenanceCommitIndeterminateError,
    ProjectMaintenanceFoundationService,
)
from app.workflows.project_maintenance import (
    AffectedItem,
    AffectedItemType,
    ImpactLevel,
    MaintenanceDecision,
    MaintenanceReviewOutcome,
    ProjectMaintenanceState,
    ProjectMaintenanceStatus,
)


def revision_state() -> ProjectMaintenanceState:
    return ProjectMaintenanceState(
        status=ProjectMaintenanceStatus.REVISION_PLAN,
        current_node="revision_plan",
        awaiting_user=False,
        lore_impact_report_id=str(uuid4()),
        chief_impact_report_id=str(uuid4()),
        revision_plan_document_id=str(uuid4()),
        revision_plan_version_id=str(uuid4()),
    )


def consistency_state() -> ProjectMaintenanceState:
    return ProjectMaintenanceState(
        status=ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        current_node="consistency_review",
        awaiting_user=False,
        lore_impact_report_id=str(uuid4()),
        chief_impact_report_id=str(uuid4()),
        revision_plan_document_id=str(uuid4()),
        revision_plan_version_id=str(uuid4()),
        applied_document_version_ids=(str(uuid4()),),
    )


async def seed_state(
    session: AsyncSession, state: ProjectMaintenanceState
) -> tuple[UUID, UUID]:
    project = Project(
        slug=f"maintenance-foundation-{uuid4()}",
        title="Maintenance foundation test",
        workspace_root="/tmp/guranovel-maintenance-foundation",
    )
    session.add(project)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id,
        chapter_id=None,
        workflow_type=WorkflowType.PROJECT_MAINTENANCE.value,
        status=state.status.value,
        current_node=state.current_node,
        next_node=None,
        awaiting_user=state.awaiting_user,
    )
    session.add(run)
    await session.flush()
    project.current_workflow_id = run.id
    session.add(
        WorkflowCheckpoint(
            workflow_run_id=run.id,
            checkpoint_index=0,
            node_name=state.current_node,
            state_json=state.to_checkpoint(),
        )
    )
    await session.commit()
    return project.id, run.id


def _json(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True))


def _time(value: object) -> str | None:
    return value.isoformat() if value is not None else None  # type: ignore[union-attr]


async def durable_snapshot(session: AsyncSession, run_id: UUID) -> dict[str, object]:
    run = await session.get(WorkflowRun, run_id)
    assert run is not None
    actions = list(
        await session.scalars(
            select(ActionRequest)
            .where(ActionRequest.workflow_run_id == run_id)
            .order_by(ActionRequest.created_at, ActionRequest.id)
        )
    )
    checkpoints = list(
        await session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run_id)
            .order_by(WorkflowCheckpoint.checkpoint_index, WorkflowCheckpoint.id)
        )
    )
    events = list(
        await session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run_id)
            .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
        )
    )
    return {
        "run": (
            str(run.id),
            str(run.project_id),
            run.chapter_id,
            run.workflow_type,
            run.status,
            run.current_node,
            run.next_node,
            run.awaiting_user,
            _time(run.completed_at),
        ),
        "actions": [
            (
                str(action.id),
                str(action.workflow_run_id),
                str(action.project_id),
                action.chapter_id,
                action.request_type,
                action.status,
                action.prompt,
                tuple(action.options),
                action.default_option,
                action.user_decision,
                action.user_feedback,
                action.resolved_by_id,
                _time(action.resolved_at),
                _time(action.expires_at),
                _json(action.metadata_),
            )
            for action in actions
        ],
        "checkpoints": [
            (checkpoint.checkpoint_index, checkpoint.node_name, _json(checkpoint.state_json))
            for checkpoint in checkpoints
        ],
        "events": [
            (event.event_type, event.node_name, _json(event.payload)) for event in events
        ],
    }


async def assert_latest_transition(
    session: AsyncSession,
    project_id: UUID,
    run_id: UUID,
    action_id: UUID,
    expected_state: ProjectMaintenanceState,
    expected_action_status: ActionRequestStatus,
    expected_decision: MaintenanceDecision,
    expected_request_type: str,
    expected_options: list[str],
    expected_metadata: dict[str, str],
) -> None:
    run = await session.get(WorkflowRun, run_id)
    action = await session.get(ActionRequest, action_id)
    checkpoint = await session.scalar(
        select(WorkflowCheckpoint)
        .where(WorkflowCheckpoint.workflow_run_id == run_id)
        .order_by(WorkflowCheckpoint.checkpoint_index.desc())
        .limit(1)
    )
    event = await session.scalar(
        select(WorkflowEvent)
        .where(WorkflowEvent.workflow_run_id == run_id)
        .order_by(WorkflowEvent.created_at.desc(), WorkflowEvent.id.desc())
        .limit(1)
    )
    assert run is not None and action is not None and checkpoint is not None and event is not None
    assert (
        run.project_id,
        run.chapter_id,
        run.workflow_type,
        run.status,
        run.current_node,
        run.next_node,
        run.awaiting_user,
        run.completed_at is not None,
    ) == (
        project_id,
        None,
        WorkflowType.PROJECT_MAINTENANCE.value,
        expected_state.status.value,
        expected_state.current_node,
        None,
        expected_state.awaiting_user,
        expected_state.is_terminal,
    )
    assert (
        action.workflow_run_id,
        action.project_id,
        action.chapter_id,
        action.request_type,
        action.status,
        action.prompt,
        action.options,
        action.default_option,
        action.user_decision,
        action.user_feedback,
        action.resolved_by_id,
        action.resolved_at is not None,
        action.expires_at,
    ) == (
        run_id,
        project_id,
        None,
        expected_request_type,
        expected_action_status.value,
        "",
        expected_options,
        None,
        expected_decision.value,
        None,
        None,
        True,
        None,
    )
    assert action.metadata_ == expected_metadata
    assert checkpoint.checkpoint_index == 2
    assert checkpoint.node_name == expected_state.current_node
    assert ProjectMaintenanceState.from_checkpoint(checkpoint.state_json) == expected_state
    assert (
        event.event_type,
        event.node_name,
        event.payload,
    ) == (
        "project_maintenance_action_resolved",
        expected_state.current_node,
        expected_state.to_public_event_payload(action_request_id=str(action_id)),
    )


async def assert_exact_waiting_transition(
    session: AsyncSession,
    project_id: UUID,
    run_id: UUID,
    initial_state: ProjectMaintenanceState,
    review_outcome: MaintenanceReviewOutcome,
) -> tuple[UUID, ProjectMaintenanceState]:
    run = await session.get(WorkflowRun, run_id)
    actions = list(
        await session.scalars(
            select(ActionRequest)
            .where(ActionRequest.workflow_run_id == run_id)
            .order_by(ActionRequest.created_at, ActionRequest.id)
        )
    )
    checkpoints = list(
        await session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run_id)
            .order_by(WorkflowCheckpoint.checkpoint_index, WorkflowCheckpoint.id)
        )
    )
    events = list(
        await session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run_id)
            .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
        )
    )
    assert run is not None
    assert len(actions) == 1
    assert len(checkpoints) == 2
    assert len(events) == 1

    action = actions[0]
    expected_state = initial_state.request_revision_confirmation(
        action_request_id=str(action.id), review_outcome=review_outcome
    )
    assert (
        run.id,
        run.project_id,
        run.chapter_id,
        run.workflow_type,
        run.status,
        run.current_node,
        run.next_node,
        run.awaiting_user,
        run.completed_at,
    ) == (
        run_id,
        project_id,
        None,
        WorkflowType.PROJECT_MAINTENANCE.value,
        expected_state.status.value,
        expected_state.current_node,
        None,
        True,
        None,
    )
    assert (
        action.workflow_run_id,
        action.project_id,
        action.chapter_id,
        action.request_type,
        action.status,
        action.prompt,
        action.options,
        action.default_option,
        action.user_decision,
        action.user_feedback,
        action.resolved_by_id,
        action.resolved_at,
        action.expires_at,
        action.metadata_,
    ) == (
        run_id,
        project_id,
        None,
        "project_maintenance_revision_confirmation",
        ActionRequestStatus.PENDING.value,
        "",
        ["approve", "revise", "cancel"],
        None,
        None,
        None,
        None,
        None,
        None,
        {
            "confirmation_kind": "revision_confirmation",
            "review_outcome": review_outcome.value,
        },
    )
    assert (
        checkpoints[0].checkpoint_index,
        checkpoints[0].node_name,
        checkpoints[0].state_json,
    ) == (0, initial_state.current_node, initial_state.to_checkpoint())
    assert (
        checkpoints[1].checkpoint_index,
        checkpoints[1].node_name,
        checkpoints[1].state_json,
    ) == (1, expected_state.current_node, expected_state.to_checkpoint())
    assert ProjectMaintenanceState.from_checkpoint(checkpoints[1].state_json) == expected_state
    expected_payload = expected_state.to_public_event_payload()
    assert set(events[0].payload) == {
        "status",
        "current_node",
        "awaiting_user",
        "action_request_id",
        "confirmation_kind",
    }
    assert (events[0].event_type, events[0].node_name, events[0].payload) == (
        "project_maintenance_confirmation_requested",
        expected_state.current_node,
        expected_payload,
    )
    return action.id, expected_state


@pytest.mark.integration
@pytest.mark.anyio
async def test_checkpoint_and_confirmation_event_round_trip_without_content(
    async_session: AsyncSession,
    integration_database_url: str,
) -> None:
    content_sentinel = "PRIVATE_CHANGE_REASON_MUST_NOT_ENTER_CHECKPOINT"
    affected_item = AffectedItem(
        type=AffectedItemType.WORLD,
        ref="world/private_rule",
        impact_level=ImpactLevel.HIGH,
        reason=content_sentinel,
    )
    state = revision_state()
    state = ProjectMaintenanceState(
        **{
            **state.__dict__,
            "affected_item_ids": (affected_item.to_checkpoint_reference(str(uuid4())),),
        }
    )
    project_id, run_id = await seed_state(async_session, state)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id,
                run_id,
                review_outcome=MaintenanceReviewOutcome.PASSED,
            )
        async with sessions() as fresh_session:
            checkpoint = await fresh_session.scalar(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == run_id)
                .order_by(WorkflowCheckpoint.checkpoint_index.desc())
                .limit(1)
            )
            event = await fresh_session.scalar(
                select(WorkflowEvent).where(WorkflowEvent.workflow_run_id == run_id)
            )
            assert checkpoint is not None and event is not None
            assert ProjectMaintenanceState.from_checkpoint(checkpoint.state_json) == waiting.state
            assert (event.event_type, event.node_name, event.payload) == (
                "project_maintenance_confirmation_requested",
                "user_confirm_revision",
                waiting.state.to_public_event_payload(),
            )
            serialized = json.dumps(
                {"checkpoint": checkpoint.state_json, "event": event.payload}, sort_keys=True
            )
            assert content_sentinel not in serialized
            assert affected_item.ref not in serialized
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "decision", "expected_status", "expected_action_status"),
    [
        (
            MaintenanceReviewOutcome.PASSED,
            MaintenanceDecision.APPROVE,
            ProjectMaintenanceStatus.APPLY_CHANGE,
            ActionRequestStatus.APPROVED,
        ),
        (
            MaintenanceReviewOutcome.WARNING,
            MaintenanceDecision.APPROVE,
            ProjectMaintenanceStatus.APPLY_CHANGE,
            ActionRequestStatus.APPROVED,
        ),
        (
            MaintenanceReviewOutcome.PASSED,
            MaintenanceDecision.REVISE,
            ProjectMaintenanceStatus.REVISION_PLAN,
            ActionRequestStatus.REVISED,
        ),
        (
            MaintenanceReviewOutcome.PASSED,
            MaintenanceDecision.CANCEL,
            ProjectMaintenanceStatus.CANCELLED,
            ActionRequestStatus.CANCELLED,
        ),
        (
            MaintenanceReviewOutcome.WARNING,
            MaintenanceDecision.REVISE,
            ProjectMaintenanceStatus.REVISION_PLAN,
            ActionRequestStatus.REVISED,
        ),
        (
            MaintenanceReviewOutcome.WARNING,
            MaintenanceDecision.CANCEL,
            ProjectMaintenanceStatus.CANCELLED,
            ActionRequestStatus.CANCELLED,
        ),
        (
            MaintenanceReviewOutcome.BLOCKING,
            MaintenanceDecision.REVISE,
            ProjectMaintenanceStatus.REVISION_PLAN,
            ActionRequestStatus.REVISED,
        ),
        (
            MaintenanceReviewOutcome.BLOCKING,
            MaintenanceDecision.CANCEL,
            ProjectMaintenanceStatus.CANCELLED,
            ActionRequestStatus.CANCELLED,
        ),
    ],
)
async def test_revision_confirmation_decision_matrix_persists_exact_transition(
    async_session: AsyncSession,
    integration_database_url: str,
    outcome: MaintenanceReviewOutcome,
    decision: MaintenanceDecision,
    expected_status: ProjectMaintenanceStatus,
    expected_action_status: ActionRequestStatus,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(project_id, run_id, review_outcome=outcome)
        async with sessions() as resolve_session:
            resolved = await ProjectMaintenanceFoundationService(resolve_session).resolve_action(
                project_id, run_id, waiting.action_request_id, decision=decision
            )
            assert resolved.status is expected_status
        async with sessions() as fresh_session:
            await assert_latest_transition(
                fresh_session,
                project_id,
                run_id,
                waiting.action_request_id,
                resolved,
                expected_action_status,
                decision,
                "project_maintenance_revision_confirmation",
                (
                    ["revise", "cancel"]
                    if outcome is MaintenanceReviewOutcome.BLOCKING
                    else ["approve", "revise", "cancel"]
                ),
                {
                    "confirmation_kind": "revision_confirmation",
                    "review_outcome": outcome.value,
                },
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_action_status"),
    [
        (
            MaintenanceDecision.ACCEPT_WARNING,
            ProjectMaintenanceStatus.PROJECT_UPDATED,
            ActionRequestStatus.FORCE_APPROVED,
        ),
        (
            MaintenanceDecision.REVISE,
            ProjectMaintenanceStatus.REVISION_PLAN,
            ActionRequestStatus.REVISED,
        ),
    ],
)
async def test_consistency_warning_decision_matrix_persists_exact_transition(
    async_session: AsyncSession,
    integration_database_url: str,
    decision: MaintenanceDecision,
    expected_status: ProjectMaintenanceStatus,
    expected_action_status: ActionRequestStatus,
) -> None:
    project_id, run_id = await seed_state(async_session, consistency_state())
    consistency_report_id = str(uuid4())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id,
                run_id,
                review_outcome=MaintenanceReviewOutcome.WARNING,
                consistency_report_id=consistency_report_id,
            )
        async with sessions() as resolve_session:
            resolved = await ProjectMaintenanceFoundationService(resolve_session).resolve_action(
                project_id, run_id, waiting.action_request_id, decision=decision
            )
            assert resolved.status is expected_status
            assert resolved.consistency_report_id == consistency_report_id
            if decision is MaintenanceDecision.REVISE:
                assert resolved.revision_plan_document_id is None
                assert resolved.revision_plan_version_id is None
        async with sessions() as fresh_session:
            await assert_latest_transition(
                fresh_session,
                project_id,
                run_id,
                waiting.action_request_id,
                resolved,
                expected_action_status,
                decision,
                "project_maintenance_consistency_warning",
                ["accept_warning", "revise"],
                {
                    "confirmation_kind": "consistency_warning",
                    "review_outcome": "warning",
                },
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_replay_and_real_foreign_action_leave_exact_durable_snapshots_unchanged(
    async_session: AsyncSession,
    integration_database_url: str,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    foreign_project_id, foreign_run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id, run_id, review_outcome=MaintenanceReviewOutcome.PASSED
            )
        async with sessions() as foreign_request_session:
            foreign = await ProjectMaintenanceFoundationService(
                foreign_request_session
            ).create_confirmation(
                foreign_project_id,
                foreign_run_id,
                review_outcome=MaintenanceReviewOutcome.PASSED,
            )
        async with sessions() as resolve_session:
            await ProjectMaintenanceFoundationService(resolve_session).resolve_action(
                project_id,
                run_id,
                waiting.action_request_id,
                decision=MaintenanceDecision.APPROVE,
            )
        async with sessions() as replay_session:
            before = await durable_snapshot(replay_session, run_id)
            with pytest.raises(WorkflowStateError):
                await ProjectMaintenanceFoundationService(replay_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            assert await durable_snapshot(replay_session, run_id) == before
        async with sessions() as foreign_session:
            own_before = await durable_snapshot(foreign_session, run_id)
            foreign_before = await durable_snapshot(foreign_session, foreign_run_id)
            with pytest.raises(NotFoundError):
                await ProjectMaintenanceFoundationService(foreign_session).resolve_action(
                    project_id,
                    run_id,
                    foreign.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            assert await durable_snapshot(foreign_session, run_id) == own_before
            assert await durable_snapshot(foreign_session, foreign_run_id) == foreign_before
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["request_type", "options", "metadata", "checkpoint"])
async def test_tampered_bindings_fail_with_exact_snapshot_unchanged(
    async_session: AsyncSession,
    integration_database_url: str,
    corruption: str,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id, run_id, review_outcome=MaintenanceReviewOutcome.WARNING
            )
        async with sessions() as corrupt_session:
            action = await corrupt_session.get(ActionRequest, waiting.action_request_id)
            checkpoint = await corrupt_session.scalar(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == run_id)
                .order_by(WorkflowCheckpoint.checkpoint_index.desc())
                .limit(1)
            )
            assert action is not None and checkpoint is not None
            if corruption == "request_type":
                action.request_type = "foreign_action_type"
            elif corruption == "options":
                action.options = [MaintenanceDecision.CANCEL.value]
            elif corruption == "metadata":
                action.metadata_ = {
                    "confirmation_kind": "revision_confirmation",
                    "review_outcome": "blocking",
                }
            else:
                checkpoint.state_json = {
                    **checkpoint.state_json,
                    "action_request_id": str(uuid4()),
                }
            await corrupt_session.commit()
        async with sessions() as resolve_session:
            before = await durable_snapshot(resolve_session, run_id)
            with pytest.raises(WorkflowStateError):
                await ProjectMaintenanceFoundationService(resolve_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            assert await durable_snapshot(resolve_session, run_id) == before
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_blocking_gate_rejects_approve_with_exact_snapshot_unchanged(
    async_session: AsyncSession,
    integration_database_url: str,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id, run_id, review_outcome=MaintenanceReviewOutcome.BLOCKING
            )
        async with sessions() as resolve_session:
            action = await resolve_session.get(ActionRequest, waiting.action_request_id)
            assert action is not None and action.options == ["revise", "cancel"]
            before = await durable_snapshot(resolve_session, run_id)
            with pytest.raises(WorkflowStateError):
                await ProjectMaintenanceFoundationService(resolve_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            assert await durable_snapshot(resolve_session, run_id) == before
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_corrective_plan_cancel_and_tampered_cancel_leave_snapshot_unchanged(
    async_session: AsyncSession,
    integration_database_url: str,
) -> None:
    project_id, run_id = await seed_state(async_session, consistency_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as consistency_session:
            consistency_gate = await ProjectMaintenanceFoundationService(
                consistency_session
            ).create_confirmation(
                project_id,
                run_id,
                review_outcome=MaintenanceReviewOutcome.WARNING,
                consistency_report_id=str(uuid4()),
            )
        async with sessions() as revise_session:
            corrective = await ProjectMaintenanceFoundationService(revise_session).resolve_action(
                project_id,
                run_id,
                consistency_gate.action_request_id,
                decision=MaintenanceDecision.REVISE,
            )
        planned = corrective.record_revision_plan(
            revision_plan_document_id=str(uuid4()),
            revision_plan_version_id=str(uuid4()),
        )
        async with sessions() as plan_session:
            run = await plan_session.get(WorkflowRun, run_id)
            assert run is not None
            run.status = planned.status.value
            run.current_node = planned.current_node
            run.awaiting_user = False
            plan_session.add(
                WorkflowCheckpoint(
                    workflow_run_id=run_id,
                    checkpoint_index=3,
                    node_name=planned.current_node,
                    state_json=planned.to_checkpoint(),
                )
            )
            await plan_session.commit()
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id,
                run_id,
                review_outcome=MaintenanceReviewOutcome.BLOCKING,
            )
        async with sessions() as reject_session:
            action = await reject_session.get(ActionRequest, waiting.action_request_id)
            assert action is not None and action.options == ["revise"]
            before = await durable_snapshot(reject_session, run_id)
            with pytest.raises(WorkflowStateError):
                await ProjectMaintenanceFoundationService(reject_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.CANCEL,
                )
            assert await durable_snapshot(reject_session, run_id) == before
        async with sessions() as tamper_session:
            action = await tamper_session.get(ActionRequest, waiting.action_request_id)
            assert action is not None
            action.options = ["revise", "cancel"]
            await tamper_session.commit()
        async with sessions() as tampered_reject_session:
            before = await durable_snapshot(tampered_reject_session, run_id)
            with pytest.raises(WorkflowStateError):
                await ProjectMaintenanceFoundationService(tampered_reject_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.CANCEL,
                )
            assert await durable_snapshot(tampered_reject_session, run_id) == before
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "decision", [MaintenanceDecision.APPROVE, MaintenanceDecision.CANCEL]
)
async def test_consistency_warning_invalid_decisions_leave_exact_snapshot_unchanged(
    async_session: AsyncSession,
    integration_database_url: str,
    decision: MaintenanceDecision,
) -> None:
    project_id, run_id = await seed_state(async_session, consistency_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id,
                run_id,
                review_outcome=MaintenanceReviewOutcome.WARNING,
                consistency_report_id=str(uuid4()),
            )
        async with sessions() as resolve_session:
            before = await durable_snapshot(resolve_session, run_id)
            with pytest.raises(WorkflowStateError):
                await ProjectMaintenanceFoundationService(resolve_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=decision,
                )
            assert await durable_snapshot(resolve_session, run_id) == before
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_session_create_race_persists_exactly_one_waiting_transition(
    async_session: AsyncSession,
    integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state = revision_state()
    project_id, run_id = await seed_state(async_session, initial_state)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            sessions() as winner_session,
            sessions() as loser_session,
            sessions() as observer_session,
        ):
            winner_at_commit = asyncio.Event()
            allow_winner_commit = asyncio.Event()
            original_commit = winner_session.commit

            async def gated_commit() -> None:
                winner_at_commit.set()
                await allow_winner_commit.wait()
                await original_commit()

            monkeypatch.setattr(winner_session, "commit", gated_commit)
            winner_task = asyncio.create_task(
                ProjectMaintenanceFoundationService(winner_session).create_confirmation(
                    project_id,
                    run_id,
                    review_outcome=MaintenanceReviewOutcome.PASSED,
                )
            )
            await asyncio.wait_for(winner_at_commit.wait(), timeout=5)
            loser_pid = await loser_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(loser_pid, int)
            loser_task = asyncio.create_task(
                ProjectMaintenanceFoundationService(loser_session).create_confirmation(
                    project_id,
                    run_id,
                    review_outcome=MaintenanceReviewOutcome.PASSED,
                )
            )
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                wait_event_type = await observer_session.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": loser_pid},
                )
                if wait_event_type == "Lock":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail("Losing creator did not reach the PostgreSQL lock barrier.")
                await asyncio.sleep(0.01)
            allow_winner_commit.set()
            waiting = await asyncio.wait_for(winner_task, timeout=5)
            with pytest.raises(WorkflowStateError):
                await asyncio.wait_for(loser_task, timeout=5)
        async with sessions() as fresh_session:
            action_id, expected_state = await assert_exact_waiting_transition(
                fresh_session,
                project_id,
                run_id,
                initial_state,
                MaintenanceReviewOutcome.PASSED,
            )
            assert (waiting.action_request_id, waiting.state) == (action_id, expected_state)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_session_resolve_race_persists_exactly_one_transition(
    async_session: AsyncSession,
    integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id, run_id, review_outcome=MaintenanceReviewOutcome.PASSED
            )
        async with (
            sessions() as winner_session,
            sessions() as loser_session,
            sessions() as observer_session,
        ):
            winner_at_commit = asyncio.Event()
            allow_winner_commit = asyncio.Event()
            original_commit = winner_session.commit

            async def gated_commit() -> None:
                winner_at_commit.set()
                await allow_winner_commit.wait()
                await original_commit()

            monkeypatch.setattr(winner_session, "commit", gated_commit)
            winner_task = asyncio.create_task(
                ProjectMaintenanceFoundationService(winner_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            )
            await asyncio.wait_for(winner_at_commit.wait(), timeout=5)
            loser_pid = await loser_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(loser_pid, int)
            loser_task = asyncio.create_task(
                ProjectMaintenanceFoundationService(loser_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            )
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                wait_event_type = await observer_session.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": loser_pid},
                )
                if wait_event_type == "Lock":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail("Losing resolver did not reach the PostgreSQL lock barrier.")
                await asyncio.sleep(0.01)
            allow_winner_commit.set()
            winner_state = await asyncio.wait_for(winner_task, timeout=5)
            with pytest.raises(WorkflowStateError):
                await asyncio.wait_for(loser_task, timeout=5)
        async with sessions() as fresh_session:
            await assert_latest_transition(
                fresh_session,
                project_id,
                run_id,
                waiting.action_request_id,
                winner_state,
                ActionRequestStatus.APPROVED,
                MaintenanceDecision.APPROVE,
                "project_maintenance_revision_confirmation",
                ["approve", "revise", "cancel"],
                {
                    "confirmation_kind": "revision_confirmation",
                    "review_outcome": "passed",
                },
            )
            snapshot = await durable_snapshot(fresh_session, run_id)
            assert len(snapshot["actions"]) == 1  # type: ignore[arg-type]
            assert len(snapshot["checkpoints"]) == 3  # type: ignore[arg-type]
            assert len(snapshot["events"]) == 2  # type: ignore[arg-type]
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_load_latest_state_serializes_with_writer_and_releases_read_locks(
    async_session: AsyncSession,
    integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            sessions() as load_session,
            sessions() as write_session,
            sessions() as observer_session,
        ):
            reader_pid = await load_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(reader_pid, int)
            writer_at_commit = asyncio.Event()
            allow_writer_commit = asyncio.Event()
            original_commit = write_session.commit

            async def gated_commit() -> None:
                writer_at_commit.set()
                await allow_writer_commit.wait()
                await original_commit()

            monkeypatch.setattr(write_session, "commit", gated_commit)
            create_task = asyncio.create_task(
                ProjectMaintenanceFoundationService(write_session).create_confirmation(
                    project_id,
                    run_id,
                    review_outcome=MaintenanceReviewOutcome.PASSED,
                )
            )
            await asyncio.wait_for(writer_at_commit.wait(), timeout=5)
            load_task = asyncio.create_task(
                ProjectMaintenanceFoundationService(load_session).load_latest_state(
                    project_id, run_id
                )
            )
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                wait_event_type = await observer_session.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": reader_pid},
                )
                if wait_event_type == "Lock":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail("Reader did not reach the PostgreSQL lock barrier.")
                await asyncio.sleep(0.01)
            allow_writer_commit.set()
            waiting = await asyncio.wait_for(create_task, timeout=5)
            loaded = await asyncio.wait_for(load_task, timeout=5)
            assert loaded == waiting.state
            await asyncio.wait_for(
                ProjectMaintenanceFoundationService(load_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                ),
                timeout=5,
            )
        async with sessions() as fresh_session:
            assert (
                await ProjectMaintenanceFoundationService(fresh_session).load_latest_state(
                    project_id, run_id
                )
            ).status is ProjectMaintenanceStatus.APPLY_CHANGE
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_precommit_failure_rolls_back_and_commit_ack_loss_is_indeterminate(
    async_session: AsyncSession,
    integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state = revision_state()
    project_id, run_id = await seed_state(async_session, initial_state)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as failing_session:
            before = await durable_snapshot(failing_session, run_id)
            original_flush = failing_session.flush

            async def fail_flush(*_: object, **__: object) -> None:
                raise RuntimeError("precommit failure")

            monkeypatch.setattr(failing_session, "flush", fail_flush)
            with pytest.raises(RuntimeError, match="precommit failure"):
                await ProjectMaintenanceFoundationService(failing_session).create_confirmation(
                    project_id,
                    run_id,
                    review_outcome=MaintenanceReviewOutcome.PASSED,
                )
            monkeypatch.setattr(failing_session, "flush", original_flush)
            assert await durable_snapshot(failing_session, run_id) == before
        async with sessions() as indeterminate_session:
            original_commit: Callable[[], object] = indeterminate_session.commit

            async def committed_then_lost() -> None:
                await original_commit()  # type: ignore[misc]
                raise RuntimeError("commit acknowledgement lost")

            monkeypatch.setattr(indeterminate_session, "commit", committed_then_lost)
            with pytest.raises(ProjectMaintenanceCommitIndeterminateError):
                await ProjectMaintenanceFoundationService(
                    indeterminate_session
                ).create_confirmation(
                    project_id,
                    run_id,
                    review_outcome=MaintenanceReviewOutcome.PASSED,
                )
        async with sessions() as fresh_session:
            await assert_exact_waiting_transition(
                fresh_session,
                project_id,
                run_id,
                initial_state,
                MaintenanceReviewOutcome.PASSED,
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_resolve_flush_failure_rolls_back_and_ack_loss_persists_once(
    async_session: AsyncSession,
    integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, run_id = await seed_state(async_session, revision_state())
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as request_session:
            waiting = await ProjectMaintenanceFoundationService(
                request_session
            ).create_confirmation(
                project_id, run_id, review_outcome=MaintenanceReviewOutcome.PASSED
            )
        expected_state = waiting.state.resolve_confirmation(
            live_action_request_id=str(waiting.action_request_id),
            action_status=ActionRequestStatus.PENDING,
            decision=MaintenanceDecision.APPROVE,
        )
        async with sessions() as failing_session:
            before = await durable_snapshot(failing_session, run_id)
            original_flush = failing_session.flush

            async def fail_flush(*_: object, **__: object) -> None:
                raise RuntimeError("resolve flush failed")

            monkeypatch.setattr(failing_session, "flush", fail_flush)
            with pytest.raises(RuntimeError, match="resolve flush failed"):
                await ProjectMaintenanceFoundationService(failing_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
            monkeypatch.setattr(failing_session, "flush", original_flush)
            assert await durable_snapshot(failing_session, run_id) == before
        async with sessions() as indeterminate_session:
            original_commit: Callable[[], object] = indeterminate_session.commit

            async def committed_then_lost() -> None:
                await original_commit()  # type: ignore[misc]
                raise RuntimeError("resolve acknowledgement lost")

            monkeypatch.setattr(indeterminate_session, "commit", committed_then_lost)
            with pytest.raises(ProjectMaintenanceCommitIndeterminateError):
                await ProjectMaintenanceFoundationService(indeterminate_session).resolve_action(
                    project_id,
                    run_id,
                    waiting.action_request_id,
                    decision=MaintenanceDecision.APPROVE,
                )
        async with sessions() as fresh_session:
            await assert_latest_transition(
                fresh_session,
                project_id,
                run_id,
                waiting.action_request_id,
                expected_state,
                ActionRequestStatus.APPROVED,
                MaintenanceDecision.APPROVE,
                "project_maintenance_revision_confirmation",
                ["approve", "revise", "cancel"],
                {
                    "confirmation_kind": "revision_confirmation",
                    "review_outcome": "passed",
                },
            )
            snapshot = await durable_snapshot(fresh_session, run_id)
            assert len(snapshot["actions"]) == 1  # type: ignore[arg-type]
            assert len(snapshot["checkpoints"]) == 3  # type: ignore[arg-type]
            assert len(snapshot["events"]) == 2  # type: ignore[arg-type]
    finally:
        await engine.dispose()
