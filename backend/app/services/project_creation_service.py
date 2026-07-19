"""Durable foundation persistence for project-creation workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, WorkflowStateError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Project,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.workflows.project_creation import (
    ProjectCreationState,
    ProjectCreationStatus,
    ProjectCreationValidationError,
)


@dataclass(frozen=True)
class ProjectCreationStarted:
    workflow_run_id: UUID


@dataclass(frozen=True)
class ProjectCreationWaiting:
    action_request_id: UUID
    state: ProjectCreationState


class ProjectCreationService:
    """Persist state-machine mechanics without persisting creative/user content."""

    _WORKFLOW_TYPE = WorkflowType.PROJECT_CREATION.value
    _CONCEPT_REVIEW_ACTION = "project_creation_concept_review"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, project_id: UUID) -> ProjectCreationStarted:
        project = await self.session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("Project not found.")
        active_run = await self.session.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.project_id == project.id,
                WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
                WorkflowRun.status.not_in(
                    (ProjectCreationStatus.COMPLETED.value, ProjectCreationStatus.REJECTED.value)
                ),
            )
        )
        if active_run is not None:
            raise ConflictError("Project creation is already active.")
        state = ProjectCreationState(
            status=ProjectCreationStatus.USER_IDEA,
            current_node="user_idea",
            awaiting_user=False,
        )
        run = WorkflowRun(
            project_id=project.id,
            workflow_type=self._WORKFLOW_TYPE,
            status=state.status.value,
            current_node=state.current_node,
            next_node=None,
            awaiting_user=state.awaiting_user,
        )
        self.session.add(run)
        try:
            await self.session.flush()
            project.current_workflow_id = run.id
            self.session.add_all(
                [
                    WorkflowCheckpoint(
                        workflow_run_id=run.id,
                        checkpoint_index=0,
                        node_name=state.current_node,
                        state_json=state.to_checkpoint(),
                    ),
                    WorkflowEvent(
                        workflow_run_id=run.id,
                        event_type="project_creation_started",
                        node_name=state.current_node,
                        payload=self._safe_event_payload(state.status),
                    ),
                ]
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return ProjectCreationStarted(workflow_run_id=run.id)

    async def request_concept_review(self, workflow_run_id: UUID) -> ProjectCreationWaiting:
        """Move from the initial idea marker to an explicit user decision gate."""
        run = await self._locked_run(workflow_run_id)
        state, checkpoint_index = await self._latest_state_locked(run)
        self._require_transition(state, ProjectCreationStatus.CONCEPT_OPTIONS)
        action = ActionRequest(
            workflow_run_id=run.id,
            project_id=run.project_id,
            request_type=self._CONCEPT_REVIEW_ACTION,
            status=ActionRequestStatus.PENDING.value,
            # The foundation stores no prompt or generated options.
            prompt="",
            options=[],
            default_option=None,
        )
        self.session.add(action)
        try:
            await self.session.flush()
            next_state = ProjectCreationState(
                status=ProjectCreationStatus.CONCEPT_OPTIONS,
                current_node="concept_review",
                awaiting_user=True,
                action_request_id=str(action.id),
            )
            self._persist_transition(
                run,
                checkpoint_index + 1,
                next_state,
                "concept_review_requested",
                action_request_id=action.id,
            )
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return ProjectCreationWaiting(action.id, next_state)

    async def resume_concept_review(
        self, workflow_run_id: UUID, action_request_id: UUID, decision: str
    ) -> ProjectCreationState:
        """The sole transition permitted while this foundation is awaiting a user."""
        run = await self._locked_run(workflow_run_id)
        state, checkpoint_index = await self._latest_state_locked(run)
        if state.status is not ProjectCreationStatus.CONCEPT_OPTIONS or not run.awaiting_user:
            raise WorkflowStateError("Project creation is not awaiting a concept-review decision.")
        if state.action_request_id != str(action_request_id):
            raise WorkflowStateError("Project creation decision does not match the waiting action.")
        if decision not in {"approved", "rejected"}:
            raise WorkflowStateError("Project creation decisions must be approved or rejected.")
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_request_id,
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.request_type == self._CONCEPT_REVIEW_ACTION,
            )
            .with_for_update()
        )
        if action is None or action.status != ActionRequestStatus.PENDING.value:
            raise WorkflowStateError("Project creation decision has already been resolved.")
        approved = decision == "approved"
        action.status = (
            ActionRequestStatus.APPROVED.value if approved else ActionRequestStatus.REJECTED.value
        )
        action.user_decision = decision
        action.resolved_at = datetime.now(UTC)
        next_state = ProjectCreationState(
            status=(
                ProjectCreationStatus.CONCEPT_REVIEWED
                if approved
                else ProjectCreationStatus.REJECTED
            ),
            current_node="concept_reviewed" if approved else "concept_review",
            awaiting_user=False,
        )
        self._persist_transition(
            run,
            checkpoint_index + 1,
            next_state,
            f"concept_review_{decision}",
            action_request_id=action.id,
        )
        await self._commit()
        return next_state

    async def complete(self, workflow_run_id: UUID) -> ProjectCreationState:
        """Close the foundation only after the explicit review decision."""
        run = await self._locked_run(workflow_run_id)
        state, checkpoint_index = await self._latest_state_locked(run)
        self._require_transition(state, ProjectCreationStatus.COMPLETED)
        next_state = ProjectCreationState(
            status=ProjectCreationStatus.COMPLETED,
            current_node="complete",
            awaiting_user=False,
        )
        self._persist_transition(run, checkpoint_index + 1, next_state, "project_creation_completed")
        await self._commit()
        return next_state

    async def load_latest_state(self, workflow_run_id: UUID) -> ProjectCreationState:
        run = await self.session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
            )
        )
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        state, _ = await self._latest_state_locked(run)
        return state

    async def _locked_run(self, workflow_run_id: UUID) -> WorkflowRun:
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
            )
            .with_for_update()
        )
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        return run

    async def _latest_state_locked(self, run: WorkflowRun) -> tuple[ProjectCreationState, int]:
        checkpoint = await self.session.scalar(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .limit(1)
        )
        if checkpoint is None:
            raise WorkflowStateError("Project creation checkpoint is missing.")
        try:
            state = ProjectCreationState.from_checkpoint(checkpoint.state_json)
        except ProjectCreationValidationError as error:
            raise WorkflowStateError("Project creation checkpoint is invalid.") from error
        await self._require_projection_consistency(run, state)
        return state, checkpoint.checkpoint_index

    async def _require_projection_consistency(
        self, run: WorkflowRun, state: ProjectCreationState
    ) -> None:
        if (
            run.status != state.status.value
            or run.current_node != state.current_node
            or run.awaiting_user != state.awaiting_user
            or run.next_node is not None
            or state.is_terminal != (run.completed_at is not None)
        ):
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        pending_action_ids = list(
            await self.session.scalars(
                select(ActionRequest.id).where(
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.project_id == run.project_id,
                    ActionRequest.request_type == self._CONCEPT_REVIEW_ACTION,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
            )
        )
        if state.awaiting_user:
            if len(pending_action_ids) != 1 or str(pending_action_ids[0]) != state.action_request_id:
                raise WorkflowStateError("Project creation workflow state is inconsistent.")
        elif pending_action_ids:
            raise WorkflowStateError("Project creation workflow state is inconsistent.")

    @staticmethod
    def _require_transition(
        state: ProjectCreationState, target: ProjectCreationStatus
    ) -> None:
        if state.is_terminal:
            raise WorkflowStateError("Project creation is in a terminal state.")
        transitions = {
            ProjectCreationStatus.USER_IDEA: {ProjectCreationStatus.CONCEPT_OPTIONS},
            ProjectCreationStatus.CONCEPT_REVIEWED: {ProjectCreationStatus.COMPLETED},
        }
        if target not in transitions.get(state.status, set()):
            if state.awaiting_user:
                raise WorkflowStateError("Project creation is awaiting a user decision.")
            raise WorkflowStateError("Project creation transition is not allowed.")

    def _persist_transition(
        self,
        run: WorkflowRun,
        checkpoint_index: int,
        state: ProjectCreationState,
        event_type: str,
        *,
        action_request_id: UUID | None = None,
    ) -> None:
        run.status = state.status.value
        run.current_node = state.current_node
        run.next_node = None
        run.awaiting_user = state.awaiting_user
        if state.is_terminal:
            run.completed_at = datetime.now(UTC)
        self.session.add_all(
            [
                WorkflowCheckpoint(
                    workflow_run_id=run.id,
                    checkpoint_index=checkpoint_index,
                    node_name=state.current_node,
                    state_json=state.to_checkpoint(),
                ),
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type=event_type,
                    node_name=state.current_node,
                    payload=self._safe_event_payload(state.status, action_request_id),
                ),
            ]
        )

    @staticmethod
    def _safe_event_payload(
        status: ProjectCreationStatus, action_request_id: UUID | None = None
    ) -> dict[str, str]:
        """Return the whole event payload schema; no caller supplies free-form data."""
        payload = {"status": status.value}
        if action_request_id is not None:
            payload["action_request_id"] = str(action_request_id)
        return payload

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
