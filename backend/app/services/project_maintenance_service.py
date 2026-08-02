"""Project-scoped orchestration for the maintenance analysis confirmation gate."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.maintenance_agents import (
    ArchivistAgent,
    ChiefEditorAgent,
    LoreAgent,
    PlotArchitectAgent,
    WorldbuildingAgent,
)
from app.agents.maintenance_contracts import (
    AffectedItemReference,
    AppliedDocumentReference,
    ApplyChangeOutput,
    ApplyChangeRequest,
    ChiefEditorMaintenanceImpactOutput,
    ConsistencyFindingSeverity,
    ConsistencyReviewOutcome,
    ConsistencyReviewOutput,
    DocumentVersionReference,
    ImpactAffectedItem,
    LoreImpactOutput,
    MaintenanceImpactRequest,
    PostChangeRequest,
    RevisionOperation,
    RevisionOperationKind,
    RevisionPlanOutput,
    RevisionPlanRequest,
    WarningSeverity,
)
from app.core.errors import AppError, ConflictError, NotFoundError, WorkflowStateError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    MaintenanceAffectedItem,
    MaintenanceChange,
    Project,
    ReviewReport,
    ReviewMode,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.services.document_service import DocumentCommitIndeterminateError, DocumentService
from app.services.maintenance_change_service import MaintenanceChangeService
from app.services.project_maintenance_foundation_service import (
    ProjectMaintenanceCommitIndeterminateError,
    ProjectMaintenanceFoundationService,
)
from app.workflows.project_maintenance import (
    MaintenanceConfirmationKind,
    MaintenanceDecision,
    MaintenanceReviewOutcome,
    ProjectMaintenanceState,
    ProjectMaintenanceStatus,
)
from app.workflows.project_maintenance_types import ImpactLevel
from app.workspace.hashing import sha256_content


_MAX_DOCUMENTS = 128
_TERMINAL_STATUSES = {
    ProjectMaintenanceStatus.PROJECT_UPDATED.value,
    ProjectMaintenanceStatus.CANCELLED.value,
}
_IMPACT_RANK = {ImpactLevel.LOW: 0, ImpactLevel.MEDIUM: 1, ImpactLevel.HIGH: 2}


@dataclass(frozen=True)
class ProjectMaintenanceComposition:
    lore_agent: LoreAgent
    chief_editor_agent: ChiefEditorAgent
    plot_architect_agent: PlotArchitectAgent
    worldbuilding_agent: WorldbuildingAgent
    archivist_agent: ArchivistAgent | None = None


@dataclass(frozen=True)
class ProjectMaintenanceStarted:
    workflow_run_id: UUID
    maintenance_change_id: UUID
    revision_plan_id: UUID
    revision_plan_document_id: UUID
    revision_plan_version_id: UUID
    action_request_id: UUID
    state: ProjectMaintenanceState


@dataclass(frozen=True)
class _ReconciledAffectedItem:
    id: UUID
    item: ImpactAffectedItem


@dataclass(frozen=True)
class _CanonicalOperation:
    operation_id: UUID
    operation: RevisionOperationKind
    target: DocumentVersionReference
    affected_item_ids: tuple[UUID, ...]
    instruction: str
    rationale: tuple[str, ...]
    provider_operation_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class _PreparedPlan:
    plan_id: UUID
    operations: tuple[_CanonicalOperation, ...]
    content: str
    outcome: MaintenanceReviewOutcome


@dataclass(frozen=True)
class _LockedMaintenanceContext:
    run: WorkflowRun
    state: ProjectMaintenanceState
    checkpoint_index: int
    change: MaintenanceChange


@dataclass(frozen=True)
class _RevisionCycleContext:
    locked: _LockedMaintenanceContext
    request: RevisionPlanRequest
    affected: tuple[_ReconciledAffectedItem, ...]
    plan_document_id: UUID
    plan_version_id: UUID


@dataclass(frozen=True)
class _ApplyCycleContext:
    locked: _LockedMaintenanceContext
    approval: ActionRequest
    request: ApplyChangeRequest


@dataclass(frozen=True)
class _ConsistencyCycleContext:
    locked: _LockedMaintenanceContext
    request: PostChangeRequest


def _safe_text(value: object, label: str, *, maximum: int, allow_multiline: bool) -> str:
    if type(value) is not str:
        raise WorkflowStateError(f"{label} is invalid.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise WorkflowStateError(f"{label} is invalid.")
    allowed_controls = {"\t", "\n", "\r"} if allow_multiline else set()
    if any(
        unicodedata.category(character) == "Cc" and character not in allowed_controls
        for character in normalized
    ):
        raise WorkflowStateError(f"{label} is invalid.")
    return normalized


def _checkpoint(run_id: UUID, index: int, state: ProjectMaintenanceState) -> WorkflowCheckpoint:
    return WorkflowCheckpoint(
        workflow_run_id=run_id,
        checkpoint_index=index,
        node_name=state.current_node,
        state_json=state.to_checkpoint(),
    )


def _event(
    run_id: UUID,
    event_type: str,
    state: ProjectMaintenanceState,
    *,
    action_id: UUID | None = None,
    created_at: datetime | None = None,
) -> WorkflowEvent:
    event = WorkflowEvent(
        workflow_run_id=run_id,
        event_type=event_type,
        node_name=state.current_node,
        payload=state.to_public_event_payload(
            action_request_id=str(action_id) if action_id is not None else None
        ),
    )
    if created_at is not None:
        event.created_at = created_at
    return event


def _confirmation_options(outcome: MaintenanceReviewOutcome) -> list[str]:
    if outcome is MaintenanceReviewOutcome.BLOCKING:
        return [MaintenanceDecision.REVISE.value, MaintenanceDecision.CANCEL.value]
    return [
        MaintenanceDecision.APPROVE.value,
        MaintenanceDecision.REVISE.value,
        MaintenanceDecision.CANCEL.value,
    ]


def _reconcile_affected_items(
    lore: LoreImpactOutput,
    chief: ChiefEditorMaintenanceImpactOutput,
) -> tuple[_ReconciledAffectedItem, ...]:
    ordered: list[ImpactAffectedItem] = []
    positions: dict[str, int] = {}
    for candidate in (*lore.affected_items, *chief.affected_items):
        position = positions.get(candidate.stable_reference)
        if position is None:
            positions[candidate.stable_reference] = len(ordered)
            ordered.append(candidate)
            continue
        existing = ordered[position]
        if existing.item_type is not candidate.item_type or existing.document != candidate.document:
            raise WorkflowStateError("Maintenance impact outputs conflict.")
        if _IMPACT_RANK[candidate.impact_level] > _IMPACT_RANK[existing.impact_level]:
            ordered[position] = existing.model_copy(update={"impact_level": candidate.impact_level})
    return tuple(_ReconciledAffectedItem(uuid4(), item) for item in ordered)


def _reconcile_operations(
    plot: RevisionPlanOutput,
    world: RevisionPlanOutput,
    *,
    affected_item_ids: frozenset[UUID],
) -> tuple[_CanonicalOperation, ...]:
    ordered: list[_CanonicalOperation] = []
    positions: dict[tuple[UUID, UUID], int] = {}
    for provider_plan in (plot, world):
        for candidate in provider_plan.operations:
            key = (candidate.target.document_id, candidate.target.current_version_id)
            position = positions.get(key)
            if position is None:
                positions[key] = len(ordered)
                ordered.append(
                    _CanonicalOperation(
                        operation_id=uuid4(),
                        operation=candidate.operation,
                        target=candidate.target,
                        affected_item_ids=candidate.affected_item_ids,
                        instruction=candidate.instruction,
                        rationale=(candidate.instruction,),
                        provider_operation_ids=(candidate.operation_id,),
                    )
                )
                continue
            existing = ordered[position]
            if existing.operation is not candidate.operation or frozenset(
                existing.affected_item_ids
            ) != frozenset(candidate.affected_item_ids):
                raise WorkflowStateError("Maintenance revision-plan outputs conflict.")
            rationales = tuple(dict.fromkeys((*existing.rationale, candidate.instruction)))
            instruction = "\n".join(rationales)
            if len(instruction) > 1000:
                raise WorkflowStateError("Maintenance revision-plan outputs conflict.")
            ordered[position] = _CanonicalOperation(
                operation_id=existing.operation_id,
                operation=existing.operation,
                target=existing.target,
                affected_item_ids=existing.affected_item_ids,
                instruction=instruction,
                rationale=rationales,
                provider_operation_ids=(*existing.provider_operation_ids, candidate.operation_id),
            )
    covered = frozenset(
        affected_id for operation in ordered for affected_id in operation.affected_item_ids
    )
    if not ordered or covered != affected_item_ids:
        raise WorkflowStateError("Maintenance revision plan does not cover its impact set.")
    _ensure_apply_compatible_operations(ordered)
    return tuple(ordered)


def _ensure_apply_compatible_operations(
    operations: tuple[_CanonicalOperation, ...] | tuple[RevisionOperation, ...],
) -> None:
    operation_kinds = tuple(getattr(item, "operation", None) for item in operations)
    if (
        RevisionOperationKind.RETIRE in operation_kinds
        or RevisionOperationKind.REVISE not in operation_kinds
    ):
        raise WorkflowStateError("Maintenance revision plan cannot be applied.")


def _prepare_plan(
    *,
    project_id: UUID,
    run_id: UUID,
    change_id: UUID,
    affected: tuple[_ReconciledAffectedItem, ...],
    lore: LoreImpactOutput,
    chief: ChiefEditorMaintenanceImpactOutput,
    plot: RevisionPlanOutput,
    world: RevisionPlanOutput,
) -> _PreparedPlan:
    plan_id = uuid4()
    operations = _reconcile_operations(
        plot,
        world,
        affected_item_ids=frozenset(item.id for item in affected),
    )
    all_warnings = (*lore.warnings, *chief.warnings, *plot.warnings, *world.warnings)
    severities = {warning.severity for warning in all_warnings}
    if WarningSeverity.BLOCKING in severities:
        outcome = MaintenanceReviewOutcome.BLOCKING
    elif severities:
        outcome = MaintenanceReviewOutcome.WARNING
    else:
        outcome = MaintenanceReviewOutcome.PASSED

    warning_payloads = [warning.model_dump(mode="json") for warning in all_warnings]
    operation_payloads: list[dict[str, object]] = []
    rationale_payloads: dict[str, list[str]] = {}
    operation_provenance: dict[str, list[str]] = {}
    for sequence, operation in enumerate(operations, start=1):
        operation_id = str(operation.operation_id)
        operation_payloads.append(
            {
                "operation_id": operation_id,
                "sequence": sequence,
                "operation": operation.operation.value,
                "target": operation.target.model_dump(mode="json"),
                "affected_item_ids": [str(item) for item in operation.affected_item_ids],
                "instruction": operation.instruction,
            }
        )
        rationale_payloads[operation_id] = list(operation.rationale)
        operation_provenance[operation_id] = [
            str(item) for item in operation.provider_operation_ids
        ]
    payload = {
        "version": 1,
        "revision_plan": {
            "plan_id": str(plan_id),
            "summary": "Apply the reconciled project changes in canonical sequence.",
            "operations": operation_payloads,
            "safety": {
                "requires_user_confirmation": True,
                "preserve_existing_versions": True,
                "direct_write_authority": False,
            },
            "warnings": [],
        },
        "bindings": {
            "project_id": str(project_id),
            "workflow_run_id": str(run_id),
            "change_request_id": str(change_id),
        },
        "review_outcome": outcome.value,
        "provenance": {
            "source_plan_ids": {
                "plot_architect": str(plot.plan_id),
                "worldbuilding": str(world.plan_id),
            },
            "source_operation_ids": operation_provenance,
        },
        "rationale": rationale_payloads,
        "risks": warning_payloads,
        "rollback_guidance": (
            "Canonical documents remain versioned; restore each recorded current_version_id "
            "through DocumentService if an approved change must be rolled back."
        ),
    }
    content = (
        "# Project Maintenance Revision Plan\n\n"
        "This plan is inert until the exact pending confirmation action is approved.\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        "```\n"
    )
    return _PreparedPlan(plan_id, operations, content, outcome)


def _decode_persisted_plan(
    content: str,
    *,
    project_id: UUID,
    run_id: UUID,
    change_id: UUID,
    outcome: MaintenanceReviewOutcome,
) -> tuple[RevisionPlanOutput, dict[str, object]]:
    prefix = (
        "# Project Maintenance Revision Plan\n\n"
        "This plan is inert until the exact pending confirmation action is approved.\n\n"
        "```json\n"
    )
    suffix = "\n```\n"
    if not content.startswith(prefix) or not content.endswith(suffix):
        raise WorkflowStateError("Project-maintenance plan content is invalid.")
    try:
        payload = json.loads(content[len(prefix) : -len(suffix)])
        if type(payload) is not dict or set(payload) != {
            "version",
            "revision_plan",
            "bindings",
            "review_outcome",
            "provenance",
            "rationale",
            "risks",
            "rollback_guidance",
        }:
            raise ValueError
        bindings = payload["bindings"]
        if type(bindings) is not dict or bindings != {
            "project_id": str(project_id),
            "workflow_run_id": str(run_id),
            "change_request_id": str(change_id),
        }:
            raise ValueError
        if payload["version"] != 1 or payload["review_outcome"] != outcome.value:
            raise ValueError
        plan = RevisionPlanOutput.model_validate(payload["revision_plan"])
        _ensure_apply_compatible_operations(plan.operations)
        rationale = payload["rationale"]
        provenance = payload["provenance"]
        operation_ids = {str(item.operation_id) for item in plan.operations}
        if (
            type(rationale) is not dict
            or set(rationale) != operation_ids
            or any(
                type(items) is not list
                or not items
                or any(type(item) is not str or not item.strip() for item in items)
                for items in rationale.values()
            )
            or type(provenance) is not dict
            or set(provenance) != {"source_plan_ids", "source_operation_ids"}
            or type(provenance["source_operation_ids"]) is not dict
            or set(provenance["source_operation_ids"]) != operation_ids
            or type(payload["risks"]) is not list
            or type(payload["rollback_guidance"]) is not str
            or not payload["rollback_guidance"].strip()
        ):
            raise ValueError
    except Exception:
        raise WorkflowStateError("Project-maintenance plan content is invalid.") from None
    return plan, payload


class ProjectMaintenanceService:
    """Analyze a project change and atomically persist its confirmation gate."""

    _ACTION_TYPE = "project_maintenance_revision_confirmation"

    def __init__(
        self,
        session: AsyncSession,
        composition: ProjectMaintenanceComposition,
    ) -> None:
        self.session = session
        self.composition = composition

    async def start(
        self,
        project_id: UUID,
        *,
        title: str,
        change_request: str,
    ) -> ProjectMaintenanceStarted:
        if not isinstance(project_id, UUID) or project_id.int == 0:
            raise NotFoundError("Project not found.")
        title = _safe_text(title, "Maintenance title", maximum=512, allow_multiline=False)
        change_request = _safe_text(
            change_request,
            "Maintenance change request",
            maximum=4000,
            allow_multiline=True,
        )
        run_id, change_id = uuid4(), uuid4()

        try:
            document_refs = await self._load_document_snapshot(project_id)
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The project maintenance context could not be loaded."
            ) from None
        await self.session.rollback()

        impact_request = MaintenanceImpactRequest(
            project_id=project_id,
            workflow_run_id=run_id,
            change_request_id=change_id,
            change_request=change_request,
            document_refs=document_refs,
        )
        try:
            lore = await self.composition.lore_agent.analyze(impact_request)
            chief = await self.composition.chief_editor_agent.analyze(impact_request)
            affected = _reconcile_affected_items(lore, chief)
            revision_request = RevisionPlanRequest(
                project_id=project_id,
                workflow_run_id=run_id,
                change_request_id=change_id,
                change_request=change_request,
                affected_items=tuple(
                    AffectedItemReference(
                        affected_item_id=item.id,
                        stable_reference=item.item.stable_reference,
                        item_type=item.item.item_type,
                        impact_level=item.item.impact_level,
                        document=item.item.document,
                        reason=item.item.reason,
                    )
                    for item in affected
                ),
                document_refs=document_refs,
            )
            plot = await self.composition.plot_architect_agent.plan(revision_request)
            world = await self.composition.worldbuilding_agent.plan(revision_request)
            plan = _prepare_plan(
                project_id=project_id,
                run_id=run_id,
                change_id=change_id,
                affected=affected,
                lore=lore,
                chief=chief,
                plot=plot,
                world=world,
            )
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The project maintenance plan could not be prepared."
            ) from None

        return await self._persist_start(
            project_id=project_id,
            run_id=run_id,
            change_id=change_id,
            title=title,
            change_request=change_request,
            document_refs=document_refs,
            affected=affected,
            plan=plan,
            lore=lore,
            chief=chief,
        )

    async def load_waiting(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> ProjectMaintenanceStarted:
        try:
            result = await self._load_waiting_locked(project_id, workflow_run_id)
        except ProjectMaintenanceCommitIndeterminateError:
            await self.session.rollback()
            raise
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("Project-maintenance state could not be loaded.") from None
        await self.session.rollback()
        return result

    async def resolve_action(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        decision: MaintenanceDecision,
    ) -> ProjectMaintenanceState:
        """Resolve one exact action, then drive only the resulting durable phase."""

        next_state = await self._persist_action_decision(
            project_id,
            workflow_run_id,
            action_request_id,
            decision=decision,
        )
        if next_state.status is ProjectMaintenanceStatus.APPLY_CHANGE:
            return await self.resume_apply(project_id, workflow_run_id)
        if next_state.status is ProjectMaintenanceStatus.REVISION_PLAN:
            return await self.resume_revision(project_id, workflow_run_id)
        return next_state

    async def resume_apply(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> ProjectMaintenanceState:
        """Resume an already-approved plan without consuming its action twice."""

        archivist = self.composition.archivist_agent
        if archivist is None:
            raise WorkflowStateError("The maintenance apply agent is unavailable.")
        try:
            initial = await self._load_apply_cycle_locked(project_id, workflow_run_id)
            request = initial.request
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("The approved maintenance plan is invalid.") from None
        await self.session.rollback()
        try:
            proposal = await archivist.apply_change(request)
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The approved maintenance change could not be proposed."
            ) from None
        await self._persist_applied_versions(project_id, workflow_run_id, request, proposal)
        return await self.resume_consistency(project_id, workflow_run_id)

    async def resume_consistency(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> ProjectMaintenanceState:
        """Review one known committed apply cycle; never reapply canonical versions."""

        try:
            initial = await self._load_consistency_cycle_locked(project_id, workflow_run_id)
            request = initial.request
        except DocumentCommitIndeterminateError:
            await self.session.rollback()
            raise
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("The applied maintenance state is invalid.") from None
        await self.session.rollback()
        try:
            review = await self.composition.lore_agent.post_change(request)
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("The maintenance consistency review failed.") from None
        next_state = await self._persist_consistency_review(
            project_id, workflow_run_id, request, review
        )
        if next_state.status is ProjectMaintenanceStatus.REVISION_PLAN:
            return await self.resume_revision(project_id, workflow_run_id)
        return next_state

    async def resume_revision(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> ProjectMaintenanceState:
        """Prepare a new plan version for an explicit or consistency-driven revision."""

        try:
            initial = await self._load_revision_cycle_locked(project_id, workflow_run_id)
            request = initial.request
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("The maintenance revision context is invalid.") from None
        await self.session.rollback()
        try:
            plot = await self.composition.plot_architect_agent.plan(request)
            world = await self.composition.worldbuilding_agent.plan(request)
            lore, chief = self._synthetic_impact_outputs(initial.affected)
            plan = _prepare_plan(
                project_id=project_id,
                run_id=workflow_run_id,
                change_id=request.change_request_id,
                affected=initial.affected,
                lore=lore,
                chief=chief,
                plot=plot,
                world=world,
            )
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The maintenance revision plan could not be prepared."
            ) from None
        return await self._persist_revision_cycle(
            project_id,
            workflow_run_id,
            request,
            initial.plan_document_id,
            initial.plan_version_id,
            plan,
        )

    async def _persist_action_decision(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        decision: MaintenanceDecision,
    ) -> ProjectMaintenanceState:
        if not isinstance(decision, MaintenanceDecision):
            raise WorkflowStateError("Maintenance decision is invalid.")
        try:
            locked = await self._locked_context(
                project_id,
                workflow_run_id,
                expected_status=ProjectMaintenanceStatus.USER_CONFIRMATION,
            )
            action = await self.session.scalar(
                select(ActionRequest)
                .where(
                    ActionRequest.id == action_request_id,
                    ActionRequest.workflow_run_id == workflow_run_id,
                    ActionRequest.project_id == project_id,
                    ActionRequest.chapter_id.is_(None),
                )
                .with_for_update()
            )
            if action is None:
                raise NotFoundError("Project-maintenance action not found.")
            foundation = ProjectMaintenanceFoundationService(self.session)
            foundation._validate_action_binding(locked.run, locked.state, action)
            if decision.value not in action.options:
                raise WorkflowStateError("Maintenance decision is not available.")
            if decision is MaintenanceDecision.APPROVE:
                await self._validate_waiting_plan_locked(locked)
            elif decision is MaintenanceDecision.ACCEPT_WARNING:
                await self._load_consistency_cycle_from_locked(project_id, workflow_run_id, locked)
            next_state = locked.state.resolve_confirmation(
                live_action_request_id=str(action.id),
                action_status=ActionRequestStatus(action.status),
                decision=decision,
            )
            metadata = dict(locked.change.metadata_)
            if locked.state.revision_plan_document_id is not None:
                metadata["previous_revision_plan_document_id"] = (
                    locked.state.revision_plan_document_id
                )
                metadata["previous_revision_plan_version_id"] = (
                    locked.state.revision_plan_version_id
                )
            metadata["approval_action_id"] = str(action.id)
            if locked.state.gate_review_outcome is not None:
                metadata["revision_plan_review_outcome"] = locked.state.gate_review_outcome.value
            target_plan_id = (
                None
                if next_state.status is ProjectMaintenanceStatus.REVISION_PLAN
                else locked.change.revision_plan_document_id
            )
            allow_existing_applied = bool(
                locked.state.applied_document_version_ids
                and locked.state.confirmation_kind
                is MaintenanceConfirmationKind.REVISION_CONFIRMATION
                and next_state.status is ProjectMaintenanceStatus.APPLY_CHANGE
            )
            await MaintenanceChangeService(self.session).stage_update_change(
                project_id=project_id,
                change_id=locked.change.id,
                expected_updated_at=locked.change.updated_at,
                expected_status=ProjectMaintenanceStatus.USER_CONFIRMATION,
                target_status=next_state.status,
                revision_plan_document_id=target_plan_id,
                applied_at=locked.change.applied_at,
                metadata=metadata,
                allow_existing_applied=allow_existing_applied,
            )
            action.status = {
                MaintenanceDecision.APPROVE: ActionRequestStatus.APPROVED.value,
                MaintenanceDecision.ACCEPT_WARNING: ActionRequestStatus.FORCE_APPROVED.value,
                MaintenanceDecision.REVISE: ActionRequestStatus.REVISED.value,
                MaintenanceDecision.CANCEL: ActionRequestStatus.CANCELLED.value,
            }[decision]
            action.user_decision = decision.value
            action.resolved_at = datetime.now(UTC)
            foundation._persist_transition(
                locked.run,
                locked.checkpoint_index + 1,
                next_state,
                "project_maintenance_action_resolved",
                action.id,
            )
            if next_state.is_terminal:
                project = await self.session.get(Project, project_id)
                if project is None or project.current_workflow_id != workflow_run_id:
                    raise WorkflowStateError("Project-maintenance workflow is inconsistent.")
                project.current_workflow_id = None
            await self.session.flush()
            await foundation._commit()
            return next_state
        except ProjectMaintenanceCommitIndeterminateError:
            raise
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("The maintenance decision could not be resolved.") from None

    async def _locked_context(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        *,
        expected_status: ProjectMaintenanceStatus,
    ) -> _LockedMaintenanceContext:
        foundation = ProjectMaintenanceFoundationService(self.session)
        run = await foundation._locked_run(project_id, workflow_run_id)
        state, index = await foundation._latest_state(run, for_update=True)
        change = await self.session.scalar(
            select(MaintenanceChange)
            .where(
                MaintenanceChange.project_id == project_id,
                MaintenanceChange.workflow_run_id == workflow_run_id,
            )
            .with_for_update()
        )
        if (
            state.status is not expected_status
            or change is None
            or change.status != state.status.value
        ):
            raise WorkflowStateError("Project-maintenance phase is inconsistent.")
        return _LockedMaintenanceContext(run, state, index, change)

    async def _validate_waiting_plan_locked(self, locked: _LockedMaintenanceContext) -> None:
        metadata = locked.change.metadata_
        try:
            plan_id = UUID(metadata["revision_plan_id"])
            plan_document_id = UUID(locked.state.revision_plan_document_id or "")
            plan_version_id = UUID(locked.state.revision_plan_version_id or "")
            outcome = locked.state.gate_review_outcome
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkflowStateError("Maintenance confirmation lineage is invalid.") from None
        if (
            locked.state.confirmation_kind is not MaintenanceConfirmationKind.REVISION_CONFIRMATION
            or outcome is None
            or locked.change.revision_plan_document_id != plan_document_id
            or metadata.get("revision_plan_version_id") != str(plan_version_id)
        ):
            raise WorkflowStateError("Maintenance confirmation lineage is invalid.")
        plan = await self._load_persisted_plan_locked(
            project_id=locked.change.project_id,
            workflow_run_id=locked.run.id,
            change_id=locked.change.id,
            plan_id=plan_id,
            plan_document_id=plan_document_id,
            plan_version_id=plan_version_id,
            outcome=outcome,
        )
        await self._validate_plan_targets_locked(locked, plan)

    async def _load_apply_cycle_locked(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> _ApplyCycleContext:
        locked = await self._locked_context(
            project_id,
            workflow_run_id,
            expected_status=ProjectMaintenanceStatus.APPLY_CHANGE,
        )
        metadata = locked.change.metadata_
        try:
            approval_id = UUID(metadata["approval_action_id"])
            plan_id = UUID(metadata["revision_plan_id"])
            plan_document_id = UUID(metadata["previous_revision_plan_document_id"])
            plan_version_id = UUID(metadata["previous_revision_plan_version_id"])
            outcome = MaintenanceReviewOutcome(metadata["revision_plan_review_outcome"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkflowStateError("Approved maintenance lineage is invalid.") from None
        if (
            locked.state.revision_plan_document_id != str(plan_document_id)
            or locked.state.revision_plan_version_id != str(plan_version_id)
            or locked.change.revision_plan_document_id != plan_document_id
        ):
            raise WorkflowStateError("Approved maintenance lineage is invalid.")
        approval = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == approval_id,
                ActionRequest.project_id == project_id,
                ActionRequest.workflow_run_id == workflow_run_id,
                ActionRequest.chapter_id.is_(None),
            )
            .with_for_update()
        )
        if (
            approval is None
            or approval.request_type != self._ACTION_TYPE
            or approval.status != ActionRequestStatus.APPROVED.value
            or approval.user_decision != MaintenanceDecision.APPROVE.value
            or approval.resolved_at is None
        ):
            raise WorkflowStateError("Approved maintenance action is invalid.")
        plan = await self._load_persisted_plan_locked(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            change_id=locked.change.id,
            plan_id=plan_id,
            plan_document_id=plan_document_id,
            plan_version_id=plan_version_id,
            outcome=outcome,
        )
        await self._validate_plan_targets_locked(locked, plan)
        request = ApplyChangeRequest(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            change_request_id=locked.change.id,
            approval_id=approval.id,
            revision_plan_id=plan.plan_id,
            revision_plan_document_id=plan_document_id,
            revision_plan_version_id=plan_version_id,
            operations=plan.operations,
        )
        return _ApplyCycleContext(locked, approval, request)

    async def _load_persisted_plan_locked(
        self,
        *,
        project_id: UUID,
        workflow_run_id: UUID,
        change_id: UUID,
        plan_id: UUID,
        plan_document_id: UUID,
        plan_version_id: UUID,
        outcome: MaintenanceReviewOutcome,
    ) -> RevisionPlanOutput:
        document = await self.session.scalar(
            select(Document)
            .where(
                Document.id == plan_document_id,
                Document.project_id == project_id,
                Document.type == DocumentType.MAINTENANCE_PLAN.value,
                Document.chapter_id.is_(None),
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == plan_version_id,
                DocumentVersion.document_id == plan_document_id,
                DocumentVersion.workflow_run_id == workflow_run_id,
            )
            .with_for_update()
        )
        if (
            document is None
            or version is None
            or document.current_version_id != version.id
            or version.source != DocumentSource.SYSTEM.value
            or version.agent_role != "project_maintenance_orchestrator"
        ):
            raise WorkflowStateError("Maintenance plan binding is invalid.")
        try:
            current = await DocumentService(self.session).read_current_content(document.id)
        except Exception:
            raise DocumentCommitIndeterminateError() from None
        if (
            current.version_id != version.id
            or sha256_content(current.content) != version.content_hash
            or len(current.content.encode("utf-8")) != version.byte_size
        ):
            raise WorkflowStateError("Maintenance plan binding is invalid.")
        plan, _ = _decode_persisted_plan(
            current.content,
            project_id=project_id,
            run_id=workflow_run_id,
            change_id=change_id,
            outcome=outcome,
        )
        if plan.plan_id != plan_id:
            raise WorkflowStateError("Maintenance plan binding is invalid.")
        return plan

    async def _validate_plan_targets_locked(
        self,
        locked: _LockedMaintenanceContext,
        plan: RevisionPlanOutput,
    ) -> dict[UUID, Document]:
        target_versions = {
            operation.target.document_id: operation.target.current_version_id
            for operation in plan.operations
        }
        documents = list(
            await self.session.scalars(
                select(Document)
                .where(
                    Document.project_id == locked.run.project_id,
                    Document.id.in_(target_versions),
                    Document.type.not_in(
                        [
                            DocumentType.MAINTENANCE_PLAN.value,
                            DocumentType.MAINTENANCE_REPORT.value,
                        ]
                    ),
                )
                .order_by(Document.id)
                .with_for_update()
            )
        )
        by_id = {document.id: document for document in documents}
        if set(by_id) != set(target_versions) or any(
            by_id[document_id].current_version_id != version_id
            for document_id, version_id in target_versions.items()
        ):
            raise ConflictError("A maintenance target version is stale.")
        affected_items = list(
            await self.session.scalars(
                select(MaintenanceAffectedItem)
                .where(MaintenanceAffectedItem.maintenance_change_id == locked.change.id)
                .order_by(MaintenanceAffectedItem.position)
                .with_for_update()
            )
        )
        if tuple(str(item.id) for item in affected_items) != locked.state.affected_item_ids:
            raise WorkflowStateError("Maintenance affected-item binding is invalid.")
        bindings = {item.id: item.existing_document_id for item in affected_items}
        if any(
            affected_id not in bindings
            or bindings[affected_id] not in {None, operation.target.document_id}
            for operation in plan.operations
            for affected_id in operation.affected_item_ids
        ):
            raise WorkflowStateError("Maintenance target mapping is invalid.")
        return by_id

    async def _persist_applied_versions(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        original_request: ApplyChangeRequest,
        proposal: ApplyChangeOutput,
    ) -> ProjectMaintenanceState:
        document_writes: list[tuple[Document, tuple[tuple[str, str], ...]]] = []
        try:
            current = await self._load_apply_cycle_locked(project_id, workflow_run_id)
            if current.request != original_request:
                raise ConflictError("The approved maintenance plan changed.")
            documents = await self._validate_plan_targets_locked(
                current.locked,
                RevisionPlanOutput(
                    plan_id=current.request.revision_plan_id,
                    summary="Apply approved maintenance revisions.",
                    operations=current.request.operations,
                    safety={
                        "requires_user_confirmation": True,
                        "preserve_existing_versions": True,
                        "direct_write_authority": False,
                    },
                ),
            )
            document_service = DocumentService(self.session)
            applied: list[AppliedDocumentReference] = []
            for edit in proposal.proposed_edits:
                document = documents.get(edit.document_id)
                if document is None:
                    raise WorkflowStateError("A proposed maintenance target is invalid.")
                version, *writes = await document_service.stage_write_document(
                    document_id=edit.document_id,
                    content=edit.content,
                    source=DocumentSource.ARCHIVIST_AGENT,
                    expected_current_version_id=edit.expected_current_version_id,
                    agent_role="archivist_agent",
                    workflow_run_id=workflow_run_id,
                    change_summary="Apply an approved project maintenance revision.",
                )
                if version.parent_version_id != edit.expected_current_version_id:
                    raise WorkflowStateError("An applied maintenance version is invalid.")
                previous = await self.session.get(DocumentVersion, edit.expected_current_version_id)
                if previous is None or previous.document_id != edit.document_id:
                    raise WorkflowStateError("An applied maintenance parent is invalid.")
                if version.content_hash == previous.content_hash:
                    raise WorkflowStateError("A maintenance edit must change its document.")
                version.metadata_ = {
                    "maintenance_change_id": str(current.locked.change.id),
                    "approval_action_id": str(current.approval.id),
                    "revision_plan_id": str(current.request.revision_plan_id),
                    "revision_plan_document_id": str(current.request.revision_plan_document_id),
                    "revision_plan_version_id": str(current.request.revision_plan_version_id),
                    "change_set_id": str(proposal.change_set_id),
                    "revision_operation_id": str(edit.revision_operation_id),
                    "proposed_edit_id": str(edit.proposed_edit_id),
                }
                applied.append(
                    AppliedDocumentReference(
                        proposed_edit_id=edit.proposed_edit_id,
                        document_id=edit.document_id,
                        previous_version_id=edit.expected_current_version_id,
                        current_version_id=version.id,
                    )
                )
                document_writes.append((document, tuple(writes)))
            new_version_ids = tuple(str(item.current_version_id) for item in applied)
            cumulative_ids = (
                *current.locked.state.applied_document_version_ids,
                *new_version_ids,
            )
            if len(set(cumulative_ids)) != len(cumulative_ids):
                raise WorkflowStateError("Applied maintenance history is invalid.")
            next_state = current.locked.state.record_consistency_review(
                applied_document_version_ids=cumulative_ids
            )
            applied_at = current.locked.change.applied_at or datetime.now(UTC)
            metadata = dict(current.locked.change.metadata_)
            metadata.update(
                {
                    "change_set_id": str(proposal.change_set_id),
                    "cycle_applied_version_ids": list(new_version_ids),
                }
            )
            await MaintenanceChangeService(self.session).stage_update_change(
                project_id=project_id,
                change_id=current.locked.change.id,
                expected_updated_at=current.locked.change.updated_at,
                expected_status=ProjectMaintenanceStatus.APPLY_CHANGE,
                target_status=ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
                revision_plan_document_id=current.locked.change.revision_plan_document_id,
                applied_at=applied_at,
                metadata=metadata,
            )
            foundation = ProjectMaintenanceFoundationService(self.session)
            foundation._persist_transition(
                current.locked.run,
                current.locked.checkpoint_index + 1,
                next_state,
                "project_maintenance_documents_applied",
            )
            await self.session.flush()
            await foundation._commit()
        except ProjectMaintenanceCommitIndeterminateError:
            raise
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The approved maintenance change could not be applied."
            ) from None
        materialization_failed = False
        for document, writes in document_writes:
            try:
                DocumentService(self.session).write_staged_files(document, writes)
            except Exception:
                materialization_failed = True
        if materialization_failed:
            raise DocumentCommitIndeterminateError()
        return next_state

    async def _load_consistency_cycle_locked(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> _ConsistencyCycleContext:
        locked = await self._locked_context(
            project_id,
            workflow_run_id,
            expected_status=ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        )
        return await self._load_consistency_cycle_from_locked(project_id, workflow_run_id, locked)

    async def _load_consistency_cycle_from_locked(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        locked: _LockedMaintenanceContext,
    ) -> _ConsistencyCycleContext:
        metadata = locked.change.metadata_
        try:
            approval_id = UUID(metadata["approval_action_id"])
            plan_id = UUID(metadata["revision_plan_id"])
            plan_document_id = UUID(metadata["previous_revision_plan_document_id"])
            plan_version_id = UUID(metadata["previous_revision_plan_version_id"])
            change_set_id = UUID(metadata["change_set_id"])
            cycle_ids = tuple(UUID(item) for item in metadata["cycle_applied_version_ids"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkflowStateError("Applied maintenance lineage is invalid.") from None
        if (
            not cycle_ids
            or tuple(str(item) for item in cycle_ids)
            != locked.state.applied_document_version_ids[-len(cycle_ids) :]
            or locked.state.revision_plan_document_id != str(plan_document_id)
            or locked.state.revision_plan_version_id != str(plan_version_id)
        ):
            raise WorkflowStateError("Applied maintenance lineage is invalid.")
        approval = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == approval_id,
                ActionRequest.project_id == project_id,
                ActionRequest.workflow_run_id == workflow_run_id,
                ActionRequest.status == ActionRequestStatus.APPROVED.value,
                ActionRequest.user_decision == MaintenanceDecision.APPROVE.value,
            )
            .with_for_update()
        )
        versions = list(
            await self.session.scalars(
                select(DocumentVersion).where(DocumentVersion.id.in_(cycle_ids)).with_for_update()
            )
        )
        versions_by_id = {version.id: version for version in versions}
        documents = list(
            await self.session.scalars(
                select(Document)
                .where(
                    Document.project_id == project_id,
                    Document.current_version_id.in_(cycle_ids),
                )
                .with_for_update()
            )
        )
        documents_by_version = {document.current_version_id: document for document in documents}
        if approval is None or set(versions_by_id) != set(cycle_ids):
            raise WorkflowStateError("Applied maintenance lineage is invalid.")
        applied: list[AppliedDocumentReference] = []
        expected_metadata_keys = {
            "maintenance_change_id",
            "approval_action_id",
            "revision_plan_id",
            "revision_plan_document_id",
            "revision_plan_version_id",
            "change_set_id",
            "revision_operation_id",
            "proposed_edit_id",
        }
        for version_id in cycle_ids:
            version = versions_by_id[version_id]
            document = documents_by_version.get(version_id)
            lineage = version.metadata_
            try:
                proposed_edit_id = UUID(lineage["proposed_edit_id"])
            except (KeyError, TypeError, ValueError, AttributeError):
                raise WorkflowStateError("Applied maintenance lineage is invalid.") from None
            if (
                document is None
                or version.document_id != document.id
                or version.parent_version_id is None
                or version.source != DocumentSource.ARCHIVIST_AGENT.value
                or version.agent_role != "archivist_agent"
                or version.workflow_run_id != workflow_run_id
                or set(lineage) != expected_metadata_keys
                or lineage["maintenance_change_id"] != str(locked.change.id)
                or lineage["approval_action_id"] != str(approval_id)
                or lineage["revision_plan_id"] != str(plan_id)
                or lineage["revision_plan_document_id"] != str(plan_document_id)
                or lineage["revision_plan_version_id"] != str(plan_version_id)
                or lineage["change_set_id"] != str(change_set_id)
            ):
                raise WorkflowStateError("Applied maintenance lineage is invalid.")
            try:
                content = await DocumentService(self.session).read_version_content(
                    document.id, version.id
                )
            except Exception:
                raise DocumentCommitIndeterminateError() from None
            if (
                sha256_content(content) != version.content_hash
                or len(content.encode("utf-8")) != version.byte_size
            ):
                raise DocumentCommitIndeterminateError()
            applied.append(
                AppliedDocumentReference(
                    proposed_edit_id=proposed_edit_id,
                    document_id=document.id,
                    previous_version_id=version.parent_version_id,
                    current_version_id=version.id,
                )
            )
        request = PostChangeRequest(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            change_request_id=locked.change.id,
            approval_id=approval_id,
            revision_plan_id=plan_id,
            revision_plan_document_id=plan_document_id,
            revision_plan_version_id=plan_version_id,
            change_set_id=change_set_id,
            applied_changes=tuple(applied),
        )
        return _ConsistencyCycleContext(locked, request)

    async def _persist_consistency_review(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        original_request: PostChangeRequest,
        review: ConsistencyReviewOutput,
    ) -> ProjectMaintenanceState:
        try:
            current = await self._load_consistency_cycle_locked(project_id, workflow_run_id)
            if current.request != original_request:
                raise ConflictError("The applied maintenance state changed.")
            outcome = {
                ConsistencyReviewOutcome.CLEAN: MaintenanceReviewOutcome.PASSED,
                ConsistencyReviewOutcome.WARNING: MaintenanceReviewOutcome.WARNING,
                ConsistencyReviewOutcome.BLOCKING: MaintenanceReviewOutcome.BLOCKING,
            }[review.outcome]
            report = self._consistency_report(
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                output=review,
            )
            self.session.add(report)
            action: ActionRequest | None = None
            if outcome is MaintenanceReviewOutcome.WARNING:
                action = ActionRequest(
                    workflow_run_id=workflow_run_id,
                    project_id=project_id,
                    chapter_id=None,
                    request_type=ProjectMaintenanceFoundationService._CONSISTENCY_ACTION,
                    status=ActionRequestStatus.PENDING.value,
                    prompt="",
                    options=[
                        MaintenanceDecision.ACCEPT_WARNING.value,
                        MaintenanceDecision.REVISE.value,
                    ],
                    default_option=None,
                    metadata_={
                        "confirmation_kind": MaintenanceConfirmationKind.CONSISTENCY_WARNING.value,
                        "review_outcome": MaintenanceReviewOutcome.WARNING.value,
                    },
                )
                self.session.add(action)
                await self.session.flush()
                next_state = current.locked.state.route_consistency_review(
                    review_outcome=outcome,
                    consistency_report_id=str(report.id),
                    action_request_id=str(action.id),
                )
            else:
                next_state = current.locked.state.route_consistency_review(
                    review_outcome=outcome,
                    consistency_report_id=str(report.id),
                )
            metadata = dict(current.locked.change.metadata_)
            metadata.update(
                {
                    "consistency_report_id": str(report.id),
                    "consistency_outcome": review.outcome.value,
                    "provider_review_id": str(review.review_id),
                }
            )
            if next_state.status is ProjectMaintenanceStatus.REVISION_PLAN:
                metadata["previous_revision_plan_document_id"] = (
                    current.locked.state.revision_plan_document_id
                )
                metadata["previous_revision_plan_version_id"] = (
                    current.locked.state.revision_plan_version_id
                )
            target_plan_id = (
                None
                if next_state.status is ProjectMaintenanceStatus.REVISION_PLAN
                else current.locked.change.revision_plan_document_id
            )
            await MaintenanceChangeService(self.session).stage_update_change(
                project_id=project_id,
                change_id=current.locked.change.id,
                expected_updated_at=current.locked.change.updated_at,
                expected_status=ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
                target_status=next_state.status,
                revision_plan_document_id=target_plan_id,
                applied_at=current.locked.change.applied_at,
                metadata=metadata,
            )
            foundation = ProjectMaintenanceFoundationService(self.session)
            foundation._persist_transition(
                current.locked.run,
                current.locked.checkpoint_index + 1,
                next_state,
                "project_maintenance_consistency_reviewed",
                action.id if action is not None else None,
            )
            if next_state.is_terminal:
                project = await self.session.get(Project, project_id)
                if project is None or project.current_workflow_id != workflow_run_id:
                    raise WorkflowStateError("Project-maintenance workflow is inconsistent.")
                project.current_workflow_id = None
            await self.session.flush()
            await foundation._commit()
            return next_state
        except ProjectMaintenanceCommitIndeterminateError:
            raise
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError("The consistency review could not be persisted.") from None

    @staticmethod
    def _consistency_report(
        *,
        project_id: UUID,
        workflow_run_id: UUID,
        output: ConsistencyReviewOutput,
    ) -> ReviewReport:
        blocking = [
            finding.model_dump(mode="json")
            for finding in output.findings
            if finding.severity is ConsistencyFindingSeverity.BLOCKING
        ]
        warnings = [
            finding.model_dump(mode="json")
            for finding in output.findings
            if finding.severity is ConsistencyFindingSeverity.WARNING
        ]
        return ReviewReport(
            id=uuid4(),
            project_id=project_id,
            chapter_id=None,
            workflow_run_id=workflow_run_id,
            review_mode=ReviewMode.MAINTENANCE_CONSISTENCY.value,
            reviewer_agent_role="lore_agent",
            target_document_id=None,
            target_version_id=None,
            passed=output.outcome is not ConsistencyReviewOutcome.BLOCKING,
            summary="Review the applied project maintenance versions for consistency.",
            blocking_issues=blocking,
            warnings=warnings,
            notes=[],
            suggested_actions=[
                {
                    "finding_id": str(finding.finding_id),
                    "suggested_corrective_action": finding.suggested_corrective_action,
                }
                for finding in output.findings
            ],
            raw_report={
                "outcome": output.outcome.value,
                "provider_review_id": str(output.review_id),
                "change_set_id": str(output.change_set_id),
            },
            report_document_id=None,
        )

    async def _load_revision_cycle_locked(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> _RevisionCycleContext:
        locked = await self._locked_context(
            project_id,
            workflow_run_id,
            expected_status=ProjectMaintenanceStatus.REVISION_PLAN,
        )
        metadata = locked.change.metadata_
        try:
            plan_document_id = UUID(metadata["previous_revision_plan_document_id"])
            plan_version_id = UUID(metadata["previous_revision_plan_version_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkflowStateError("Maintenance revision lineage is invalid.") from None
        plan_document = await self.session.scalar(
            select(Document)
            .where(
                Document.id == plan_document_id,
                Document.project_id == project_id,
                Document.type == DocumentType.MAINTENANCE_PLAN.value,
                Document.chapter_id.is_(None),
            )
            .with_for_update()
        )
        plan_version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == plan_version_id,
                DocumentVersion.document_id == plan_document_id,
                DocumentVersion.workflow_run_id == workflow_run_id,
            )
            .with_for_update()
        )
        if (
            plan_document is None
            or plan_version is None
            or plan_document.current_version_id != plan_version.id
            or locked.change.revision_plan_document_id is not None
            or locked.state.revision_plan_document_id is not None
        ):
            raise WorkflowStateError("Maintenance revision lineage is invalid.")
        document_rows = list(
            (
                await self.session.execute(
                    select(Document.id, Document.current_version_id)
                    .where(
                        Document.project_id == project_id,
                        Document.current_version_id.is_not(None),
                        Document.type.not_in(
                            [
                                DocumentType.MAINTENANCE_PLAN.value,
                                DocumentType.MAINTENANCE_REPORT.value,
                            ]
                        ),
                    )
                    .order_by(Document.id)
                    .with_for_update()
                )
            ).all()
        )
        if not document_rows or len(document_rows) > _MAX_DOCUMENTS:
            raise WorkflowStateError("Maintenance revision documents are invalid.")
        refs = tuple(
            DocumentVersionReference(document_id=document_id, current_version_id=version_id)
            for document_id, version_id in document_rows
        )
        refs_by_document = {item.document_id: item for item in refs}
        affected_rows = list(
            await self.session.scalars(
                select(MaintenanceAffectedItem)
                .where(MaintenanceAffectedItem.maintenance_change_id == locked.change.id)
                .order_by(MaintenanceAffectedItem.position)
                .with_for_update()
            )
        )
        if tuple(str(item.id) for item in affected_rows) != locked.state.affected_item_ids:
            raise WorkflowStateError("Maintenance affected-item binding is invalid.")
        affected: list[_ReconciledAffectedItem] = []
        request_items: list[AffectedItemReference] = []
        for row in affected_rows:
            document_ref = (
                refs_by_document.get(row.existing_document_id)
                if row.existing_document_id is not None
                else None
            )
            if row.existing_document_id is not None and document_ref is None:
                raise WorkflowStateError("Maintenance affected document is invalid.")
            item = ImpactAffectedItem(
                stable_reference=row.stable_reference,
                item_type=row.item_type,
                impact_level=row.impact_level,
                document=document_ref,
                reason=row.reason,
            )
            affected.append(_ReconciledAffectedItem(row.id, item))
            request_items.append(
                AffectedItemReference(
                    affected_item_id=row.id,
                    stable_reference=row.stable_reference,
                    item_type=row.item_type,
                    impact_level=row.impact_level,
                    document=document_ref,
                    reason=row.reason,
                )
            )
        request = RevisionPlanRequest(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            change_request_id=locked.change.id,
            change_request=locked.change.original_change_request,
            affected_items=tuple(request_items),
            document_refs=refs,
        )
        return _RevisionCycleContext(
            locked,
            request,
            tuple(affected),
            plan_document_id,
            plan_version_id,
        )

    @staticmethod
    def _synthetic_impact_outputs(
        affected: tuple[_ReconciledAffectedItem, ...],
    ) -> tuple[LoreImpactOutput, ChiefEditorMaintenanceImpactOutput]:
        items = tuple(item.item for item in affected)
        lore = LoreImpactOutput(
            affected_items=items,
            impact_summary="Replan the persisted maintenance impact set.",
            safe_to_change=True,
        )
        chief = ChiefEditorMaintenanceImpactOutput(
            affected_items=items,
            impact_summary="Replan the persisted maintenance impact set.",
            safe_to_change=True,
            reader_expectation_impact="medium",
            commercial_impact="medium",
        )
        return lore, chief

    async def _persist_revision_cycle(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        original_request: RevisionPlanRequest,
        plan_document_id: UUID,
        plan_version_id: UUID,
        plan: _PreparedPlan,
    ) -> ProjectMaintenanceState:
        try:
            current = await self._load_revision_cycle_locked(project_id, workflow_run_id)
            if (
                current.request != original_request
                or current.plan_document_id != plan_document_id
                or current.plan_version_id != plan_version_id
            ):
                raise ConflictError("The maintenance revision context changed.")
            plan_document = await self.session.scalar(
                select(Document)
                .where(
                    Document.id == plan_document_id,
                    Document.project_id == project_id,
                    Document.current_version_id == plan_version_id,
                )
                .with_for_update()
            )
            if plan_document is None:
                raise ConflictError("The maintenance plan version changed.")
            document_service = DocumentService(self.session)
            version, *writes = await document_service.stage_write_document(
                document_id=plan_document_id,
                content=plan.content,
                source=DocumentSource.SYSTEM,
                expected_current_version_id=plan_version_id,
                agent_role="project_maintenance_orchestrator",
                workflow_run_id=workflow_run_id,
                change_summary="Prepare a revised project maintenance plan.",
            )
            action = ActionRequest(
                workflow_run_id=workflow_run_id,
                project_id=project_id,
                chapter_id=None,
                request_type=self._ACTION_TYPE,
                status=ActionRequestStatus.PENDING.value,
                prompt="",
                options=ProjectMaintenanceFoundationService._revision_options(
                    plan.outcome,
                    corrective=bool(current.locked.state.applied_document_version_ids),
                ),
                default_option=None,
                metadata_={
                    "confirmation_kind": MaintenanceConfirmationKind.REVISION_CONFIRMATION.value,
                    "review_outcome": plan.outcome.value,
                },
            )
            self.session.add(action)
            await self.session.flush()
            planned_state = current.locked.state.record_revision_plan(
                revision_plan_document_id=str(plan_document_id),
                revision_plan_version_id=str(version.id),
            )
            waiting_state = planned_state.request_revision_confirmation(
                action_request_id=str(action.id),
                review_outcome=plan.outcome,
            )
            change_service = MaintenanceChangeService(self.session)
            change = await change_service.stage_update_change(
                project_id=project_id,
                change_id=current.locked.change.id,
                expected_updated_at=current.locked.change.updated_at,
                expected_status=ProjectMaintenanceStatus.REVISION_PLAN,
                target_status=ProjectMaintenanceStatus.REVISION_PLAN,
                revision_plan_document_id=plan_document_id,
                applied_at=current.locked.change.applied_at,
                metadata={
                    "revision_plan_id": str(plan.plan_id),
                    "revision_plan_version_id": str(version.id),
                },
            )
            await change_service.stage_update_change(
                project_id=project_id,
                change_id=change.id,
                expected_updated_at=change.updated_at,
                expected_status=ProjectMaintenanceStatus.REVISION_PLAN,
                target_status=ProjectMaintenanceStatus.USER_CONFIRMATION,
                revision_plan_document_id=plan_document_id,
                applied_at=change.applied_at,
                metadata=change.metadata_,
            )
            foundation = ProjectMaintenanceFoundationService(self.session)
            event_time = datetime.now(UTC)
            foundation._persist_transition(
                current.locked.run,
                current.locked.checkpoint_index + 1,
                planned_state,
                "project_maintenance_revision_plan_created",
                created_at=event_time,
            )
            foundation._persist_transition(
                current.locked.run,
                current.locked.checkpoint_index + 2,
                waiting_state,
                "project_maintenance_confirmation_requested",
                action.id,
                created_at=event_time + timedelta(microseconds=1),
            )
            await self.session.flush()
            await foundation._commit()
        except ProjectMaintenanceCommitIndeterminateError:
            raise
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The maintenance revision plan could not be persisted."
            ) from None
        try:
            document_service.write_staged_files(plan_document, tuple(writes))
        except Exception:
            raise DocumentCommitIndeterminateError() from None
        return waiting_state

    async def _load_waiting_locked(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> ProjectMaintenanceStarted:
        foundation = ProjectMaintenanceFoundationService(self.session)
        run = await foundation._locked_run(project_id, workflow_run_id)
        state, _ = await foundation._latest_state(run, for_update=True)
        if (
            state.status is not ProjectMaintenanceStatus.USER_CONFIRMATION
            or state.confirmation_kind is not MaintenanceConfirmationKind.REVISION_CONFIRMATION
            or state.action_request_id is None
            or state.revision_plan_document_id is None
            or state.revision_plan_version_id is None
        ):
            raise WorkflowStateError("Project maintenance is not awaiting revision confirmation.")
        change = await self.session.scalar(
            select(MaintenanceChange)
            .where(
                MaintenanceChange.project_id == project_id,
                MaintenanceChange.workflow_run_id == workflow_run_id,
            )
            .with_for_update()
        )
        if change is None:
            raise WorkflowStateError("Project-maintenance change binding is missing.")
        document = await self.session.scalar(
            select(Document)
            .where(
                Document.id == UUID(state.revision_plan_document_id),
                Document.project_id == project_id,
            )
            .with_for_update()
        )
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == UUID(state.revision_plan_version_id),
                DocumentVersion.document_id == UUID(state.revision_plan_document_id),
            )
            .with_for_update()
        )
        affected_items = list(
            await self.session.scalars(
                select(MaintenanceAffectedItem)
                .where(MaintenanceAffectedItem.maintenance_change_id == change.id)
                .order_by(MaintenanceAffectedItem.position)
                .with_for_update()
            )
        )
        affected_document_ids = {
            item.existing_document_id
            for item in affected_items
            if item.existing_document_id is not None
        }
        owned_affected_document_ids = set(
            await self.session.scalars(
                select(Document.id).where(
                    Document.project_id == project_id,
                    Document.id.in_(affected_document_ids),
                )
            )
        )
        lore_report = await self._bound_impact_report(
            report_id=state.lore_impact_report_id,
            project_id=project_id,
            run_id=workflow_run_id,
            mode="maintenance_lore_impact",
            role="lore_agent",
        )
        chief_report = await self._bound_impact_report(
            report_id=state.chief_impact_report_id,
            project_id=project_id,
            run_id=workflow_run_id,
            mode="maintenance_chief_impact",
            role="chief_editor_agent",
        )
        metadata = change.metadata_
        try:
            revision_plan_id = UUID(metadata["revision_plan_id"])
            metadata_version_id = UUID(metadata["revision_plan_version_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkflowStateError("Project-maintenance plan binding is invalid.") from None
        if (
            set(metadata) != {"revision_plan_id", "revision_plan_version_id"}
            or revision_plan_id.int == 0
            or metadata_version_id != UUID(state.revision_plan_version_id)
            or change.id.int == 0
            or change.status != state.status.value
            or change.revision_plan_document_id != UUID(state.revision_plan_document_id)
            or tuple(str(item.id) for item in affected_items) != state.affected_item_ids
            or any(item.position != index for index, item in enumerate(affected_items))
            or any(item.existing_chapter_id is not None for item in affected_items)
            or owned_affected_document_ids != affected_document_ids
            or lore_report is None
            or chief_report is None
            or document is None
            or document.type != DocumentType.MAINTENANCE_PLAN.value
            or document.chapter_id is not None
            or document.path != f"maintenance/{change.id}/revision_plan.md"
            or document.current_version_id != UUID(state.revision_plan_version_id)
            or version is None
            or version.source != DocumentSource.SYSTEM.value
            or version.agent_role != "project_maintenance_orchestrator"
            or version.workflow_run_id != workflow_run_id
        ):
            raise WorkflowStateError("Project-maintenance plan binding is invalid.")
        try:
            current = await DocumentService(self.session).read_current_content(document.id)
        except Exception:
            raise ProjectMaintenanceCommitIndeterminateError() from None
        if (
            current.version_id != version.id
            or sha256_content(current.content) != version.content_hash
            or len(current.content.encode("utf-8")) != version.byte_size
        ):
            raise WorkflowStateError("Project-maintenance plan binding is invalid.")
        assert state.gate_review_outcome is not None
        persisted_plan, _ = _decode_persisted_plan(
            current.content,
            project_id=project_id,
            run_id=workflow_run_id,
            change_id=change.id,
            outcome=state.gate_review_outcome,
        )
        target_pairs = {
            (item.target.document_id, item.target.current_version_id)
            for item in persisted_plan.operations
        }
        live_target_pairs = set(
            (
                await self.session.execute(
                    select(Document.id, Document.current_version_id)
                    .where(
                        Document.project_id == project_id,
                        Document.id.in_({item[0] for item in target_pairs}),
                    )
                    .with_for_update()
                )
            ).all()
        )
        covered_affected_ids = {
            str(item_id)
            for operation in persisted_plan.operations
            for item_id in operation.affected_item_ids
        }
        affected_document_bindings = {
            str(item.id): item.existing_document_id for item in affected_items
        }
        targets_match_affected_items = all(
            affected_document_bindings.get(str(affected_id)) in {None, operation.target.document_id}
            for operation in persisted_plan.operations
            for affected_id in operation.affected_item_ids
        )
        if (
            persisted_plan.plan_id != revision_plan_id
            or len(target_pairs) != len(persisted_plan.operations)
            or live_target_pairs != target_pairs
            or covered_affected_ids != set(state.affected_item_ids)
            or not targets_match_affected_items
        ):
            raise WorkflowStateError("Project-maintenance plan binding is invalid.")
        return ProjectMaintenanceStarted(
            workflow_run_id=workflow_run_id,
            maintenance_change_id=change.id,
            revision_plan_id=revision_plan_id,
            revision_plan_document_id=document.id,
            revision_plan_version_id=version.id,
            action_request_id=UUID(state.action_request_id),
            state=state,
        )

    async def _bound_impact_report(
        self,
        *,
        report_id: str | None,
        project_id: UUID,
        run_id: UUID,
        mode: str,
        role: str,
    ) -> ReviewReport | None:
        if report_id is None:
            return None
        return await self.session.scalar(
            select(ReviewReport)
            .where(
                ReviewReport.id == UUID(report_id),
                ReviewReport.project_id == project_id,
                ReviewReport.chapter_id.is_(None),
                ReviewReport.workflow_run_id == run_id,
                ReviewReport.review_mode == mode,
                ReviewReport.reviewer_agent_role == role,
            )
            .with_for_update()
        )

    async def _load_document_snapshot(
        self, project_id: UUID
    ) -> tuple[DocumentVersionReference, ...]:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        if project.current_workflow_id is not None:
            raise ConflictError("The project already has an active workflow.")
        documents = list(
            await self.session.scalars(
                select(Document)
                .where(
                    Document.project_id == project_id,
                    Document.current_version_id.is_not(None),
                    Document.type.not_in(
                        [
                            DocumentType.MAINTENANCE_PLAN.value,
                            DocumentType.MAINTENANCE_REPORT.value,
                        ]
                    ),
                )
                .order_by(Document.id)
                .limit(_MAX_DOCUMENTS + 1)
            )
        )
        if not documents:
            raise WorkflowStateError("The project has no current documents to maintain.")
        if len(documents) > _MAX_DOCUMENTS:
            raise WorkflowStateError("The project has too many maintenance context documents.")
        service = DocumentService(self.session)
        references: list[DocumentVersionReference] = []
        for document in documents:
            current = await service.read_current_content(document.id)
            if current.version_id != document.current_version_id:
                raise ConflictError("The project document snapshot changed.")
            references.append(
                DocumentVersionReference(
                    document_id=document.id,
                    current_version_id=current.version_id,
                )
            )
        return tuple(references)

    async def _persist_start(
        self,
        *,
        project_id: UUID,
        run_id: UUID,
        change_id: UUID,
        title: str,
        change_request: str,
        document_refs: tuple[DocumentVersionReference, ...],
        affected: tuple[_ReconciledAffectedItem, ...],
        plan: _PreparedPlan,
        lore: LoreImpactOutput,
        chief: ChiefEditorMaintenanceImpactOutput,
    ) -> ProjectMaintenanceStarted:
        document_service = DocumentService(self.session)
        try:
            await self.session.scalar(
                select(func.pg_advisory_xact_lock(self._advisory_key(project_id)))
            )
            project = await self.session.scalar(
                select(Project).where(Project.id == project_id).with_for_update()
            )
            if project is None:
                raise NotFoundError("Project not found.")
            if project.current_workflow_id is not None:
                raise ConflictError("The project already has an active workflow.")
            active_change = await self.session.scalar(
                select(MaintenanceChange.id).where(
                    MaintenanceChange.project_id == project_id,
                    MaintenanceChange.status.not_in(_TERMINAL_STATUSES),
                )
            )
            if active_change is not None:
                raise ConflictError("The project already has an active maintenance change.")
            await self._revalidate_document_snapshot(project_id, document_refs)

            state0 = ProjectMaintenanceState(
                ProjectMaintenanceStatus.CHANGE_REQUESTED,
                "user_change_request",
                False,
            )
            state1 = state0.transition_to(ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS)
            lore_report_id, chief_report_id = uuid4(), uuid4()
            affected_ids = tuple(str(item.id) for item in affected)
            state2 = state1.record_lore_impact(
                lore_impact_report_id=str(lore_report_id),
                affected_item_ids=affected_ids,
            )
            state3 = state2.record_chief_impact(chief_impact_report_id=str(chief_report_id))
            action_id = uuid4()
            run = WorkflowRun(
                id=run_id,
                project_id=project_id,
                chapter_id=None,
                workflow_type=WorkflowType.PROJECT_MAINTENANCE.value,
                status=ProjectMaintenanceStatus.USER_CONFIRMATION.value,
                current_node="user_confirm_revision",
                next_node=None,
                awaiting_user=True,
                metadata_={},
            )
            self.session.add(run)
            await self.session.flush()
            project.current_workflow_id = run_id
            document, current_write, snapshot_write = await document_service.stage_create_document(
                project_id=project_id,
                document_type=DocumentType.MAINTENANCE_PLAN,
                title="Project maintenance revision plan",
                path=f"maintenance/{change_id}/revision_plan.md",
                content=plan.content,
                source=DocumentSource.SYSTEM,
                chapter_id=None,
                actor_user_id=None,
                agent_role="project_maintenance_orchestrator",
                workflow_run_id=run_id,
                change_summary="Prepare a project maintenance revision plan.",
            )
            if document.current_version_id is None:
                raise WorkflowStateError("Maintenance revision-plan version is missing.")
            change = MaintenanceChange(
                id=change_id,
                project_id=project_id,
                workflow_run_id=run_id,
                title=title,
                original_change_request=change_request,
                status=ProjectMaintenanceStatus.USER_CONFIRMATION.value,
                revision_plan_document_id=document.id,
                applied_at=None,
                metadata_={
                    "revision_plan_id": str(plan.plan_id),
                    "revision_plan_version_id": str(document.current_version_id),
                },
            )
            self.session.add(change)
            self.session.add_all(
                [
                    MaintenanceAffectedItem(
                        id=item.id,
                        maintenance_change_id=change_id,
                        position=position,
                        item_type=item.item.item_type.value,
                        stable_reference=item.item.stable_reference,
                        impact_level=item.item.impact_level.value,
                        reason=item.item.reason,
                        existing_document_id=(
                            item.item.document.document_id
                            if item.item.document is not None
                            else None
                        ),
                        existing_chapter_id=None,
                    )
                    for position, item in enumerate(affected)
                ]
            )
            state3 = state3.record_revision_plan(
                revision_plan_document_id=str(document.id),
                revision_plan_version_id=str(document.current_version_id),
            )
            action = ActionRequest(
                id=action_id,
                workflow_run_id=run_id,
                project_id=project_id,
                chapter_id=None,
                request_type=self._ACTION_TYPE,
                status=ActionRequestStatus.PENDING.value,
                prompt="",
                options=_confirmation_options(plan.outcome),
                default_option=None,
                metadata_={
                    "confirmation_kind": MaintenanceConfirmationKind.REVISION_CONFIRMATION.value,
                    "review_outcome": plan.outcome.value,
                },
            )
            state4 = state3.request_revision_confirmation(
                action_request_id=str(action_id), review_outcome=plan.outcome
            )
            event_time = datetime.now(UTC)
            self.session.add_all(
                [
                    action,
                    self._impact_report(
                        report_id=lore_report_id,
                        project_id=project_id,
                        run_id=run_id,
                        mode="maintenance_lore_impact",
                        agent_role="lore_agent",
                        output=lore,
                    ),
                    self._impact_report(
                        report_id=chief_report_id,
                        project_id=project_id,
                        run_id=run_id,
                        mode="maintenance_chief_impact",
                        agent_role="chief_editor_agent",
                        output=chief,
                    ),
                    _checkpoint(run_id, 0, state0),
                    _event(
                        run_id,
                        "project_maintenance_started",
                        state0,
                        created_at=event_time,
                    ),
                    _checkpoint(run_id, 1, state1),
                    _event(
                        run_id,
                        "project_maintenance_lore_analysis_started",
                        state1,
                        created_at=event_time + timedelta(microseconds=1),
                    ),
                    _checkpoint(run_id, 2, state2),
                    _event(
                        run_id,
                        "project_maintenance_impact_reconciled",
                        state2,
                        created_at=event_time + timedelta(microseconds=2),
                    ),
                    _checkpoint(run_id, 3, state3),
                    _event(
                        run_id,
                        "project_maintenance_revision_plan_created",
                        state3,
                        created_at=event_time + timedelta(microseconds=3),
                    ),
                    _checkpoint(run_id, 4, state4),
                    _event(
                        run_id,
                        "project_maintenance_confirmation_requested",
                        state4,
                        action_id=action_id,
                        created_at=event_time + timedelta(microseconds=4),
                    ),
                ]
            )
            await self.session.flush()
            plan_document_id = document.id
            plan_version_id = document.current_version_id
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                "The project maintenance start conflicts with persisted data."
            ) from None
        except AppError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise WorkflowStateError(
                "The project maintenance start could not be prepared."
            ) from None
        except BaseException:
            await self.session.rollback()
            raise

        try:
            await self.session.commit()
        except BaseException:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise ProjectMaintenanceCommitIndeterminateError() from None

        document_service.write_staged_files(document, (current_write, snapshot_write))
        return ProjectMaintenanceStarted(
            workflow_run_id=run_id,
            maintenance_change_id=change_id,
            revision_plan_id=plan.plan_id,
            revision_plan_document_id=plan_document_id,
            revision_plan_version_id=plan_version_id,
            action_request_id=action_id,
            state=state4,
        )

    async def _revalidate_document_snapshot(
        self,
        project_id: UUID,
        expected: tuple[DocumentVersionReference, ...],
    ) -> None:
        current = tuple(
            DocumentVersionReference(document_id=document_id, current_version_id=version_id)
            for document_id, version_id in (
                await self.session.execute(
                    select(Document.id, Document.current_version_id)
                    .where(
                        Document.project_id == project_id,
                        Document.current_version_id.is_not(None),
                        Document.type.not_in(
                            [
                                DocumentType.MAINTENANCE_PLAN.value,
                                DocumentType.MAINTENANCE_REPORT.value,
                            ]
                        ),
                    )
                    .order_by(Document.id)
                    .with_for_update()
                )
            ).all()
        )
        if current != expected:
            raise ConflictError("The project document snapshot changed.")

    @staticmethod
    def _advisory_key(project_id: UUID) -> int:
        value = int.from_bytes(project_id.bytes[:8], "big", signed=False)
        return value if value < 2**63 else value - 2**64

    @staticmethod
    def _impact_report(
        *,
        report_id: UUID,
        project_id: UUID,
        run_id: UUID,
        mode: str,
        agent_role: str,
        output: LoreImpactOutput | ChiefEditorMaintenanceImpactOutput,
    ) -> ReviewReport:
        blocking = [
            item.model_dump(mode="json")
            for item in output.warnings
            if item.severity is WarningSeverity.BLOCKING
        ]
        warnings = [
            item.model_dump(mode="json")
            for item in output.warnings
            if item.severity is WarningSeverity.ADVISORY
        ]
        return ReviewReport(
            id=report_id,
            project_id=project_id,
            chapter_id=None,
            workflow_run_id=run_id,
            review_mode=mode,
            reviewer_agent_role=agent_role,
            target_document_id=None,
            target_version_id=None,
            passed=output.safe_to_change,
            summary=output.impact_summary,
            blocking_issues=blocking,
            warnings=warnings,
            notes=[],
            suggested_actions=[item.model_dump(mode="json") for item in output.required_rewrites],
            raw_report={},
            report_document_id=None,
        )
