"""Fail-closed, content-free contracts for project-maintenance workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from uuid import UUID

from app.models.enums import ActionRequestStatus


class ProjectMaintenanceStatus(str, Enum):
    CHANGE_REQUESTED = "CHANGE_REQUESTED"
    LORE_IMPACT_ANALYSIS = "LORE_IMPACT_ANALYSIS"
    CHIEF_EDITOR_IMPACT_ANALYSIS = "CHIEF_EDITOR_IMPACT_ANALYSIS"
    REVISION_PLAN = "REVISION_PLAN"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    APPLY_CHANGE = "APPLY_CHANGE"
    CONSISTENCY_REVIEW = "CONSISTENCY_REVIEW"
    PROJECT_UPDATED = "PROJECT_UPDATED"
    CANCELLED = "CANCELLED"


class AffectedItemType(str, Enum):
    CHAPTER = "chapter"
    CHARACTER = "character"
    WORLD = "world"
    OUTLINE = "outline"
    FORESHADOWING = "foreshadowing"
    TIMELINE = "timeline"
    STYLE = "style"


class ImpactLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MaintenanceConfirmationKind(str, Enum):
    REVISION_CONFIRMATION = "revision_confirmation"
    CONSISTENCY_WARNING = "consistency_warning"


class MaintenanceDecision(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    CANCEL = "cancel"
    ACCEPT_WARNING = "accept_warning"


class MaintenanceReviewOutcome(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKING = "blocking"


class ProjectMaintenanceValidationError(ValueError):
    """Raised when workflow mechanics do not match the maintenance contract."""


@dataclass(frozen=True)
class AffectedItem:
    """Strict impact-analysis DTO; its free text never enters a checkpoint."""

    type: AffectedItemType
    ref: str
    impact_level: ImpactLevel
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.type, AffectedItemType):
            raise ProjectMaintenanceValidationError("Affected-item type is not typed.")
        if not isinstance(self.impact_level, ImpactLevel):
            raise ProjectMaintenanceValidationError("Affected-item impact level is not typed.")
        if type(self.ref) is not str or not self.ref.strip():
            raise ProjectMaintenanceValidationError("Affected-item reference is invalid.")
        if type(self.reason) is not str or not self.reason.strip():
            raise ProjectMaintenanceValidationError("Affected-item reason is invalid.")

    def to_checkpoint_reference(self, affected_item_id: str) -> str:
        """Project this content-bearing DTO to its sole durable checkpoint identifier."""

        reference = _canonical_uuid(affected_item_id, "Affected-item reference")
        assert reference is not None
        return reference


_NODES_BY_STATUS = {
    ProjectMaintenanceStatus.CHANGE_REQUESTED: "user_change_request",
    ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS: "lore_impact_analysis",
    ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS: "chief_editor_impact_review",
    ProjectMaintenanceStatus.REVISION_PLAN: "revision_plan",
    ProjectMaintenanceStatus.USER_CONFIRMATION: "user_confirm_revision",
    ProjectMaintenanceStatus.APPLY_CHANGE: "apply_revision",
    ProjectMaintenanceStatus.CONSISTENCY_REVIEW: "consistency_review",
    ProjectMaintenanceStatus.PROJECT_UPDATED: "project_updated",
    ProjectMaintenanceStatus.CANCELLED: "cancelled",
}

_DIRECT_TRANSITIONS = {
    (ProjectMaintenanceStatus.CHANGE_REQUESTED, ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS),
}

_TERMINAL_STATUSES = {
    ProjectMaintenanceStatus.PROJECT_UPDATED,
    ProjectMaintenanceStatus.CANCELLED,
}

_CHECKPOINT_KEYS = {
    "version",
    "status",
    "current_node",
    "awaiting_user",
    "action_request_id",
    "confirmation_kind",
    "gate_review_outcome",
    "affected_item_ids",
    "lore_impact_report_id",
    "chief_impact_report_id",
    "revision_plan_document_id",
    "revision_plan_version_id",
    "proposed_document_version_ids",
    "applied_document_version_ids",
    "consistency_report_id",
}


def _canonical_uuid(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise ProjectMaintenanceValidationError(f"{field} is invalid.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ProjectMaintenanceValidationError(f"{field} is invalid.") from error
    if str(parsed) != value:
        raise ProjectMaintenanceValidationError(f"{field} is not canonical.")
    if parsed.int == 0:
        raise ProjectMaintenanceValidationError(f"{field} cannot be nil.")
    return value


def _validate_uuid_tuple(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ProjectMaintenanceValidationError(f"{field} is not an immutable tuple.")
    validated = tuple(_canonical_uuid(item, field) for item in value)
    if len(set(validated)) != len(validated):
        raise ProjectMaintenanceValidationError(f"{field} contains duplicate references.")
    return validated  # type: ignore[return-value]


def _uuid_list_from_checkpoint(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise ProjectMaintenanceValidationError(f"Checkpoint {field} is invalid.")
    return _validate_uuid_tuple(tuple(value), field)


@dataclass(frozen=True)
class ProjectMaintenanceState:
    """Durable mechanics and stable references, intentionally without novel content."""

    status: ProjectMaintenanceStatus
    current_node: str
    awaiting_user: bool
    action_request_id: str | None = None
    confirmation_kind: MaintenanceConfirmationKind | None = None
    gate_review_outcome: MaintenanceReviewOutcome | None = None
    affected_item_ids: tuple[str, ...] = ()
    lore_impact_report_id: str | None = None
    chief_impact_report_id: str | None = None
    revision_plan_document_id: str | None = None
    revision_plan_version_id: str | None = None
    proposed_document_version_ids: tuple[str, ...] = ()
    applied_document_version_ids: tuple[str, ...] = ()
    consistency_report_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProjectMaintenanceStatus):
            raise ProjectMaintenanceValidationError("Checkpoint status is not typed.")
        if type(self.current_node) is not str or self.current_node != _NODES_BY_STATUS[self.status]:
            raise ProjectMaintenanceValidationError("Checkpoint node does not match its status.")
        if type(self.awaiting_user) is not bool:
            raise ProjectMaintenanceValidationError("Checkpoint waiting flag is not typed.")

        is_confirmation = self.status is ProjectMaintenanceStatus.USER_CONFIRMATION
        if self.awaiting_user is not is_confirmation:
            raise ProjectMaintenanceValidationError(
                "Checkpoint waiting flag does not match its status."
            )
        if (self.action_request_id is not None) is not is_confirmation:
            raise ProjectMaintenanceValidationError(
                "Checkpoint action reference does not match its status."
            )
        if (self.confirmation_kind is not None) is not is_confirmation or (
            self.gate_review_outcome is not None
        ) is not is_confirmation:
            raise ProjectMaintenanceValidationError(
                "Checkpoint confirmation binding does not match its status."
            )
        if self.confirmation_kind is not None and not isinstance(
            self.confirmation_kind, MaintenanceConfirmationKind
        ):
            raise ProjectMaintenanceValidationError("Checkpoint confirmation kind is not typed.")
        if self.gate_review_outcome is not None and not isinstance(
            self.gate_review_outcome, MaintenanceReviewOutcome
        ):
            raise ProjectMaintenanceValidationError("Checkpoint gate outcome is not typed.")

        _canonical_uuid(self.action_request_id, "Checkpoint action reference", optional=True)
        _validate_uuid_tuple(self.affected_item_ids, "Affected-item references")
        _canonical_uuid(self.lore_impact_report_id, "Lore report reference", optional=True)
        _canonical_uuid(self.chief_impact_report_id, "Chief report reference", optional=True)
        _canonical_uuid(self.revision_plan_document_id, "Revision-plan reference", optional=True)
        _canonical_uuid(
            self.revision_plan_version_id, "Revision-plan version reference", optional=True
        )
        _validate_uuid_tuple(self.proposed_document_version_ids, "Proposed-version references")
        _validate_uuid_tuple(self.applied_document_version_ids, "Applied-version references")
        _canonical_uuid(self.consistency_report_id, "Consistency report reference", optional=True)
        self._validate_phase_references()

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def transition_to(
        self,
        target: ProjectMaintenanceStatus,
        *,
        action_request_id: str | None = None,
    ) -> ProjectMaintenanceState:
        """Advance through a declared non-decision edge only."""

        if not isinstance(target, ProjectMaintenanceStatus):
            raise ProjectMaintenanceValidationError("Transition target is not typed.")
        if (self.status, target) not in _DIRECT_TRANSITIONS:
            raise ProjectMaintenanceValidationError("Project-maintenance transition is not allowed.")

        if action_request_id is not None:
            raise ProjectMaintenanceValidationError(
                "Generic transitions do not accept an action reference."
            )

        return replace(
            self,
            status=target,
            current_node=_NODES_BY_STATUS[target],
            awaiting_user=False,
            action_request_id=None,
            confirmation_kind=None,
            gate_review_outcome=None,
        )

    def record_revision_plan(
        self,
        *,
        revision_plan_document_id: str,
        revision_plan_version_id: str,
        proposed_document_version_ids: tuple[str, ...] = (),
    ) -> ProjectMaintenanceState:
        if (
            self.status is not ProjectMaintenanceStatus.REVISION_PLAN
            or self.chief_impact_report_id is None
        ):
            raise ProjectMaintenanceValidationError("Revision plan cannot be recorded now.")
        return replace(
            self,
            status=ProjectMaintenanceStatus.REVISION_PLAN,
            current_node=_NODES_BY_STATUS[ProjectMaintenanceStatus.REVISION_PLAN],
            revision_plan_document_id=revision_plan_document_id,
            revision_plan_version_id=revision_plan_version_id,
            proposed_document_version_ids=proposed_document_version_ids,
        )

    def record_lore_impact(
        self,
        *,
        lore_impact_report_id: str,
        affected_item_ids: tuple[str, ...] = (),
    ) -> ProjectMaintenanceState:
        if self.status is not ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS:
            raise ProjectMaintenanceValidationError("Lore impact cannot be recorded now.")
        return replace(
            self,
            status=ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
            current_node=_NODES_BY_STATUS[ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS],
            affected_item_ids=affected_item_ids,
            lore_impact_report_id=lore_impact_report_id,
        )

    def record_chief_impact(
        self, *, chief_impact_report_id: str
    ) -> ProjectMaintenanceState:
        if self.status is not ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS:
            raise ProjectMaintenanceValidationError("Chief-editor impact cannot be recorded now.")
        return replace(
            self,
            status=ProjectMaintenanceStatus.REVISION_PLAN,
            current_node=_NODES_BY_STATUS[ProjectMaintenanceStatus.REVISION_PLAN],
            chief_impact_report_id=chief_impact_report_id,
        )

    def request_revision_confirmation(
        self,
        *,
        action_request_id: str,
        review_outcome: MaintenanceReviewOutcome,
    ) -> ProjectMaintenanceState:
        if self.status is not ProjectMaintenanceStatus.REVISION_PLAN or not isinstance(
            review_outcome, MaintenanceReviewOutcome
        ):
            raise ProjectMaintenanceValidationError("Revision confirmation cannot be requested.")
        return replace(
            self,
            status=ProjectMaintenanceStatus.USER_CONFIRMATION,
            current_node=_NODES_BY_STATUS[ProjectMaintenanceStatus.USER_CONFIRMATION],
            awaiting_user=True,
            action_request_id=action_request_id,
            confirmation_kind=MaintenanceConfirmationKind.REVISION_CONFIRMATION,
            gate_review_outcome=review_outcome,
        )

    def record_consistency_review(
        self,
        *,
        applied_document_version_ids: tuple[str, ...],
    ) -> ProjectMaintenanceState:
        if self.status is not ProjectMaintenanceStatus.APPLY_CHANGE:
            raise ProjectMaintenanceValidationError("Consistency review cannot be recorded now.")
        return replace(
            self,
            status=ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            current_node=_NODES_BY_STATUS[ProjectMaintenanceStatus.CONSISTENCY_REVIEW],
            applied_document_version_ids=applied_document_version_ids,
            consistency_report_id=None,
        )

    def route_consistency_review(
        self,
        *,
        review_outcome: MaintenanceReviewOutcome,
        consistency_report_id: str,
        action_request_id: str | None = None,
    ) -> ProjectMaintenanceState:
        if self.status is not ProjectMaintenanceStatus.CONSISTENCY_REVIEW or not isinstance(
            review_outcome, MaintenanceReviewOutcome
        ):
            raise ProjectMaintenanceValidationError("Consistency review cannot be routed now.")
        if review_outcome is MaintenanceReviewOutcome.WARNING:
            if action_request_id is None:
                raise ProjectMaintenanceValidationError("Consistency warning requires an action.")
            return replace(
                self,
                status=ProjectMaintenanceStatus.USER_CONFIRMATION,
                current_node=_NODES_BY_STATUS[ProjectMaintenanceStatus.USER_CONFIRMATION],
                awaiting_user=True,
                action_request_id=action_request_id,
                confirmation_kind=MaintenanceConfirmationKind.CONSISTENCY_WARNING,
                gate_review_outcome=review_outcome,
                consistency_report_id=consistency_report_id,
            )
        if action_request_id is not None:
            raise ProjectMaintenanceValidationError("This consistency outcome cannot create an action.")
        target = (
            ProjectMaintenanceStatus.REVISION_PLAN
            if review_outcome is MaintenanceReviewOutcome.BLOCKING
            else ProjectMaintenanceStatus.PROJECT_UPDATED
        )
        return replace(
            self,
            status=target,
            current_node=_NODES_BY_STATUS[target],
            revision_plan_document_id=(
                None if target is ProjectMaintenanceStatus.REVISION_PLAN else self.revision_plan_document_id
            ),
            revision_plan_version_id=(
                None if target is ProjectMaintenanceStatus.REVISION_PLAN else self.revision_plan_version_id
            ),
            proposed_document_version_ids=(
                () if target is ProjectMaintenanceStatus.REVISION_PLAN else self.proposed_document_version_ids
            ),
            consistency_report_id=consistency_report_id,
        )

    def resolve_confirmation(
        self,
        *,
        live_action_request_id: str,
        action_status: ActionRequestStatus,
        decision: MaintenanceDecision,
    ) -> ProjectMaintenanceState:
        """Resolve the exact live action once, with blocking outcomes enforced."""

        if self.status is not ProjectMaintenanceStatus.USER_CONFIRMATION:
            raise ProjectMaintenanceValidationError("Workflow is not awaiting confirmation.")
        live_id = _canonical_uuid(live_action_request_id, "Live action reference")
        if live_id != self.action_request_id:
            raise ProjectMaintenanceValidationError("Confirmation action is stale.")
        if not isinstance(action_status, ActionRequestStatus):
            raise ProjectMaintenanceValidationError("Action status is not typed.")
        if action_status is not ActionRequestStatus.PENDING:
            raise ProjectMaintenanceValidationError("Confirmation action was already resolved.")
        if not isinstance(decision, MaintenanceDecision):
            raise ProjectMaintenanceValidationError("Confirmation decision is not typed.")
        review_outcome = self.gate_review_outcome
        if review_outcome is None:
            raise ProjectMaintenanceValidationError("Confirmation outcome binding is missing.")

        target: ProjectMaintenanceStatus
        if self.confirmation_kind is MaintenanceConfirmationKind.REVISION_CONFIRMATION:
            if decision is MaintenanceDecision.REVISE:
                target = ProjectMaintenanceStatus.REVISION_PLAN
            elif decision is MaintenanceDecision.CANCEL:
                if self.applied_document_version_ids:
                    raise ProjectMaintenanceValidationError(
                        "A corrective revision cannot be cancelled without rollback."
                    )
                target = ProjectMaintenanceStatus.CANCELLED
            elif decision is MaintenanceDecision.APPROVE:
                if review_outcome is MaintenanceReviewOutcome.BLOCKING:
                    raise ProjectMaintenanceValidationError(
                        "A blocking outcome cannot be approved."
                    )
                target = ProjectMaintenanceStatus.APPLY_CHANGE
            else:
                raise ProjectMaintenanceValidationError(
                    "Decision is not valid for revision confirmation."
                )
        elif self.confirmation_kind is MaintenanceConfirmationKind.CONSISTENCY_WARNING:
            if review_outcome is not MaintenanceReviewOutcome.WARNING:
                raise ProjectMaintenanceValidationError(
                    "Consistency warning action does not match its review outcome."
                )
            if decision is MaintenanceDecision.ACCEPT_WARNING:
                target = ProjectMaintenanceStatus.PROJECT_UPDATED
            elif decision is MaintenanceDecision.REVISE:
                target = ProjectMaintenanceStatus.REVISION_PLAN
            else:
                raise ProjectMaintenanceValidationError(
                    "Decision is not valid for a consistency warning."
                )
        else:
            raise ProjectMaintenanceValidationError("Confirmation kind is invalid.")

        return replace(
            self,
            status=target,
            current_node=_NODES_BY_STATUS[target],
            awaiting_user=False,
            action_request_id=None,
            confirmation_kind=None,
            gate_review_outcome=None,
            revision_plan_document_id=(
                None if target is ProjectMaintenanceStatus.REVISION_PLAN else self.revision_plan_document_id
            ),
            revision_plan_version_id=(
                None if target is ProjectMaintenanceStatus.REVISION_PLAN else self.revision_plan_version_id
            ),
            proposed_document_version_ids=(
                () if target is ProjectMaintenanceStatus.REVISION_PLAN else self.proposed_document_version_ids
            ),
            consistency_report_id=(
                None if target is ProjectMaintenanceStatus.APPLY_CHANGE else self.consistency_report_id
            ),
        )

    def to_checkpoint(self) -> dict[str, object]:
        return {
            "version": 1,
            "status": self.status.value,
            "current_node": self.current_node,
            "awaiting_user": self.awaiting_user,
            "action_request_id": self.action_request_id,
            "confirmation_kind": (
                self.confirmation_kind.value if self.confirmation_kind is not None else None
            ),
            "gate_review_outcome": (
                self.gate_review_outcome.value if self.gate_review_outcome is not None else None
            ),
            "affected_item_ids": list(self.affected_item_ids),
            "lore_impact_report_id": self.lore_impact_report_id,
            "chief_impact_report_id": self.chief_impact_report_id,
            "revision_plan_document_id": self.revision_plan_document_id,
            "revision_plan_version_id": self.revision_plan_version_id,
            "proposed_document_version_ids": list(self.proposed_document_version_ids),
            "applied_document_version_ids": list(self.applied_document_version_ids),
            "consistency_report_id": self.consistency_report_id,
        }

    @classmethod
    def from_checkpoint(cls, payload: object) -> ProjectMaintenanceState:
        if type(payload) is not dict or set(payload) != _CHECKPOINT_KEYS:
            raise ProjectMaintenanceValidationError("Checkpoint payload has an unrecognized shape.")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ProjectMaintenanceValidationError("Checkpoint payload has an invalid version.")
        if type(payload["status"]) is not str:
            raise ProjectMaintenanceValidationError("Checkpoint status is invalid.")
        try:
            status = ProjectMaintenanceStatus(payload["status"])
        except ValueError as error:
            raise ProjectMaintenanceValidationError("Checkpoint status is unrecognized.") from error
        if type(payload["current_node"]) is not str or type(payload["awaiting_user"]) is not bool:
            raise ProjectMaintenanceValidationError("Checkpoint workflow fields are invalid.")
        action_request_id = _canonical_uuid(
            payload["action_request_id"], "Checkpoint action reference", optional=True
        )
        raw_kind = payload["confirmation_kind"]
        if raw_kind is None:
            confirmation_kind = None
        elif type(raw_kind) is str:
            try:
                confirmation_kind = MaintenanceConfirmationKind(raw_kind)
            except ValueError as error:
                raise ProjectMaintenanceValidationError(
                    "Checkpoint confirmation kind is unrecognized."
                ) from error
        else:
            raise ProjectMaintenanceValidationError("Checkpoint confirmation kind is invalid.")
        raw_outcome = payload["gate_review_outcome"]
        if raw_outcome is None:
            gate_review_outcome = None
        elif type(raw_outcome) is str:
            try:
                gate_review_outcome = MaintenanceReviewOutcome(raw_outcome)
            except ValueError as error:
                raise ProjectMaintenanceValidationError(
                    "Checkpoint gate outcome is unrecognized."
                ) from error
        else:
            raise ProjectMaintenanceValidationError("Checkpoint gate outcome is invalid.")

        return cls(
            status=status,
            current_node=payload["current_node"],
            awaiting_user=payload["awaiting_user"],
            action_request_id=action_request_id,
            confirmation_kind=confirmation_kind,
            gate_review_outcome=gate_review_outcome,
            affected_item_ids=_uuid_list_from_checkpoint(
                payload["affected_item_ids"], "affected-item references"
            ),
            lore_impact_report_id=_canonical_uuid(
                payload["lore_impact_report_id"], "Lore report reference", optional=True
            ),
            chief_impact_report_id=_canonical_uuid(
                payload["chief_impact_report_id"], "Chief report reference", optional=True
            ),
            revision_plan_document_id=_canonical_uuid(
                payload["revision_plan_document_id"],
                "Revision-plan reference",
                optional=True,
            ),
            revision_plan_version_id=_canonical_uuid(
                payload["revision_plan_version_id"],
                "Revision-plan version reference",
                optional=True,
            ),
            proposed_document_version_ids=_uuid_list_from_checkpoint(
                payload["proposed_document_version_ids"], "proposed-version references"
            ),
            applied_document_version_ids=_uuid_list_from_checkpoint(
                payload["applied_document_version_ids"], "applied-version references"
            ),
            consistency_report_id=_canonical_uuid(
                payload["consistency_report_id"],
                "Consistency report reference",
                optional=True,
            ),
        )

    def to_public_event_payload(
        self, *, action_request_id: str | None = None
    ) -> dict[str, str | bool]:
        """Project a fixed operational allowlist; never accept arbitrary payload data."""

        event_action_id = action_request_id or self.action_request_id
        if event_action_id is not None:
            _canonical_uuid(event_action_id, "Public event action reference")
        payload: dict[str, str | bool] = {
            "status": self.status.value,
            "current_node": self.current_node,
            "awaiting_user": self.awaiting_user,
        }
        if event_action_id is not None:
            payload["action_request_id"] = event_action_id
        if self.confirmation_kind is not None:
            payload["confirmation_kind"] = self.confirmation_kind.value
        return payload

    def _validate_phase_references(self) -> None:
        """Validate an executing-node snapshot, not an all-at-once phase result.

        A node may carry output it has already produced locally (for example an
        APPLY_CHANGE snapshot may contain applied versions). References owned by
        a future node remain forbidden until that node is entered.
        """
        plan_is_complete = (
            self.revision_plan_document_id is not None
            and self.revision_plan_version_id is not None
        )
        if (self.revision_plan_document_id is None) is not (
            self.revision_plan_version_id is None
        ):
            raise ProjectMaintenanceValidationError("Revision-plan references are incomplete.")
        if self.proposed_document_version_ids and not plan_is_complete:
            raise ProjectMaintenanceValidationError(
                "Proposed versions require a complete revision-plan reference."
            )
        if set(self.proposed_document_version_ids) & set(self.applied_document_version_ids):
            raise ProjectMaintenanceValidationError(
                "Proposed and applied version references cannot overlap."
            )
        if self.consistency_report_id is not None and not self.applied_document_version_ids:
            raise ProjectMaintenanceValidationError(
                "Consistency report requires applied-version references."
            )
        if (
            self.applied_document_version_ids
            and self.consistency_report_id is None
            and (
                self.status is ProjectMaintenanceStatus.REVISION_PLAN
                or (
                    self.status is ProjectMaintenanceStatus.USER_CONFIRMATION
                    and self.confirmation_kind
                    is MaintenanceConfirmationKind.REVISION_CONFIRMATION
                )
            )
        ):
            raise ProjectMaintenanceValidationError(
                "Corrective revision requires an originating consistency report."
            )
        if (
            self.status is ProjectMaintenanceStatus.APPLY_CHANGE
            and self.consistency_report_id is not None
        ):
            raise ProjectMaintenanceValidationError(
                "Apply-change state cannot retain a consistency report."
            )
        if self.status is ProjectMaintenanceStatus.CANCELLED and (
            self.applied_document_version_ids or self.consistency_report_id is not None
        ):
            raise ProjectMaintenanceValidationError(
                "Cancelled state cannot retain applied or consistency references."
            )
        if self.status is ProjectMaintenanceStatus.CHANGE_REQUESTED and any(
            (
                self.affected_item_ids,
                self.lore_impact_report_id,
                self.chief_impact_report_id,
                self.revision_plan_document_id,
                self.proposed_document_version_ids,
                self.applied_document_version_ids,
                self.consistency_report_id,
            )
        ):
            raise ProjectMaintenanceValidationError("Change-request state contains future references.")
        if self.status in {
            ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
            ProjectMaintenanceStatus.REVISION_PLAN,
            ProjectMaintenanceStatus.USER_CONFIRMATION,
            ProjectMaintenanceStatus.APPLY_CHANGE,
            ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            ProjectMaintenanceStatus.PROJECT_UPDATED,
            ProjectMaintenanceStatus.CANCELLED,
        } and self.lore_impact_report_id is None:
            raise ProjectMaintenanceValidationError("Workflow phase requires a lore report.")
        if self.status in {
            ProjectMaintenanceStatus.REVISION_PLAN,
            ProjectMaintenanceStatus.USER_CONFIRMATION,
            ProjectMaintenanceStatus.APPLY_CHANGE,
            ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            ProjectMaintenanceStatus.PROJECT_UPDATED,
            ProjectMaintenanceStatus.CANCELLED,
        } and self.chief_impact_report_id is None:
            raise ProjectMaintenanceValidationError("Workflow phase requires a chief-editor report.")
        if self.status in {
            ProjectMaintenanceStatus.USER_CONFIRMATION,
            ProjectMaintenanceStatus.APPLY_CHANGE,
            ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            ProjectMaintenanceStatus.PROJECT_UPDATED,
            ProjectMaintenanceStatus.CANCELLED,
        } and not plan_is_complete:
            raise ProjectMaintenanceValidationError("Workflow phase requires a revision plan.")
        if self.status in {
            ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            ProjectMaintenanceStatus.PROJECT_UPDATED,
        } and not self.applied_document_version_ids:
            raise ProjectMaintenanceValidationError(
                "Workflow phase requires applied versions."
            )
        if (
            self.status is ProjectMaintenanceStatus.PROJECT_UPDATED
            and self.consistency_report_id is None
        ):
            raise ProjectMaintenanceValidationError("Updated project requires consistency report.")
        if (
            self.status is ProjectMaintenanceStatus.USER_CONFIRMATION
            and self.confirmation_kind is MaintenanceConfirmationKind.CONSISTENCY_WARNING
            and (
                self.gate_review_outcome is not MaintenanceReviewOutcome.WARNING
                or not self.applied_document_version_ids
                or self.consistency_report_id is None
            )
        ):
            raise ProjectMaintenanceValidationError(
                "Consistency warning is missing applied references."
            )
        if self.status in {
            ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
            ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
        } and any(
            (
                self.revision_plan_document_id,
                self.proposed_document_version_ids,
                self.applied_document_version_ids,
                self.consistency_report_id,
            )
        ):
            raise ProjectMaintenanceValidationError("Workflow phase contains future references.")
        if (
            self.status is ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS
            and self.chief_impact_report_id is not None
        ):
            raise ProjectMaintenanceValidationError("Lore-impact state contains a future report.")
