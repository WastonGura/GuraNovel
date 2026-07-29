"""The secure, first vertical slice of project creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composition import ProjectCreationComposition
from app.agents.contracts import (
    ChiefEditorReviewOutput,
    ConceptAgentRequest,
    ConceptGenerationOutput,
    validate_chief_editor_review_output,
)
from app.core.errors import ConflictError, NotFoundError, WorkflowStateError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentVersion,
    DocumentSource,
    DocumentType,
    Project,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.services.document_service import DocumentService
from app.services.document_service import DocumentCommitIndeterminateError
from app.workspace.hashing import sha256_content
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


@dataclass(frozen=True)
class ProjectCreationConceptOptionRead:
    id: str
    title: str
    logline: str
    premise: str
    genres: tuple[str, ...]


@dataclass(frozen=True)
class ProjectCreationBlockingIssueRead:
    code: str
    message: str


@dataclass(frozen=True)
class ProjectCreationPendingActionRead:
    id: UUID
    type: str
    status: str
    allowed_decisions: tuple[str, ...]
    review_severity: str | None = None
    blocking_issues: tuple[ProjectCreationBlockingIssueRead, ...] = ()
    concept_options: tuple[ProjectCreationConceptOptionRead, ...] = ()


@dataclass(frozen=True)
class ProjectCreationRunRead:
    id: UUID
    type: str
    status: str
    current_node: str | None
    next_node: str | None
    awaiting_user: bool
    pending_action: ProjectCreationPendingActionRead | None


@dataclass(frozen=True)
class _Issue65Binding:
    report: ReviewReport
    document_id: UUID
    version_id: UUID
    review_severity: str
    blocking_issues: tuple[ProjectCreationBlockingIssueRead, ...]
    concepts: ConceptGenerationOutput


class ProjectCreationService:
    _WORKFLOW_TYPE = WorkflowType.PROJECT_CREATION.value
    _SELECT_ACTION = "project_creation_concept_selection"
    _REGENERATE_ACTION = "project_creation_concept_regeneration"
    _LEGACY_REVIEW_ACTION = "project_creation_concept_review"

    def __init__(
        self, session: AsyncSession, composition: ProjectCreationComposition | None = None
    ) -> None:
        self.session, self.composition = session, composition or ProjectCreationComposition()

    async def start(
        self, project_id: UUID, request: ConceptAgentRequest | None = None
    ) -> ProjectCreationStarted:
        """Generate and review synchronously; request is transient by construction."""
        if request is None:
            return await self._start_legacy(project_id)
        request = request.model_copy(update={"project_id": project_id, "workflow_run_id": None})
        # Provider work is deliberately outside a database transaction.  The
        # project existence and active-run checks are repeated under the final
        # project row lock before anything is staged.
        try:
            output = await self.composition.concept_agent.generate(request)
            review = await self.composition.chief_editor.review(output)
        except DocumentCommitIndeterminateError:
            raise
        except BaseException:
            await self.session.rollback()
            raise

        try:
            project = await self.session.scalar(
                select(Project).where(Project.id == project_id).with_for_update()
            )
            if project is None:
                raise NotFoundError("Project not found.")
            await self._validate_existing_runs_before_start(project.id)
        except BaseException:
            await self.session.rollback()
            raise
        state = ProjectCreationState(ProjectCreationStatus.USER_IDEA, "user_idea", False)
        run = WorkflowRun(
            project_id=project.id,
            workflow_type=self._WORKFLOW_TYPE,
            status=state.status.value,
            current_node=state.current_node,
            next_node=None,
            awaiting_user=False,
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
                        payload=self._safe_payload(state.status),
                    ),
                ]
            )
            # Imported here to keep DocumentService's package exports acyclic.
            from app.agents.persistence import stage_concept_generation_output

            document, concept_version_id, file_writes = await stage_concept_generation_output(
                document_service=DocumentService(self.session),
                project_id=project.id,
                workflow_run_id=run.id,
                output=output,
            )
            await self._record_review_and_gate(
                run, document.id, concept_version_id, output, review, 1
            )
            try:
                DocumentService(self.session).write_staged_files(document, file_writes)
            except DocumentCommitIndeterminateError:
                raise
            except Exception:
                raise DocumentCommitIndeterminateError() from None
            return ProjectCreationStarted(run.id)
        except DocumentCommitIndeterminateError:
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def _start_legacy(self, project_id: UUID) -> ProjectCreationStarted:
        """Direct-service #64 fixture compatibility; never reachable from HTTP."""
        try:
            project = await self.session.scalar(
                select(Project).where(Project.id == project_id).with_for_update()
            )
            if project is None:
                raise NotFoundError("Project not found.")
            await self._validate_existing_runs_before_start(project.id)
        except BaseException:
            await self.session.rollback()
            raise
        state = ProjectCreationState(ProjectCreationStatus.USER_IDEA, "user_idea", False)
        run = WorkflowRun(
            project_id=project.id,
            workflow_type=self._WORKFLOW_TYPE,
            status=state.status.value,
            current_node=state.current_node,
            next_node=None,
            awaiting_user=False,
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
                        payload=self._safe_payload(state.status),
                    ),
                ]
            )
            await self._commit()
        except DocumentCommitIndeterminateError:
            raise
        except BaseException:
            await self.session.rollback()
            raise
        return ProjectCreationStarted(run.id)

    async def _record_review_and_gate(
        self,
        run: WorkflowRun,
        document_id: UUID,
        version_id: UUID | None,
        output: ConceptGenerationOutput,
        review: ChiefEditorReviewOutput,
        index: int,
    ) -> None:
        blocking = [item.model_dump() for item in review.blocking_issues]
        report = ReviewReport(
            project_id=run.project_id,
            workflow_run_id=run.id,
            review_mode="concept_review",
            reviewer_agent_role="chief_editor_agent",
            target_document_id=document_id,
            target_version_id=version_id,
            passed=review.passed,
            summary=review.summary,
            blocking_issues=blocking,
            warnings=[i.model_dump() for i in review.warnings],
            notes=[i.model_dump() for i in review.notes],
            suggested_actions=[i.model_dump() for i in review.suggested_actions],
            raw_report={},
        )
        self.session.add(report)
        await self.session.flush()
        if review.blocking_issues:
            request_type, status, node, options, severity = (
                self._REGENERATE_ACTION,
                ProjectCreationStatus.REVISION_REQUIRED,
                "concept_revision",
                ["regenerate", "feedback"],
                "blocking",
            )
        else:
            request_type, status, node, options, severity = (
                self._SELECT_ACTION,
                ProjectCreationStatus.CONCEPT_OPTIONS,
                "concept_review",
                [o.id for o in output.options],
                "warning" if review.warnings else "clean",
            )
        action = ActionRequest(
            workflow_run_id=run.id,
            project_id=run.project_id,
            request_type=request_type,
            status=ActionRequestStatus.PENDING.value,
            prompt="",
            options=options,
            default_option=None,
            metadata_={
                "review_severity": severity,
                "review_report_id": str(report.id),
                "concept_document_id": str(document_id),
                "concept_version_id": str(version_id) if version_id is not None else None,
            },
        )
        self.session.add(action)
        await self.session.flush()
        state = ProjectCreationState(status, node, True, str(action.id))
        self._persist_transition(run, index, state, "concept_reviewed", action.id)
        await self._commit()

    async def request_concept_review(self, workflow_run_id: UUID) -> ProjectCreationWaiting:
        """Legacy direct-service fixture transition; no HTTP route exposes this."""
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id, WorkflowRun.workflow_type == self._WORKFLOW_TYPE
            )
            .with_for_update()
        )
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        state, index = await self._latest_state_locked(run)
        if state.status is not ProjectCreationStatus.USER_IDEA:
            raise WorkflowStateError()
        action = ActionRequest(
            workflow_run_id=run.id,
            project_id=run.project_id,
            request_type=self._LEGACY_REVIEW_ACTION,
            status=ActionRequestStatus.PENDING.value,
            prompt="",
            options=[],
            default_option=None,
        )
        self.session.add(action)
        try:
            await self.session.flush()
            next_state = ProjectCreationState(
                ProjectCreationStatus.CONCEPT_OPTIONS, "concept_review", True, str(action.id)
            )
            self._persist_transition(
                run, index + 1, next_state, "concept_review_requested", action.id
            )
            await self._commit()
        except DocumentCommitIndeterminateError:
            raise
        except BaseException:
            await self.session.rollback()
            raise
        return ProjectCreationWaiting(action.id, next_state)

    async def resume_concept_review(
        self, workflow_run_id: UUID, action_request_id: UUID, decision: str
    ) -> ProjectCreationState:
        if decision not in {"approved", "rejected"}:
            raise WorkflowStateError()
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id, WorkflowRun.workflow_type == self._WORKFLOW_TYPE
            )
            .with_for_update()
        )
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        state, index = await self._latest_state_locked(run)
        if state.action_request_id != str(action_request_id):
            raise WorkflowStateError()
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_request_id,
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.request_type == self._LEGACY_REVIEW_ACTION,
            )
            .with_for_update()
        )
        if not self._is_exact_legacy_pending_action(run, state, action):
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        action.status = (
            ActionRequestStatus.APPROVED.value
            if decision == "approved"
            else ActionRequestStatus.REJECTED.value
        )
        action.user_decision, action.resolved_at = decision, datetime.now(UTC)
        next_state = ProjectCreationState(
            ProjectCreationStatus.CONCEPT_REVIEWED
            if decision == "approved"
            else ProjectCreationStatus.REJECTED,
            "concept_reviewed" if decision == "approved" else "concept_review",
            False,
        )
        self._persist_transition(
            run, index + 1, next_state, f"concept_review_{decision}", action.id
        )
        await self._commit()
        return next_state

    async def load_latest_state(self, workflow_run_id: UUID) -> ProjectCreationState:
        run = await self.session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == workflow_run_id, WorkflowRun.workflow_type == self._WORKFLOW_TYPE
            )
        )
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        state, _ = await self._latest_state_locked(run)
        return state

    async def complete(self, workflow_run_id: UUID) -> ProjectCreationState:
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id, WorkflowRun.workflow_type == self._WORKFLOW_TYPE
            )
            .with_for_update()
        )
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        state, index = await self._latest_state_locked(run)
        if state.awaiting_user:
            raise WorkflowStateError("Project creation is awaiting a user decision.")
        if state.status is not ProjectCreationStatus.CONCEPT_REVIEWED:
            raise WorkflowStateError("Project creation transition is not allowed.")
        next_state = ProjectCreationState(ProjectCreationStatus.COMPLETED, "complete", False)
        self._persist_transition(run, index + 1, next_state, "project_creation_completed")
        await self._commit()
        return next_state

    async def resolve_action(
        self,
        project_id: UUID,
        run_id: UUID,
        action_id: UUID,
        *,
        decision: str,
        option_id: str | None = None,
        fused_concept: str | None = None,
        feedback: str | None = None,
    ) -> ProjectCreationState:
        """Resolve a #65 gate, releasing preflight locks on every safe failure."""
        try:
            return await self._resolve_action(
                project_id,
                run_id,
                action_id,
                decision=decision,
                option_id=option_id,
                fused_concept=fused_concept,
                feedback=feedback,
            )
        except DocumentCommitIndeterminateError:
            # A known durable commit may already exist; its staged files are a
            # reconciliation concern and must never be rolled back here.
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def _resolve_action(
        self,
        project_id: UUID,
        run_id: UUID,
        action_id: UUID,
        *,
        decision: str,
        option_id: str | None = None,
        fused_concept: str | None = None,
        feedback: str | None = None,
    ) -> ProjectCreationState:
        run, action, state, index, binding = await self._load_locked_issue65_action(
            project_id, run_id, action_id
        )
        if action.request_type == self._SELECT_ACTION:
            try:
                concept_document = await self.session.scalar(
                    select(Document)
                    .where(
                        Document.id == binding.document_id,
                        Document.project_id == project_id,
                        Document.path == "pitch/concept_options.md",
                    )
                    .with_for_update()
                )
                if concept_document is None or concept_document.current_version_id is None:
                    raise WorkflowStateError()
                # The document lock closes the artifact race left after the initial
                # workflow/action validation.  Revalidate every bound row and the
                # snapshot against the locked document before deriving or staging.
                binding = await self._load_issue65_binding(run, action)
                if decision == "select" and fused_concept is None:
                    if not isinstance(option_id, str) or option_id not in action.options:
                        raise WorkflowStateError()
                    from app.agents.persistence import selected_concept_markdown_from_output

                    content, decision_marker = (
                        selected_concept_markdown_from_output(binding.concepts, option_id),
                        "selected_option",
                    )
                elif (
                    decision == "fuse"
                    and isinstance(fused_concept, str)
                    and 1 <= len(fused_concept.strip()) <= 4000
                ):
                    content, decision_marker = (
                        f"# Selected Concept\n\n{fused_concept.strip()}\n",
                        "fused",
                    )
                else:
                    raise WorkflowStateError()
                action.status, action.user_decision, action.resolved_at = (
                    ActionRequestStatus.APPROVED.value,
                    decision_marker,
                    datetime.now(UTC),
                )
                document_service = DocumentService(self.session)
                selected_document = await self.session.scalar(
                    select(Document)
                    .where(
                        Document.project_id == project_id, Document.path == "pitch/selected_concept.md"
                    )
                    .with_for_update()
                )
                if selected_document is None:
                    selected_document, *file_writes = await document_service.stage_create_document(
                        project_id=project_id,
                        document_type=DocumentType.PITCH,
                        title="Selected concept",
                        path="pitch/selected_concept.md",
                        content=content,
                        source=DocumentSource.USER,
                        workflow_run_id=run.id,
                        change_summary="Author selected concept",
                    )
                else:
                    _, *file_writes = await document_service.stage_write_document(
                        document_id=selected_document.id,
                        content=content,
                        source=DocumentSource.USER,
                        expected_current_version_id=selected_document.current_version_id,
                        workflow_run_id=run.id,
                        change_summary="Author selected concept",
                    )
                next_state = ProjectCreationState(
                    ProjectCreationStatus.CONCEPT_SELECTED, "concept_selected", False
                )
                self._persist_transition(run, index + 1, next_state, "concept_selected", action.id)
                await self._commit()
            except DocumentCommitIndeterminateError:
                raise
            except BaseException:
                await self.session.rollback()
                raise
            try:
                document_service.write_staged_files(selected_document, file_writes)
            except DocumentCommitIndeterminateError:
                raise
            except Exception:
                raise DocumentCommitIndeterminateError() from None
            return next_state
        if action.request_type == self._REGENERATE_ACTION:
            if decision != "regenerate" and not (
                decision == "feedback"
                and isinstance(feedback, str)
                and 1 <= len(feedback.strip()) <= 1000
            ):
                raise WorkflowStateError()
            # Feedback is transient: it reaches the provider once but is never placed
            # in an action, event, checkpoint, or document metadata field.
            regeneration_request = ConceptAgentRequest(
                project_id=project_id,
                workflow_run_id=run.id,
                user_seed=feedback if decision == "feedback" else "regenerate concepts",
            )
            # This phase only validated locked state; release its transaction before
            # invoking external providers.  IDs and the transient request are the
            # only values carried across this boundary.
            await self.session.rollback()
            del run, action, state, index, binding
            try:
                output = await self.composition.concept_agent.generate(regeneration_request)
                review = await self.composition.chief_editor.review(output)
            except DocumentCommitIndeterminateError:
                raise
            except BaseException:
                await self.session.rollback()
                raise
            from app.agents.persistence import render_concept_options_markdown

            try:
                # Provider output is never authority for a workflow transition.
                # Reacquire all workflow locks and revalidate the current gate,
                # including its durable artifact binding, before staging a write.
                run, action, _, index, binding = await self._load_locked_issue65_action(
                    project_id, run_id, action_id
                )
                concept_document = await self.session.scalar(
                    select(Document)
                    .where(
                        Document.id == binding.document_id,
                        Document.project_id == project_id,
                        Document.path == "pitch/concept_options.md",
                    )
                    .with_for_update()
                )
                if concept_document is None or concept_document.current_version_id is None:
                    raise WorkflowStateError()
                # The document lock closes the last race between binding validation
                # and staging, so validate it again against the locked version.
                binding = await self._load_issue65_binding(run, action)
                action.status, action.user_decision, action.resolved_at = (
                    ActionRequestStatus.REVISED.value,
                    "regeneration_requested",
                    datetime.now(UTC),
                )
                document_service = DocumentService(self.session)
                version, *file_writes = await document_service.stage_write_document(
                    document_id=concept_document.id,
                    content=render_concept_options_markdown(output),
                    source=DocumentSource.CONCEPT_AGENT,
                    expected_current_version_id=concept_document.current_version_id,
                    agent_role="concept_agent",
                    workflow_run_id=run.id,
                    change_summary="Regenerated concept options",
                )
                await self._record_review_and_gate(
                    run, concept_document.id, version.id, output, review, index + 1
                )
            except DocumentCommitIndeterminateError:
                raise
            except BaseException:
                await self.session.rollback()
                raise
            # _record_review_and_gate committed.  Never roll back this known durable state.
            try:
                document_service.write_staged_files(concept_document, file_writes)
            except DocumentCommitIndeterminateError:
                raise
            except Exception:
                raise DocumentCommitIndeterminateError() from None
            next_state, _ = await self._latest_state_locked(run)
            return next_state
        raise WorkflowStateError()

    async def _load_locked_issue65_action(
        self, project_id: UUID, run_id: UUID, action_id: UUID
    ) -> tuple[WorkflowRun, ActionRequest, ProjectCreationState, int, _Issue65Binding]:
        """Lock and validate the current #65 user-decision gate without mutation."""
        run = await self._scoped_run(project_id, run_id, for_update=True)
        # Scope opaque action IDs before checking workflow state so foreign or
        # invented IDs are not distinguishable as a state oracle.
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_id,
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.project_id == project_id,
                ActionRequest.request_type.in_((self._SELECT_ACTION, self._REGENERATE_ACTION)),
            )
            .with_for_update()
        )
        if action is None:
            raise NotFoundError("Project creation action not found.")
        state, index = await self._latest_state_locked(run)
        if not state.awaiting_user or state.action_request_id != str(action_id):
            raise WorkflowStateError()
        if action.status != ActionRequestStatus.PENDING.value:
            raise WorkflowStateError()
        expected_action = {
            ProjectCreationStatus.CONCEPT_OPTIONS: self._SELECT_ACTION,
            ProjectCreationStatus.REVISION_REQUIRED: self._REGENERATE_ACTION,
        }.get(state.status)
        if action.request_type != expected_action:
            raise WorkflowStateError()
        return run, action, state, index, await self._load_issue65_binding(run, action)

    async def get_project_creation_run(
        self, project_id: UUID, run_id: UUID
    ) -> ProjectCreationRunRead:
        run = await self._scoped_run(project_id, run_id)
        state, _ = await self._latest_state_locked(run)
        pending = None
        if state.awaiting_user:
            try:
                action_id = UUID(state.action_request_id or "")
            except (TypeError, ValueError):
                raise WorkflowStateError("Project creation workflow state is inconsistent.") from None
            action = await self.session.scalar(
                select(ActionRequest).where(
                    ActionRequest.id == action_id,
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.project_id == project_id,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
            )
            if action is None:
                raise WorkflowStateError("Project creation workflow state is inconsistent.")
            if action.request_type == self._LEGACY_REVIEW_ACTION:
                if not self._is_exact_legacy_pending_action(run, state, action):
                    raise WorkflowStateError("Project creation workflow state is inconsistent.")
                pending = ProjectCreationPendingActionRead(
                    action.id, action.request_type, action.status, ("approved", "rejected")
                )
                return ProjectCreationRunRead(
                    run.id,
                    run.workflow_type,
                    run.status,
                    run.current_node,
                    run.next_node,
                    run.awaiting_user,
                    pending,
                )
            expected_action = {
                ProjectCreationStatus.CONCEPT_OPTIONS: self._SELECT_ACTION,
                ProjectCreationStatus.REVISION_REQUIRED: self._REGENERATE_ACTION,
            }.get(state.status)
            if action.request_type != expected_action:
                raise WorkflowStateError("Project creation workflow state is inconsistent.")
            binding = await self._load_issue65_binding(run, action)
            decisions = (
                ("select", "fuse")
                if action.request_type == self._SELECT_ACTION
                else ("regenerate", "feedback")
            )
            pending = ProjectCreationPendingActionRead(
                id=action.id,
                type=action.request_type,
                status=action.status,
                allowed_decisions=decisions,
                review_severity=binding.review_severity,
                blocking_issues=tuple(
                    ProjectCreationBlockingIssueRead(issue.code, issue.message)
                    for issue in binding.blocking_issues
                ),
                concept_options=tuple(
                    ProjectCreationConceptOptionRead(
                        option.id,
                        option.title,
                        option.logline,
                        option.premise,
                        tuple(option.genres),
                    )
                    for option in binding.concepts.options
                ),
            )
        return ProjectCreationRunRead(
            run.id,
            run.workflow_type,
            run.status,
            run.current_node,
            run.next_node,
            run.awaiting_user,
            pending,
        )

    @staticmethod
    def _is_exact_legacy_pending_action(
        run: WorkflowRun, state: ProjectCreationState, action: ActionRequest | None
    ) -> bool:
        return bool(
            action is not None
            and action.request_type == ProjectCreationService._LEGACY_REVIEW_ACTION
            and action.status == ActionRequestStatus.PENDING.value
            and action.workflow_run_id == run.id
            and action.project_id == run.project_id
            and action.chapter_id is None
            and state.status is ProjectCreationStatus.CONCEPT_OPTIONS
            and state.current_node == "concept_review"
            and state.awaiting_user
            and state.action_request_id == str(action.id)
            and action.prompt == ""
            and action.options == []
            and action.default_option is None
            and action.metadata_ == {}
            and action.user_decision is None
            and action.user_feedback is None
            and action.resolved_by_id is None
            and action.resolved_at is None
            and action.expires_at is None
        )

    async def _load_issue65_binding(
        self, run: WorkflowRun, action: ActionRequest
    ) -> _Issue65Binding:
        """Load the sole #65 report/artifact binding after validating its exact shape."""
        metadata = action.metadata_
        required_keys = {
            "review_severity",
            "review_report_id",
            "concept_document_id",
            "concept_version_id",
        }
        if (
            action.request_type not in (self._SELECT_ACTION, self._REGENERATE_ACTION)
            or action.workflow_run_id != run.id
            or action.project_id != run.project_id
            or action.chapter_id is not None
            or action.status != ActionRequestStatus.PENDING.value
            or action.prompt != ""
            or action.default_option is not None
            or action.user_decision is not None
            or action.user_feedback is not None
            or action.resolved_by_id is not None
            or action.resolved_at is not None
            or action.expires_at is not None
            or not isinstance(metadata, dict)
            or set(metadata) != required_keys
        ):
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        try:
            severity = metadata["review_severity"]
            report_value = metadata["review_report_id"]
            document_value = metadata["concept_document_id"]
            version_value = metadata["concept_version_id"]
            if not all(
                isinstance(value, str)
                for value in (severity, report_value, document_value, version_value)
            ):
                raise ValueError
            report_id, document_id, version_id = (
                UUID(report_value),
                UUID(document_value),
                UUID(version_value),
            )
            if (
                str(report_id) != report_value
                or str(document_id) != document_value
                or str(version_id) != version_value
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise WorkflowStateError("Project creation workflow state is inconsistent.") from None

        report = await self.session.scalar(
            select(ReviewReport)
            .join(DocumentVersion, ReviewReport.target_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(
                ReviewReport.id == report_id,
                ReviewReport.project_id == run.project_id,
                ReviewReport.chapter_id.is_(None),
                ReviewReport.workflow_run_id == run.id,
                ReviewReport.target_document_id == document_id,
                ReviewReport.target_version_id == version_id,
                ReviewReport.review_mode == "concept_review",
                Document.id == document_id,
                Document.project_id == run.project_id,
                Document.chapter_id.is_(None),
                Document.path == "pitch/concept_options.md",
                Document.current_version_id == version_id,
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.workflow_run_id == run.id,
            )
        )
        if (
            report is None
            or report.reviewer_agent_role != "chief_editor_agent"
            or report.raw_report != {}
        ):
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        try:
            reviewed = validate_chief_editor_review_output(
                {
                    "passed": report.passed,
                    "summary": report.summary,
                    "blocking_issues": report.blocking_issues,
                    "warnings": report.warnings,
                    "notes": report.notes,
                    "suggested_actions": report.suggested_actions,
                }
            )
        except BaseException:
            raise WorkflowStateError("Project creation workflow state is inconsistent.") from None
        if action.request_type == self._SELECT_ACTION:
            expected_severity = "warning" if reviewed.warnings else "clean"
            valid = (
                reviewed.passed is True
                and not reviewed.blocking_issues
                and severity == expected_severity
            )
        else:
            valid = (
                reviewed.passed is False
                and bool(reviewed.blocking_issues)
                and severity == "blocking"
                and action.options == ["regenerate", "feedback"]
            )
        if not valid:
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        try:
            from app.agents.persistence import parse_concept_options_markdown

            version = await self.session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.workflow_run_id == run.id,
                )
            )
            if version is None:
                raise ValueError
            snapshot_content = await DocumentService(self.session).read_version_content(
                document_id, version_id
            )
            if sha256_content(snapshot_content) != version.content_hash:
                raise ValueError
            concepts = parse_concept_options_markdown(snapshot_content)
        except BaseException:
            raise WorkflowStateError("Project creation workflow state is inconsistent.") from None
        if action.request_type == self._SELECT_ACTION and action.options != [
            option.id for option in concepts.options
        ]:
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        return _Issue65Binding(
            report,
            document_id,
            version_id,
            severity,
            tuple(
                ProjectCreationBlockingIssueRead(issue.code, issue.message)
                for issue in reviewed.blocking_issues
            ),
            concepts,
        )

    async def _validate_existing_runs_before_start(self, project_id: UUID) -> None:
        """Fail closed unless every earlier creation run is durably terminal."""
        runs = list(
            await self.session.scalars(
                select(WorkflowRun)
                .where(
                    WorkflowRun.project_id == project_id,
                    WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
                )
                .with_for_update()
            )
        )
        for run in runs:
            state, _ = await self._latest_state_locked(run)
            if not state.is_terminal:
                raise ConflictError("Project creation is already active.")
            # A terminal run whose resolution action was deleted is corrupt.
            resolved = await self.session.scalar(
                select(ActionRequest.id).where(
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status.in_(
                        (ActionRequestStatus.APPROVED.value, ActionRequestStatus.REJECTED.value)
                    ),
                )
            )
            if resolved is None:
                raise WorkflowStateError(
                    "Project creation workflow state is inconsistent."
                )

    async def _scoped_run(
        self, project_id: UUID, run_id: UUID, *, for_update: bool = False
    ) -> WorkflowRun:
        q = select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.project_id == project_id,
            WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
        )
        run = await self.session.scalar(q.with_for_update() if for_update else q)
        if run is None:
            raise NotFoundError("Project creation workflow not found.")
        return run

    async def _latest_state_locked(self, run: WorkflowRun) -> tuple[ProjectCreationState, int]:
        cp = await self.session.scalar(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .limit(1)
        )
        if cp is None:
            raise WorkflowStateError("Project creation checkpoint is missing.")
        try:
            state = ProjectCreationState.from_checkpoint(cp.state_json)
        except ProjectCreationValidationError as error:
            raise WorkflowStateError("Project creation checkpoint is invalid.") from error
        if (
            run.status != state.status.value
            or run.current_node != state.current_node
            or run.awaiting_user != state.awaiting_user
            or run.next_node is not None
            or state.is_terminal != (run.completed_at is not None)
        ):
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        pending_ids = list(
            await self.session.scalars(
                select(ActionRequest.id).where(
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.project_id == run.project_id,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
            )
        )
        if state.awaiting_user:
            if len(pending_ids) != 1 or str(pending_ids[0]) != state.action_request_id:
                raise WorkflowStateError("Project creation workflow state is inconsistent.")
        elif pending_ids:
            raise WorkflowStateError("Project creation workflow state is inconsistent.")
        return state, cp.checkpoint_index

    def _persist_transition(
        self,
        run: WorkflowRun,
        index: int,
        state: ProjectCreationState,
        event: str,
        action_id: UUID | None = None,
    ) -> None:
        run.status, run.current_node, run.next_node, run.awaiting_user = (
            state.status.value,
            state.current_node,
            None,
            state.awaiting_user,
        )
        if state.is_terminal:
            run.completed_at = datetime.now(UTC)
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
                    event_type=event,
                    node_name=state.current_node,
                    payload=self._safe_payload(state.status, action_id),
                ),
            ]
        )

    @staticmethod
    def _safe_payload(
        status: ProjectCreationStatus, action_id: UUID | None = None
    ) -> dict[str, str]:
        payload = {"status": status.value}
        if action_id:
            payload["action_request_id"] = str(action_id)
        return payload

    # Kept as small compatibility helpers for callers of the #64 foundation.
    _safe_event_payload = _safe_payload

    @staticmethod
    def _require_transition(state: ProjectCreationState, target: ProjectCreationStatus) -> None:
        if state.is_terminal:
            raise WorkflowStateError("Project creation is in a terminal state.")
        if state.awaiting_user:
            raise WorkflowStateError("Project creation is awaiting a user decision.")
        raise WorkflowStateError("Project creation transition is not allowed.")

    async def _commit(self) -> None:
        try:
            await self.session.commit()
        except BaseException:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise DocumentCommitIndeterminateError() from None
