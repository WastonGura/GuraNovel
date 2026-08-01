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
    ChiefEditorAgent,
    LoreAgent,
    PlotArchitectAgent,
    WorldbuildingAgent,
)
from app.agents.maintenance_contracts import (
    AffectedItemReference,
    ChiefEditorMaintenanceImpactOutput,
    DocumentVersionReference,
    ImpactAffectedItem,
    LoreImpactOutput,
    MaintenanceImpactRequest,
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
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.services.document_service import DocumentService
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
