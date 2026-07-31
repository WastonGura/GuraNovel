"""Durable foundation store for project-maintenance confirmation checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError, WorkflowStateError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Project,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.workflows.project_maintenance import (
    MaintenanceConfirmationKind,
    MaintenanceDecision,
    MaintenanceReviewOutcome,
    ProjectMaintenanceState,
    ProjectMaintenanceStatus,
    ProjectMaintenanceValidationError,
)


class ProjectMaintenanceCommitIndeterminateError(AppError):
    status_code = 500
    code = "project_maintenance_commit_indeterminate"
    default_message = (
        "The project-maintenance outcome could not be confirmed. "
        "Reconciliation is required before retrying."
    )


@dataclass(frozen=True)
class ProjectMaintenanceWaiting:
    action_request_id: UUID
    state: ProjectMaintenanceState


class ProjectMaintenanceFoundationService:
    _WORKFLOW_TYPE = WorkflowType.PROJECT_MAINTENANCE.value
    _REVISION_ACTION = "project_maintenance_revision_confirmation"
    _CONSISTENCY_ACTION = "project_maintenance_consistency_warning"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_latest_state(
        self, project_id: UUID, run_id: UUID
    ) -> ProjectMaintenanceState:
        try:
            run = await self._locked_run(project_id, run_id)
            state, _ = await self._latest_state(run, for_update=True)
        except BaseException as error:
            try:
                await self.session.rollback()
            except BaseException as rollback_error:
                raise error from rollback_error
            raise
        await self.session.rollback()
        return state

    async def create_confirmation(
        self,
        project_id: UUID,
        run_id: UUID,
        *,
        review_outcome: MaintenanceReviewOutcome,
        consistency_report_id: str | None = None,
    ) -> ProjectMaintenanceWaiting:
        try:
            if not isinstance(review_outcome, MaintenanceReviewOutcome):
                raise WorkflowStateError()
            run = await self._locked_run(project_id, run_id)
            state, index = await self._latest_state(run, for_update=True)
            if state.status is ProjectMaintenanceStatus.REVISION_PLAN:
                if consistency_report_id is not None:
                    raise WorkflowStateError()
                kind = MaintenanceConfirmationKind.REVISION_CONFIRMATION
                request_type = self._REVISION_ACTION
                options = self._revision_options(
                    review_outcome, corrective=bool(state.applied_document_version_ids)
                )
            elif (
                state.status is ProjectMaintenanceStatus.CONSISTENCY_REVIEW
                and review_outcome is MaintenanceReviewOutcome.WARNING
                and consistency_report_id is not None
            ):
                kind = MaintenanceConfirmationKind.CONSISTENCY_WARNING
                request_type = self._CONSISTENCY_ACTION
                options = [MaintenanceDecision.ACCEPT_WARNING.value, MaintenanceDecision.REVISE.value]
            else:
                raise WorkflowStateError()

            action = ActionRequest(
                workflow_run_id=run.id,
                project_id=project_id,
                chapter_id=None,
                request_type=request_type,
                status=ActionRequestStatus.PENDING.value,
                prompt="",
                options=options,
                default_option=None,
                metadata_={
                    "confirmation_kind": kind.value,
                    "review_outcome": review_outcome.value,
                },
            )
            self.session.add(action)
            await self.session.flush()
            if kind is MaintenanceConfirmationKind.REVISION_CONFIRMATION:
                next_state = state.request_revision_confirmation(
                    action_request_id=str(action.id), review_outcome=review_outcome
                )
            else:
                assert consistency_report_id is not None
                next_state = state.route_consistency_review(
                    review_outcome=review_outcome,
                    consistency_report_id=consistency_report_id,
                    action_request_id=str(action.id),
                )
            self._persist_transition(
                run,
                index + 1,
                next_state,
                "project_maintenance_confirmation_requested",
            )
            await self._commit()
            return ProjectMaintenanceWaiting(action.id, next_state)
        except ProjectMaintenanceCommitIndeterminateError:
            raise
        except ProjectMaintenanceValidationError as error:
            await self.session.rollback()
            raise WorkflowStateError() from error
        except BaseException:
            await self.session.rollback()
            raise

    async def resolve_action(
        self,
        project_id: UUID,
        run_id: UUID,
        action_id: UUID,
        *,
        decision: MaintenanceDecision,
    ) -> ProjectMaintenanceState:
        try:
            if not isinstance(decision, MaintenanceDecision):
                raise WorkflowStateError()
            run = await self._locked_run(project_id, run_id)
            action = await self.session.scalar(
                select(ActionRequest)
                .where(
                    ActionRequest.id == action_id,
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.project_id == project_id,
                    ActionRequest.chapter_id.is_(None),
                )
                .with_for_update()
            )
            if action is None:
                raise NotFoundError("Project-maintenance action not found.")
            state, index = await self._latest_state(run, for_update=True)
            self._validate_action_binding(run, state, action)
            if decision.value not in action.options:
                raise WorkflowStateError()
            next_state = state.resolve_confirmation(
                live_action_request_id=str(action.id),
                action_status=ActionRequestStatus(action.status),
                decision=decision,
            )
            action.status = {
                MaintenanceDecision.APPROVE: ActionRequestStatus.APPROVED.value,
                MaintenanceDecision.ACCEPT_WARNING: ActionRequestStatus.FORCE_APPROVED.value,
                MaintenanceDecision.REVISE: ActionRequestStatus.REVISED.value,
                MaintenanceDecision.CANCEL: ActionRequestStatus.CANCELLED.value,
            }[decision]
            action.user_decision = decision.value
            action.resolved_at = datetime.now(UTC)
            self._persist_transition(
                run,
                index + 1,
                next_state,
                "project_maintenance_action_resolved",
                action.id,
            )
            await self.session.flush()
            await self._commit()
            return next_state
        except ProjectMaintenanceCommitIndeterminateError:
            raise
        except (ProjectMaintenanceValidationError, ValueError) as error:
            await self.session.rollback()
            raise WorkflowStateError() from error
        except BaseException:
            await self.session.rollback()
            raise

    async def _scoped_run(
        self, project_id: UUID, run_id: UUID, *, for_update: bool = False
    ) -> WorkflowRun:
        query = select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.project_id == project_id,
            WorkflowRun.chapter_id.is_(None),
            WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
        )
        run = await self.session.scalar(query.with_for_update() if for_update else query)
        if run is None:
            raise NotFoundError("Project-maintenance workflow not found.")
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        if project.current_workflow_id != run.id:
            raise WorkflowStateError("Project-maintenance workflow state is inconsistent.")
        return run

    async def _locked_run(self, project_id: UUID, run_id: UUID) -> WorkflowRun:
        project = await self.session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("Project not found.")
        run = await self._scoped_run(project_id, run_id, for_update=True)
        if project.current_workflow_id != run.id:
            raise WorkflowStateError("Project-maintenance workflow state is inconsistent.")
        return run

    async def _latest_state(
        self, run: WorkflowRun, *, for_update: bool = False
    ) -> tuple[ProjectMaintenanceState, int]:
        query = (
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .limit(1)
        )
        checkpoint = await self.session.scalar(
            query.with_for_update() if for_update else query
        )
        if checkpoint is None:
            raise WorkflowStateError("Project-maintenance checkpoint is missing.")
        try:
            state = ProjectMaintenanceState.from_checkpoint(checkpoint.state_json)
        except ProjectMaintenanceValidationError as error:
            raise WorkflowStateError("Project-maintenance checkpoint is invalid.") from error
        if (
            checkpoint.node_name != state.current_node
            or run.status != state.status.value
            or run.current_node != state.current_node
            or run.next_node is not None
            or run.awaiting_user != state.awaiting_user
            or state.is_terminal != (run.completed_at is not None)
        ):
            raise WorkflowStateError("Project-maintenance workflow state is inconsistent.")
        pending_actions = list(
            await self.session.scalars(
                select(ActionRequest).where(
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
            )
        )
        if state.awaiting_user:
            if len(pending_actions) != 1 or str(pending_actions[0].id) != state.action_request_id:
                raise WorkflowStateError("Project-maintenance workflow state is inconsistent.")
            self._validate_action_binding(run, state, pending_actions[0])
        elif pending_actions:
            raise WorkflowStateError("Project-maintenance workflow state is inconsistent.")
        return state, checkpoint.checkpoint_index

    @classmethod
    def _validate_action_binding(
        cls, run: WorkflowRun, state: ProjectMaintenanceState, action: ActionRequest
    ) -> None:
        if state.confirmation_kind is MaintenanceConfirmationKind.REVISION_CONFIRMATION:
            request_type = cls._REVISION_ACTION
            options = cls._revision_options(
                state.gate_review_outcome,
                corrective=bool(state.applied_document_version_ids),
            )
        elif state.confirmation_kind is MaintenanceConfirmationKind.CONSISTENCY_WARNING:
            request_type = cls._CONSISTENCY_ACTION
            options = [MaintenanceDecision.ACCEPT_WARNING.value, MaintenanceDecision.REVISE.value]
        else:
            raise WorkflowStateError("Project-maintenance action binding is invalid.")
        expected_metadata = {
            "confirmation_kind": state.confirmation_kind.value,
            "review_outcome": state.gate_review_outcome.value,
        }
        if (
            action.id is None
            or state.action_request_id != str(action.id)
            or action.workflow_run_id != run.id
            or action.project_id != run.project_id
            or action.chapter_id is not None
            or action.request_type != request_type
            or action.status != ActionRequestStatus.PENDING.value
            or action.prompt != ""
            or action.options != options
            or action.default_option is not None
            or action.user_decision is not None
            or action.user_feedback is not None
            or action.resolved_by_id is not None
            or action.resolved_at is not None
            or action.expires_at is not None
            or action.metadata_ != expected_metadata
        ):
            raise WorkflowStateError("Project-maintenance action binding is invalid.")

    @staticmethod
    def _revision_options(
        outcome: MaintenanceReviewOutcome | None, *, corrective: bool = False
    ) -> list[str]:
        if outcome is MaintenanceReviewOutcome.BLOCKING:
            return (
                [MaintenanceDecision.REVISE.value]
                if corrective
                else [MaintenanceDecision.REVISE.value, MaintenanceDecision.CANCEL.value]
            )
        if outcome in {MaintenanceReviewOutcome.PASSED, MaintenanceReviewOutcome.WARNING}:
            options = [
                MaintenanceDecision.APPROVE.value,
                MaintenanceDecision.REVISE.value,
            ]
            if not corrective:
                options.append(MaintenanceDecision.CANCEL.value)
            return options
        raise WorkflowStateError("Project-maintenance review outcome is invalid.")

    def _persist_transition(
        self,
        run: WorkflowRun,
        index: int,
        state: ProjectMaintenanceState,
        event_type: str,
        action_id: UUID | None = None,
    ) -> None:
        run.status = state.status.value
        run.current_node = state.current_node
        run.next_node = None
        run.awaiting_user = state.awaiting_user
        if state.is_terminal:
            run.completed_at = datetime.now(UTC)
        payload = state.to_public_event_payload(
            action_request_id=str(action_id) if action_id is not None else None
        )
        self.session.add_all(
            [
                WorkflowCheckpoint(
                    workflow_run_id=run.id,
                    checkpoint_index=index,
                    node_name=state.current_node,
                    state_json=state.to_checkpoint(),
                ),
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type=event_type,
                    node_name=state.current_node,
                    payload=payload,
                ),
            ]
        )

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except BaseException:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise ProjectMaintenanceCommitIndeterminateError() from None
