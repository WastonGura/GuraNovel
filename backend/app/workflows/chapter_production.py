"""Fail-closed, content-free Chapter Production V2 state contracts."""

from __future__ import annotations

import re
from dataclasses import InitVar, dataclass, replace
from enum import Enum
from uuid import UUID

from app.models.enums import ActionRequestStatus, WorkflowType


class ChapterProductionStatus(str, Enum):
    DRAFTING = "DRAFTING"
    AUTHOR_REVISION = "AUTHOR_REVISION"
    EDITOR_REVIEW = "EDITOR_REVIEW"
    REVIEW_REVISION = "REVIEW_REVISION"
    CHIEF_FINAL_REVIEW = "CHIEF_FINAL_REVIEW"
    LORE_FINAL_REVIEW = "LORE_FINAL_REVIEW"
    REVISION_READY = "REVISION_READY"
    ARCHIVE_UPDATE = "ARCHIVE_UPDATE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ChapterReviewStage(str, Enum):
    EDITOR = "editor"
    CHIEF_EDITOR = "chief_editor"
    LORE = "lore"


class ChapterReviewOutcome(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKING = "blocking"


class ChapterActionKind(str, Enum):
    AUTHOR_REVISION = "author_revision"
    REVIEW_WARNING = "review_warning"
    REVIEW_REVISION = "review_revision"


class ChapterActionDecision(str, Enum):
    ACCEPT = "accept"
    REQUEST_REVISION = "request_revision"
    SUBMIT_MANUAL_EDIT = "submit_manual_edit"
    ACCEPT_WARNING = "accept_warning"
    CANCEL = "cancel"


class ChapterFailureCode(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_PROVIDER_OUTPUT = "invalid_provider_output"
    DOCUMENT_COMMIT_INDETERMINATE = "document_commit_indeterminate"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    ARCHIVE_UNAVAILABLE = "archive_unavailable"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ChapterFailureReconciliationOutcome(str, Enum):
    NO_WRITE_OR_PERSISTENCE_RESTORED = "no_write_or_persistence_restored"
    CANONICAL_VERSION_COMMITTED = "canonical_version_committed"


class ChapterProductionValidationError(ValueError):
    """Raised when state, a transition, or an untrusted checkpoint is invalid."""


_NODES_BY_STATUS = {
    ChapterProductionStatus.DRAFTING: "drafting",
    ChapterProductionStatus.AUTHOR_REVISION: "author_revision",
    ChapterProductionStatus.EDITOR_REVIEW: "editor_review",
    ChapterProductionStatus.REVIEW_REVISION: "review_revision",
    ChapterProductionStatus.CHIEF_FINAL_REVIEW: "chief_final_review",
    ChapterProductionStatus.LORE_FINAL_REVIEW: "lore_final_review",
    # This is the sole authoritative integration discriminator. The run status,
    # run node, and checkpoint node_name must all use this exact constant.
    ChapterProductionStatus.REVISION_READY: "REVISION_READY",
    ChapterProductionStatus.ARCHIVE_UPDATE: "archive_update",
    ChapterProductionStatus.COMPLETED: "completed",
    ChapterProductionStatus.CANCELLED: "cancelled",
    ChapterProductionStatus.FAILED: "failed",
}

_TERMINAL_STATUSES = {
    ChapterProductionStatus.COMPLETED,
    ChapterProductionStatus.CANCELLED,
}

_FINALIZED_STATUSES = {
    ChapterProductionStatus.REVISION_READY,
    ChapterProductionStatus.ARCHIVE_UPDATE,
    ChapterProductionStatus.COMPLETED,
}

_CHECKPOINT_KEYS = {
    "version",
    "chapter_workflow_run_id",
    "chapter_id",
    "status",
    "current_node",
    "awaiting_user",
    "review_policy_version",
    "chief_editor_required",
    "document_id",
    "document_version_id",
    "content_hash",
    "editor_report_id",
    "chief_editor_report_id",
    "lore_report_id",
    "action_request_id",
    "action_kind",
    "failed_from_status",
    "failure_code",
}

_POLICY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_REVIEW_AUTHORITY = {
    ChapterReviewStage.EDITOR: ("chapter_editor", "editor_agent"),
    ChapterReviewStage.CHIEF_EDITOR: ("chapter_chief_final", "chief_editor_agent"),
    ChapterReviewStage.LORE: ("chapter_final_lore", "lore_agent"),
}
_ACTION_REQUEST_TYPES = {
    ChapterActionKind.AUTHOR_REVISION: "chapter_author_revision",
    ChapterActionKind.REVIEW_WARNING: "chapter_review_warning",
    ChapterActionKind.REVIEW_REVISION: "chapter_review_revision",
}
_RECONCILIATION_FAILURE_CODES = {
    ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
    ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
    ChapterFailureCode.RECONCILIATION_REQUIRED,
}
_READY_GUARD = object()


def _canonical_uuid(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise ChapterProductionValidationError(f"{field} is invalid.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ChapterProductionValidationError(f"{field} is invalid.") from error
    if parsed.int == 0:
        raise ChapterProductionValidationError(f"{field} cannot be nil.")
    if str(parsed) != value:
        raise ChapterProductionValidationError(f"{field} is not canonical.")
    return value


def _optional_enum(value: object, enum_type: type[Enum], field: str) -> Enum | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ChapterProductionValidationError(f"Checkpoint {field} is invalid.")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ChapterProductionValidationError(f"Checkpoint {field} is unrecognized.") from error


@dataclass(frozen=True)
class ChapterReviewBinding:
    """Content-free, reloaded database facts for one required review report."""

    report_id: str
    stage: ChapterReviewStage
    workflow_run_id: str
    chapter_id: str
    document_id: str
    document_version_id: str
    review_mode: str
    reviewer_agent_role: str
    passed: bool

    def __post_init__(self) -> None:
        _canonical_uuid(self.report_id, "Live review report reference")
        _canonical_uuid(self.workflow_run_id, "Live review workflow reference")
        _canonical_uuid(self.chapter_id, "Live review chapter reference")
        _canonical_uuid(self.document_id, "Live review document reference")
        _canonical_uuid(self.document_version_id, "Live review version reference")
        if not isinstance(self.stage, ChapterReviewStage):
            raise ChapterProductionValidationError("Live review stage is not typed.")
        if type(self.passed) is not bool:
            raise ChapterProductionValidationError("Live review result is not typed.")
        expected_mode, expected_role = _REVIEW_AUTHORITY[self.stage]
        if self.review_mode != expected_mode or self.reviewer_agent_role != expected_role:
            raise ChapterProductionValidationError("Live review authority is invalid.")


@dataclass(frozen=True)
class ChapterReviewPolicyBinding:
    """Trusted server/run policy snapshot for readiness capability decisions."""

    workflow_run_id: str
    chapter_id: str
    review_policy_version: str
    chief_editor_required: bool

    def __post_init__(self) -> None:
        _canonical_uuid(self.workflow_run_id, "Policy workflow reference")
        _canonical_uuid(self.chapter_id, "Policy chapter reference")
        if type(self.review_policy_version) is not str or not _POLICY_RE.fullmatch(
            self.review_policy_version
        ):
            raise ChapterProductionValidationError("Trusted review policy is invalid.")
        if type(self.chief_editor_required) is not bool:
            raise ChapterProductionValidationError(
                "Trusted Chief Editor policy is not typed."
            )


@dataclass(frozen=True)
class ChapterActionBinding:
    """Reloaded, content-free facts for exactly one pending chapter action."""

    action_request_id: str
    workflow_run_id: str
    chapter_id: str
    request_type: str
    kind: ChapterActionKind
    status: ActionRequestStatus
    pending_count: int
    document_id: str
    document_version_id: str
    content_hash: str
    current_document_id: str
    current_document_version_id: str
    current_content_hash: str

    def __post_init__(self) -> None:
        _canonical_uuid(self.action_request_id, "Live action reference")
        _canonical_uuid(self.workflow_run_id, "Live action workflow reference")
        _canonical_uuid(self.chapter_id, "Live action chapter reference")
        _canonical_uuid(self.document_id, "Action document reference")
        _canonical_uuid(self.document_version_id, "Action version reference")
        _canonical_uuid(self.current_document_id, "Live current document reference")
        _canonical_uuid(
            self.current_document_version_id, "Live current version reference"
        )
        if type(self.content_hash) is not str or not _HASH_RE.fullmatch(self.content_hash):
            raise ChapterProductionValidationError("Action content hash is invalid.")
        if (
            type(self.current_content_hash) is not str
            or not _HASH_RE.fullmatch(self.current_content_hash)
        ):
            raise ChapterProductionValidationError("Live current content hash is invalid.")
        if not isinstance(self.kind, ChapterActionKind):
            raise ChapterProductionValidationError("Live action kind is not typed.")
        if self.request_type != _ACTION_REQUEST_TYPES[self.kind]:
            raise ChapterProductionValidationError("Live action request type is invalid.")
        if not isinstance(self.status, ActionRequestStatus):
            raise ChapterProductionValidationError("Live action status is not typed.")
        if type(self.pending_count) is not int or self.pending_count < 0:
            raise ChapterProductionValidationError("Live pending-action count is invalid.")


@dataclass(frozen=True)
class ChapterFailureReconciliationBinding:
    """Locked canonical facts proving an explicit failure-reconciliation outcome."""

    workflow_run_id: str
    chapter_id: str
    failure_code: ChapterFailureCode
    outcome: ChapterFailureReconciliationOutcome
    document_id: str | None
    current_document_version_id: str | None
    current_content_hash: str | None

    def __post_init__(self) -> None:
        _canonical_uuid(self.workflow_run_id, "Reconciliation workflow reference")
        _canonical_uuid(self.chapter_id, "Reconciliation chapter reference")
        _canonical_uuid(
            self.document_id, "Reconciliation document reference", optional=True
        )
        _canonical_uuid(
            self.current_document_version_id,
            "Reconciliation current-version reference",
            optional=True,
        )
        if not isinstance(self.failure_code, ChapterFailureCode):
            raise ChapterProductionValidationError(
                "Reconciliation failure code is not typed."
            )
        if not isinstance(self.outcome, ChapterFailureReconciliationOutcome):
            raise ChapterProductionValidationError(
                "Failure-reconciliation outcome is not typed."
            )
        live_parts = (
            self.document_id,
            self.current_document_version_id,
            self.current_content_hash,
        )
        if any(part is None for part in live_parts) and any(
            part is not None for part in live_parts
        ):
            raise ChapterProductionValidationError(
                "Reconciliation canonical binding is incomplete."
            )
        if self.current_content_hash is not None and (
            type(self.current_content_hash) is not str
            or not _HASH_RE.fullmatch(self.current_content_hash)
        ):
            raise ChapterProductionValidationError(
                "Reconciliation current content hash is invalid."
            )
        if (
            self.outcome
            is ChapterFailureReconciliationOutcome.CANONICAL_VERSION_COMMITTED
            and self.document_id is None
        ):
            raise ChapterProductionValidationError(
                "Committed reconciliation requires a canonical version binding."
            )


@dataclass(frozen=True)
class ChapterProductionState:
    """Immutable workflow mechanics and stable references, never creative content.

    Database-backed orchestrators remain responsible for reloading every referenced
    row and checking project/chapter/run/document ownership under lock. This value
    object makes every legal state edge explicit and prevents structurally invalid
    or cross-version review readiness from entering a checkpoint.
    """

    chapter_workflow_run_id: str
    chapter_id: str
    status: ChapterProductionStatus
    current_node: str
    awaiting_user: bool
    review_policy_version: str
    chief_editor_required: bool
    document_id: str | None = None
    document_version_id: str | None = None
    content_hash: str | None = None
    editor_report_id: str | None = None
    chief_editor_report_id: str | None = None
    lore_report_id: str | None = None
    action_request_id: str | None = None
    action_kind: ChapterActionKind | None = None
    failed_from_status: ChapterProductionStatus | None = None
    failure_code: ChapterFailureCode | None = None
    _ready_guard: InitVar[object] = None

    def __post_init__(self, _ready_guard: object) -> None:
        _canonical_uuid(self.chapter_workflow_run_id, "Chapter workflow run reference")
        _canonical_uuid(self.chapter_id, "Chapter reference")
        if not isinstance(self.status, ChapterProductionStatus):
            raise ChapterProductionValidationError("Checkpoint status is not typed.")
        if type(self.current_node) is not str or self.current_node != _NODES_BY_STATUS[self.status]:
            raise ChapterProductionValidationError("Checkpoint node does not match its status.")
        if type(self.awaiting_user) is not bool:
            raise ChapterProductionValidationError("Checkpoint waiting flag is not typed.")
        if type(self.chief_editor_required) is not bool:
            raise ChapterProductionValidationError("Chief-editor policy flag is not typed.")
        if type(self.review_policy_version) is not str or not _POLICY_RE.fullmatch(
            self.review_policy_version
        ):
            raise ChapterProductionValidationError("Review policy version is invalid.")
        finalized_lineage = self.status in _FINALIZED_STATUSES or (
            self.status is ChapterProductionStatus.FAILED
            and self.failed_from_status in _FINALIZED_STATUSES
        )
        if finalized_lineage and _ready_guard is not _READY_GUARD:
            raise ChapterProductionValidationError(
                "Revision-ready state requires live finalization."
            )

        _canonical_uuid(self.document_id, "Document reference", optional=True)
        _canonical_uuid(self.document_version_id, "Document version reference", optional=True)
        _canonical_uuid(self.editor_report_id, "Editor report reference", optional=True)
        _canonical_uuid(
            self.chief_editor_report_id, "Chief-editor report reference", optional=True
        )
        _canonical_uuid(self.lore_report_id, "Lore report reference", optional=True)
        _canonical_uuid(self.action_request_id, "Action reference", optional=True)

        candidate_parts = (self.document_id, self.document_version_id, self.content_hash)
        if any(part is None for part in candidate_parts) and any(
            part is not None for part in candidate_parts
        ):
            raise ChapterProductionValidationError("Candidate version binding is incomplete.")
        if self.content_hash is not None and (
            type(self.content_hash) is not str or not _HASH_RE.fullmatch(self.content_hash)
        ):
            raise ChapterProductionValidationError("Content hash is invalid.")
        if self.action_kind is not None and not isinstance(self.action_kind, ChapterActionKind):
            raise ChapterProductionValidationError("Action kind is not typed.")
        has_action = self.action_request_id is not None and self.action_kind is not None
        if self.awaiting_user is not has_action or (
            (self.action_request_id is None) is not (self.action_kind is None)
        ):
            raise ChapterProductionValidationError("Action binding does not match waiting state.")
        self._validate_failure_binding()
        self._validate_phase_binding()

    @classmethod
    def initial(
        cls,
        *,
        chapter_workflow_run_id: str,
        chapter_id: str,
        review_policy_version: str,
        chief_editor_required: bool,
    ) -> ChapterProductionState:
        return cls(
            chapter_workflow_run_id=chapter_workflow_run_id,
            chapter_id=chapter_id,
            status=ChapterProductionStatus.DRAFTING,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.DRAFTING],
            awaiting_user=False,
            review_policy_version=review_policy_version,
            chief_editor_required=chief_editor_required,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def semantic_ready_key(self) -> tuple[str, str, str] | None:
        if self.status is not ChapterProductionStatus.REVISION_READY:
            return None
        assert self.document_version_id is not None
        return (
            self.chapter_workflow_run_id,
            self.document_version_id,
            self.review_policy_version,
        )

    def submit_draft(
        self,
        *,
        document_id: str,
        document_version_id: str,
        content_hash: str,
        action: ChapterActionBinding,
    ) -> ChapterProductionState:
        self._require_status(ChapterProductionStatus.DRAFTING)
        self._validate_action_binding(
            action,
            ChapterActionKind.AUTHOR_REVISION,
            document_id=document_id,
            document_version_id=document_version_id,
            content_hash=content_hash,
        )
        if self.document_id is not None and (
            document_id != self.document_id or document_version_id == self.document_version_id
        ):
            raise ChapterProductionValidationError(
                "Redrafting requires a new version of the same document."
            )
        return replace(
            self,
            status=ChapterProductionStatus.AUTHOR_REVISION,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.AUTHOR_REVISION],
            awaiting_user=True,
            document_id=document_id,
            document_version_id=document_version_id,
            content_hash=content_hash,
            editor_report_id=None,
            chief_editor_report_id=None,
            lore_report_id=None,
            action_request_id=action.action_request_id,
            action_kind=ChapterActionKind.AUTHOR_REVISION,
        )

    def record_review(
        self,
        *,
        outcome: ChapterReviewOutcome,
        review: ChapterReviewBinding,
        action: ChapterActionBinding | None = None,
    ) -> ChapterProductionState:
        if self.awaiting_user:
            raise ChapterProductionValidationError(
                "A pending user action must be resolved before another review."
            )
        expected = {
            ChapterProductionStatus.EDITOR_REVIEW: ChapterReviewStage.EDITOR,
            ChapterProductionStatus.CHIEF_FINAL_REVIEW: ChapterReviewStage.CHIEF_EDITOR,
            ChapterProductionStatus.LORE_FINAL_REVIEW: ChapterReviewStage.LORE,
        }.get(self.status)
        if not isinstance(review, ChapterReviewBinding) or review.stage is not expected:
            raise ChapterProductionValidationError("Review stage is not allowed now.")
        if not isinstance(outcome, ChapterReviewOutcome):
            raise ChapterProductionValidationError("Review outcome is not typed.")
        if (
            review.workflow_run_id != self.chapter_workflow_run_id
            or review.chapter_id != self.chapter_id
            or review.document_id != self.document_id
            or review.document_version_id != self.document_version_id
        ):
            raise ChapterProductionValidationError("Review report scope or target is inconsistent.")
        if review.passed is not (outcome is not ChapterReviewOutcome.BLOCKING):
            raise ChapterProductionValidationError(
                "Review result does not match its persisted passed flag."
            )
        if outcome is ChapterReviewOutcome.PASSED and action is not None:
            raise ChapterProductionValidationError("A passed review cannot create an action.")
        if outcome is not ChapterReviewOutcome.PASSED and action is None:
            raise ChapterProductionValidationError("Review finding requires a user action.")
        if action is not None:
            expected_action_kind = (
                ChapterActionKind.REVIEW_WARNING
                if outcome is ChapterReviewOutcome.WARNING
                else ChapterActionKind.REVIEW_REVISION
            )
            self._validate_action_binding(
                action,
                expected_action_kind,
                document_id=self.document_id,
                document_version_id=self.document_version_id,
                content_hash=self.content_hash,
            )

        report_field = {
            ChapterReviewStage.EDITOR: "editor_report_id",
            ChapterReviewStage.CHIEF_EDITOR: "chief_editor_report_id",
            ChapterReviewStage.LORE: "lore_report_id",
        }[review.stage]
        if outcome is ChapterReviewOutcome.PASSED:
            target = self._next_review_status()
            return replace(
                self,
                **{
                    report_field: review.report_id,
                    "status": target,
                    "current_node": _NODES_BY_STATUS[target],
                },
            )
        if outcome is ChapterReviewOutcome.WARNING:
            return replace(
                self,
                **{report_field: review.report_id},
                awaiting_user=True,
                action_request_id=action.action_request_id if action is not None else None,
                action_kind=ChapterActionKind.REVIEW_WARNING,
            )
        return replace(
            self,
            **{report_field: review.report_id},
            status=ChapterProductionStatus.REVIEW_REVISION,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.REVIEW_REVISION],
            awaiting_user=True,
            action_request_id=action.action_request_id if action is not None else None,
            action_kind=ChapterActionKind.REVIEW_REVISION,
        )

    def resolve_action(
        self,
        *,
        action: ChapterActionBinding,
        decision: ChapterActionDecision,
        document_id: str | None = None,
        document_version_id: str | None = None,
        content_hash: str | None = None,
    ) -> ChapterProductionState:
        if not self.awaiting_user or self.action_request_id is None or self.action_kind is None:
            raise ChapterProductionValidationError("Chapter production is not awaiting an action.")
        if not isinstance(action, ChapterActionBinding):
            raise ChapterProductionValidationError("Live action binding is invalid.")
        self._validate_action_binding(
            action,
            self.action_kind,
            document_id=self.document_id,
            document_version_id=self.document_version_id,
            content_hash=self.content_hash,
        )
        if action.action_request_id != self.action_request_id:
            raise ChapterProductionValidationError("Chapter action is stale.")
        if not isinstance(decision, ChapterActionDecision):
            raise ChapterProductionValidationError("Chapter decision is not typed.")

        supplied_version = any(
            value is not None for value in (document_id, document_version_id, content_hash)
        )
        if decision is ChapterActionDecision.CANCEL:
            if supplied_version:
                raise ChapterProductionValidationError(
                    "Cancellation cannot supply a version binding."
                )
            return self._without_action(
                status=ChapterProductionStatus.CANCELLED,
                clear_failure=True,
            )
        if decision is ChapterActionDecision.SUBMIT_MANUAL_EDIT:
            if document_id is None or document_version_id is None or content_hash is None:
                raise ChapterProductionValidationError("Manual edit requires a version binding.")
            return self._new_version_for_review(document_id, document_version_id, content_hash)
        if supplied_version:
            raise ChapterProductionValidationError("This decision cannot supply a version binding.")

        if self.action_kind is ChapterActionKind.AUTHOR_REVISION:
            if decision is ChapterActionDecision.ACCEPT:
                return self._without_action(status=ChapterProductionStatus.EDITOR_REVIEW)
            if decision is ChapterActionDecision.REQUEST_REVISION:
                return self._without_action(status=ChapterProductionStatus.DRAFTING)
            raise ChapterProductionValidationError("Decision is invalid for author revision.")
        if self.action_kind is ChapterActionKind.REVIEW_WARNING:
            if decision is ChapterActionDecision.ACCEPT_WARNING:
                target = self._next_review_status()
                return replace(
                    self,
                    status=target,
                    current_node=_NODES_BY_STATUS[target],
                    awaiting_user=False,
                    action_request_id=None,
                    action_kind=None,
                )
            if decision is ChapterActionDecision.REQUEST_REVISION:
                return self._without_action(status=ChapterProductionStatus.REVIEW_REVISION)
            raise ChapterProductionValidationError("Decision is invalid for a review warning.")
        if self.action_kind is ChapterActionKind.REVIEW_REVISION:
            if decision is ChapterActionDecision.REQUEST_REVISION:
                return self._without_action(status=ChapterProductionStatus.REVIEW_REVISION)
            raise ChapterProductionValidationError("Decision is invalid for required revision.")
        raise ChapterProductionValidationError("Action kind is invalid.")

    def reconcile_stale_action(
        self, *, action: ChapterActionBinding
    ) -> ChapterProductionState:
        """Cancel one exact stale action and adopt its locked canonical successor."""

        if (
            not self.awaiting_user
            or self.action_request_id is None
            or self.action_kind is None
            or self.document_id is None
            or self.document_version_id is None
            or self.content_hash is None
        ):
            raise ChapterProductionValidationError(
                "Chapter production is not awaiting a version-bound action."
            )
        if not isinstance(action, ChapterActionBinding):
            raise ChapterProductionValidationError("Live action binding is invalid.")
        if (
            action.action_request_id != self.action_request_id
            or action.workflow_run_id != self.chapter_workflow_run_id
            or action.chapter_id != self.chapter_id
            or action.kind is not self.action_kind
            or action.request_type != _ACTION_REQUEST_TYPES[self.action_kind]
            or action.status is not ActionRequestStatus.PENDING
            or action.pending_count != 1
            or action.document_id != self.document_id
            or action.document_version_id != self.document_version_id
            or action.content_hash != self.content_hash
            or action.current_document_id != self.document_id
            or action.current_document_version_id == self.document_version_id
        ):
            raise ChapterProductionValidationError(
                "Stale-action reconciliation proof is inconsistent."
            )
        return self._new_version_for_review(
            action.current_document_id,
            action.current_document_version_id,
            action.current_content_hash,
        )

    def submit_review_revision(
        self, *, document_id: str, document_version_id: str, content_hash: str
    ) -> ChapterProductionState:
        self._require_status(ChapterProductionStatus.REVIEW_REVISION)
        if self.awaiting_user:
            raise ChapterProductionValidationError("Revision action must be resolved first.")
        return self._new_version_for_review(document_id, document_version_id, content_hash)

    def invalidate_for_new_version(
        self, *, document_id: str, document_version_id: str, content_hash: str
    ) -> ChapterProductionState:
        if self.status not in {
            ChapterProductionStatus.EDITOR_REVIEW,
            ChapterProductionStatus.CHIEF_FINAL_REVIEW,
            ChapterProductionStatus.LORE_FINAL_REVIEW,
            ChapterProductionStatus.REVISION_READY,
            ChapterProductionStatus.ARCHIVE_UPDATE,
        } or self.awaiting_user:
            raise ChapterProductionValidationError("A new version cannot be adopted now.")
        return self._new_version_for_review(document_id, document_version_id, content_hash)

    def begin_archive_update(
        self,
        *,
        policy: ChapterReviewPolicyBinding,
        document_id: str,
        current_document_version_id: str,
        version_content_hash: str,
        editor_report: ChapterReviewBinding,
        chief_editor_report: ChapterReviewBinding | None,
        lore_report: ChapterReviewBinding,
    ) -> ChapterProductionState:
        self._require_status(ChapterProductionStatus.REVISION_READY)
        self.validate_review_policy(policy)
        self.validate_live_readiness(
            document_id=document_id,
            current_document_version_id=current_document_version_id,
            version_content_hash=version_content_hash,
            editor_report=editor_report,
            chief_editor_report=chief_editor_report,
            lore_report=lore_report,
        )
        return replace(
            self,
            status=ChapterProductionStatus.ARCHIVE_UPDATE,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.ARCHIVE_UPDATE],
            _ready_guard=_READY_GUARD,
        )

    def complete(self) -> ChapterProductionState:
        self._require_status(ChapterProductionStatus.ARCHIVE_UPDATE)
        return replace(
            self,
            status=ChapterProductionStatus.COMPLETED,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.COMPLETED],
            _ready_guard=_READY_GUARD,
        )

    def fail(self, failure_code: ChapterFailureCode) -> ChapterProductionState:
        if self.is_terminal or self.status is ChapterProductionStatus.FAILED:
            raise ChapterProductionValidationError("Terminal workflow cannot fail again.")
        if self.awaiting_user:
            raise ChapterProductionValidationError("A live user action cannot be hidden by failure.")
        if not isinstance(failure_code, ChapterFailureCode):
            raise ChapterProductionValidationError("Failure code is not server-owned.")
        return replace(
            self,
            status=ChapterProductionStatus.FAILED,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.FAILED],
            failed_from_status=self.status,
            failure_code=failure_code,
            _ready_guard=(
                _READY_GUARD if self.status in _FINALIZED_STATUSES else None
            ),
        )

    def recover(self) -> ChapterProductionState:
        self._require_status(ChapterProductionStatus.FAILED)
        assert self.failed_from_status is not None
        if self.failure_code in _RECONCILIATION_FAILURE_CODES:
            raise ChapterProductionValidationError(
                "Failure requires an explicit reconciliation workflow."
            )
        if self.failed_from_status in _FINALIZED_STATUSES:
            raise ChapterProductionValidationError(
                "Finalized failure recovery requires locked live readiness."
            )
        return replace(
            self,
            status=self.failed_from_status,
            current_node=_NODES_BY_STATUS[self.failed_from_status],
            failed_from_status=None,
            failure_code=None,
        )

    def recover_finalized(
        self,
        *,
        document_id: str,
        current_document_version_id: str,
        version_content_hash: str,
        editor_report: ChapterReviewBinding,
        chief_editor_report: ChapterReviewBinding | None,
        lore_report: ChapterReviewBinding,
        policy: ChapterReviewPolicyBinding,
    ) -> ChapterProductionState:
        """Recover a finalized phase only after locked readiness is revalidated."""

        self._require_status(ChapterProductionStatus.FAILED)
        if (
            self.failure_code in _RECONCILIATION_FAILURE_CODES
            or self.failed_from_status not in _FINALIZED_STATUSES
        ):
            raise ChapterProductionValidationError(
                "Failure is not eligible for finalized live recovery."
            )
        self.validate_review_policy(policy)
        self.validate_live_readiness(
            document_id=document_id,
            current_document_version_id=current_document_version_id,
            version_content_hash=version_content_hash,
            editor_report=editor_report,
            chief_editor_report=chief_editor_report,
            lore_report=lore_report,
        )
        assert self.failed_from_status is not None
        return replace(
            self,
            status=self.failed_from_status,
            current_node=_NODES_BY_STATUS[self.failed_from_status],
            failed_from_status=None,
            failure_code=None,
            _ready_guard=_READY_GUARD,
        )

    def reconcile_failure(
        self,
        *,
        binding: ChapterFailureReconciliationBinding,
        editor_report: ChapterReviewBinding | None = None,
        chief_editor_report: ChapterReviewBinding | None = None,
        lore_report: ChapterReviewBinding | None = None,
        action: ChapterActionBinding | None = None,
        policy: ChapterReviewPolicyBinding | None = None,
    ) -> ChapterProductionState:
        """Exit a reconciliation-only failure using locked, typed database proof."""

        self._require_status(ChapterProductionStatus.FAILED)
        if not isinstance(binding, ChapterFailureReconciliationBinding):
            raise ChapterProductionValidationError(
                "Failure reconciliation requires typed live proof."
            )
        if (
            self.failure_code not in _RECONCILIATION_FAILURE_CODES
            or binding.failure_code is not self.failure_code
            or binding.workflow_run_id != self.chapter_workflow_run_id
            or binding.chapter_id != self.chapter_id
        ):
            raise ChapterProductionValidationError(
                "Failure-reconciliation proof has the wrong scope or failure."
            )
        assert self.failed_from_status is not None
        has_candidate = self.document_id is not None
        if (
            binding.outcome
            is ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED
        ):
            if action is not None:
                raise ChapterProductionValidationError(
                    "No-write reconciliation cannot create a user action."
                )
            if has_candidate and (
                binding.document_id != self.document_id
                or binding.current_document_version_id != self.document_version_id
                or binding.current_content_hash != self.content_hash
            ):
                raise ChapterProductionValidationError(
                    "No-write reconciliation proof does not match the failed candidate."
                )
            if not has_candidate and any(
                value is not None
                for value in (
                    binding.document_id,
                    binding.current_document_version_id,
                    binding.current_content_hash,
                )
            ):
                raise ChapterProductionValidationError(
                    "Candidate-free reconciliation proof must confirm no canonical version."
                )
            if self.failed_from_status in _FINALIZED_STATUSES:
                self.validate_review_policy(policy)  # type: ignore[arg-type]
                assert binding.document_id is not None
                assert binding.current_document_version_id is not None
                assert binding.current_content_hash is not None
                self.validate_live_readiness(
                    document_id=binding.document_id,
                    current_document_version_id=binding.current_document_version_id,
                    version_content_hash=binding.current_content_hash,
                    editor_report=editor_report,  # type: ignore[arg-type]
                    chief_editor_report=chief_editor_report,
                    lore_report=lore_report,  # type: ignore[arg-type]
                )
            return replace(
                self,
                status=self.failed_from_status,
                current_node=_NODES_BY_STATUS[self.failed_from_status],
                failed_from_status=None,
                failure_code=None,
                _ready_guard=(
                    _READY_GUARD
                    if self.failed_from_status in _FINALIZED_STATUSES
                    else None
                ),
            )
        if (
            binding.outcome
            is ChapterFailureReconciliationOutcome.CANONICAL_VERSION_COMMITTED
        ):
            assert binding.document_id is not None
            assert binding.current_document_version_id is not None
            assert binding.current_content_hash is not None
            if has_candidate and (
                binding.document_id != self.document_id
                or binding.current_document_version_id == self.document_version_id
            ):
                raise ChapterProductionValidationError(
                    "Committed-version proof does not identify a new canonical version."
                )
            if not has_candidate and self.failed_from_status is not ChapterProductionStatus.DRAFTING:
                raise ChapterProductionValidationError(
                    "Candidate-free committed reconciliation has the wrong phase."
                )
            if self.failed_from_status is ChapterProductionStatus.DRAFTING:
                if not isinstance(action, ChapterActionBinding):
                    raise ChapterProductionValidationError(
                        "Drafting reconciliation requires a typed author action."
                    )
                self._validate_action_binding(
                    action,
                    ChapterActionKind.AUTHOR_REVISION,
                    document_id=binding.document_id,
                    document_version_id=binding.current_document_version_id,
                    content_hash=binding.current_content_hash,
                )
                assert action is not None
                return replace(
                    self,
                    status=ChapterProductionStatus.AUTHOR_REVISION,
                    current_node=_NODES_BY_STATUS[
                        ChapterProductionStatus.AUTHOR_REVISION
                    ],
                    awaiting_user=True,
                    document_id=binding.document_id,
                    document_version_id=binding.current_document_version_id,
                    content_hash=binding.current_content_hash,
                    editor_report_id=None,
                    chief_editor_report_id=None,
                    lore_report_id=None,
                    action_request_id=action.action_request_id,
                    action_kind=ChapterActionKind.AUTHOR_REVISION,
                    failed_from_status=None,
                    failure_code=None,
                )
            if action is not None:
                raise ChapterProductionValidationError(
                    "Committed-version reconciliation has an unexpected action."
                )
            return self._new_version_for_review(
                binding.document_id,
                binding.current_document_version_id,
                binding.current_content_hash,
            )
        raise ChapterProductionValidationError(
            "Failure-reconciliation outcome is unsupported."
        )

    def to_checkpoint(self) -> dict[str, object]:
        return {
            "version": 2,
            "chapter_workflow_run_id": self.chapter_workflow_run_id,
            "chapter_id": self.chapter_id,
            "status": self.status.value,
            "current_node": self.current_node,
            "awaiting_user": self.awaiting_user,
            "review_policy_version": self.review_policy_version,
            "chief_editor_required": self.chief_editor_required,
            "document_id": self.document_id,
            "document_version_id": self.document_version_id,
            "content_hash": self.content_hash,
            "editor_report_id": self.editor_report_id,
            "chief_editor_report_id": self.chief_editor_report_id,
            "lore_report_id": self.lore_report_id,
            "action_request_id": self.action_request_id,
            "action_kind": self.action_kind.value if self.action_kind is not None else None,
            "failed_from_status": (
                self.failed_from_status.value if self.failed_from_status is not None else None
            ),
            "failure_code": self.failure_code.value if self.failure_code is not None else None,
        }

    @classmethod
    def from_checkpoint(
        cls, payload: object, *, _ready_guard: object = None
    ) -> ChapterProductionState:
        if type(payload) is not dict or set(payload) != _CHECKPOINT_KEYS:
            raise ChapterProductionValidationError("Checkpoint payload has an unrecognized shape.")
        if type(payload["version"]) is not int or payload["version"] != 2:
            raise ChapterProductionValidationError("Checkpoint payload has an invalid version.")
        if type(payload["status"]) is not str:
            raise ChapterProductionValidationError("Checkpoint status is invalid.")
        try:
            status = ChapterProductionStatus(payload["status"])
        except ValueError as error:
            raise ChapterProductionValidationError("Checkpoint status is unrecognized.") from error
        if type(payload["current_node"]) is not str or type(payload["awaiting_user"]) is not bool:
            raise ChapterProductionValidationError("Checkpoint workflow fields are invalid.")
        if type(payload["review_policy_version"]) is not str or type(
            payload["chief_editor_required"]
        ) is not bool:
            raise ChapterProductionValidationError("Checkpoint policy fields are invalid.")

        action_kind = _optional_enum(payload["action_kind"], ChapterActionKind, "action kind")
        failed_from = _optional_enum(
            payload["failed_from_status"], ChapterProductionStatus, "failure source"
        )
        failure_code = _optional_enum(
            payload["failure_code"], ChapterFailureCode, "failure code"
        )
        for field in (
            "chapter_workflow_run_id",
            "chapter_id",
            "document_id",
            "document_version_id",
            "content_hash",
            "editor_report_id",
            "chief_editor_report_id",
            "lore_report_id",
            "action_request_id",
        ):
            if payload[field] is not None and type(payload[field]) is not str:
                raise ChapterProductionValidationError(f"Checkpoint {field} is invalid.")
        return cls(
            chapter_workflow_run_id=payload["chapter_workflow_run_id"],
            chapter_id=payload["chapter_id"],
            status=status,
            current_node=payload["current_node"],
            awaiting_user=payload["awaiting_user"],
            review_policy_version=payload["review_policy_version"],
            chief_editor_required=payload["chief_editor_required"],
            document_id=payload["document_id"],
            document_version_id=payload["document_version_id"],
            content_hash=payload["content_hash"],
            editor_report_id=payload["editor_report_id"],
            chief_editor_report_id=payload["chief_editor_report_id"],
            lore_report_id=payload["lore_report_id"],
            action_request_id=payload["action_request_id"],
            action_kind=action_kind,  # type: ignore[arg-type]
            failed_from_status=failed_from,  # type: ignore[arg-type]
            failure_code=failure_code,  # type: ignore[arg-type]
            _ready_guard=(
                _ready_guard
                if status in _FINALIZED_STATUSES
                or (
                    status is ChapterProductionStatus.FAILED
                    and failed_from in _FINALIZED_STATUSES
                )
                else None
            ),
        )

    @classmethod
    def from_finalized_checkpoint(
        cls,
        payload: object,
        *,
        policy: ChapterReviewPolicyBinding,
        workflow_run_id: str,
        chapter_id: str,
        run_workflow_type: str,
        run_status: str,
        run_current_node: str | None,
        run_awaiting_user: bool,
        checkpoint_workflow_run_id: str,
        checkpoint_node_name: str | None,
        document_id: str,
        current_document_version_id: str,
        version_content_hash: str,
        editor_report: ChapterReviewBinding,
        chief_editor_report: ChapterReviewBinding | None,
        lore_report: ChapterReviewBinding,
    ) -> ChapterProductionState:
        """Restore finalized lineage while reconciling policy, run, and live facts."""

        state = cls.from_checkpoint(payload, _ready_guard=_READY_GUARD)
        if state.status not in _FINALIZED_STATUSES and not (
            state.status is ChapterProductionStatus.FAILED
            and state.failed_from_status in _FINALIZED_STATUSES
        ):
            raise ChapterProductionValidationError("Checkpoint is not readiness-finalized.")
        state.validate_review_policy(policy)
        state.validate_persistence_binding(
            workflow_run_id=workflow_run_id,
            chapter_id=chapter_id,
            run_workflow_type=run_workflow_type,
            run_status=run_status,
            run_current_node=run_current_node,
            run_awaiting_user=run_awaiting_user,
            checkpoint_workflow_run_id=checkpoint_workflow_run_id,
            checkpoint_node_name=checkpoint_node_name,
        )
        state.validate_live_readiness(
            document_id=document_id,
            current_document_version_id=current_document_version_id,
            version_content_hash=version_content_hash,
            editor_report=editor_report,
            chief_editor_report=chief_editor_report,
            lore_report=lore_report,
        )
        return state

    @classmethod
    def from_revision_ready_checkpoint(
        cls,
        payload: object,
        *,
        policy: ChapterReviewPolicyBinding,
        workflow_run_id: str,
        chapter_id: str,
        run_workflow_type: str,
        run_status: str,
        run_current_node: str | None,
        run_awaiting_user: bool,
        checkpoint_workflow_run_id: str,
        checkpoint_node_name: str | None,
        document_id: str,
        current_document_version_id: str,
        version_content_hash: str,
        editor_report: ChapterReviewBinding,
        chief_editor_report: ChapterReviewBinding | None,
        lore_report: ChapterReviewBinding,
    ) -> ChapterProductionState:
        """Restore the exact READY capability from trusted policy and live facts."""

        state = cls.from_finalized_checkpoint(
            payload,
            policy=policy,
            workflow_run_id=workflow_run_id,
            chapter_id=chapter_id,
            run_workflow_type=run_workflow_type,
            run_status=run_status,
            run_current_node=run_current_node,
            run_awaiting_user=run_awaiting_user,
            checkpoint_workflow_run_id=checkpoint_workflow_run_id,
            checkpoint_node_name=checkpoint_node_name,
            document_id=document_id,
            current_document_version_id=current_document_version_id,
            version_content_hash=version_content_hash,
            editor_report=editor_report,
            chief_editor_report=chief_editor_report,
            lore_report=lore_report,
        )
        if state.status is not ChapterProductionStatus.REVISION_READY:
            raise ChapterProductionValidationError(
                "Checkpoint is not the exact revision-ready capability."
            )
        return state

    def to_public_event_payload(self) -> dict[str, str | bool]:
        payload: dict[str, str | bool] = {
            "status": self.status.value,
            "current_node": self.current_node,
            "awaiting_user": self.awaiting_user,
            "chapter_id": self.chapter_id,
        }
        if self.document_version_id is not None:
            payload["document_version_id"] = self.document_version_id
        if self.action_request_id is not None:
            payload["action_request_id"] = self.action_request_id
        if self.action_kind is not None:
            payload["action_kind"] = self.action_kind.value
        if self.failure_code is not None:
            payload["failure_code"] = self.failure_code.value
        return payload

    def validate_review_policy(self, policy: ChapterReviewPolicyBinding) -> None:
        """Match checkpoint policy claims to a trusted server/run snapshot."""

        if not isinstance(policy, ChapterReviewPolicyBinding) or (
            policy.workflow_run_id != self.chapter_workflow_run_id
            or policy.chapter_id != self.chapter_id
            or policy.review_policy_version != self.review_policy_version
            or policy.chief_editor_required is not self.chief_editor_required
        ):
            raise ChapterProductionValidationError(
                "Checkpoint review policy does not match trusted policy."
            )

    def validate_persistence_binding(
        self,
        *,
        workflow_run_id: str,
        chapter_id: str,
        run_workflow_type: str,
        run_status: str,
        run_current_node: str | None,
        run_awaiting_user: bool,
        checkpoint_workflow_run_id: str,
        checkpoint_node_name: str | None,
    ) -> None:
        """Fail closed unless a run and checkpoint project this exact state."""

        _canonical_uuid(workflow_run_id, "Persisted workflow run reference")
        _canonical_uuid(chapter_id, "Persisted chapter reference")
        _canonical_uuid(
            checkpoint_workflow_run_id, "Persisted checkpoint workflow reference"
        )
        if workflow_run_id != self.chapter_workflow_run_id or chapter_id != self.chapter_id:
            raise ChapterProductionValidationError("Persisted workflow scope is inconsistent.")
        if (
            run_workflow_type != WorkflowType.CHAPTER_PRODUCTION.value
            or checkpoint_workflow_run_id != self.chapter_workflow_run_id
            or type(run_status) is not str
            or run_status != self.status.value
            or run_current_node != self.current_node
            or type(run_awaiting_user) is not bool
            or run_awaiting_user is not self.awaiting_user
            or checkpoint_node_name != self.current_node
        ):
            raise ChapterProductionValidationError(
                "Persisted run and checkpoint discriminators are inconsistent."
            )

    def validate_live_readiness(
        self,
        *,
        document_id: str,
        current_document_version_id: str,
        version_content_hash: str,
        editor_report: ChapterReviewBinding,
        chief_editor_report: ChapterReviewBinding | None,
        lore_report: ChapterReviewBinding,
    ) -> None:
        """Reconcile a ready checkpoint with locked canonical database facts."""

        phase = (
            self.failed_from_status
            if self.status is ChapterProductionStatus.FAILED
            else self.status
        )
        if (
            phase
            not in {
                ChapterProductionStatus.LORE_FINAL_REVIEW,
                *_FINALIZED_STATUSES,
            }
            or self.awaiting_user
            or self.lore_report_id is None
        ):
            raise ChapterProductionValidationError("Chapter is not eligible for readiness validation.")
        _canonical_uuid(document_id, "Live document reference")
        _canonical_uuid(current_document_version_id, "Live current-version reference")
        if type(version_content_hash) is not str or not _HASH_RE.fullmatch(version_content_hash):
            raise ChapterProductionValidationError("Live version hash is invalid.")
        if (
            document_id != self.document_id
            or current_document_version_id != self.document_version_id
            or version_content_hash != self.content_hash
        ):
            raise ChapterProductionValidationError("Ready candidate is stale or inconsistent.")
        bindings = (
            (ChapterReviewStage.EDITOR, self.editor_report_id, editor_report),
            (ChapterReviewStage.CHIEF_EDITOR, self.chief_editor_report_id, chief_editor_report),
            (ChapterReviewStage.LORE, self.lore_report_id, lore_report),
        )
        for stage, expected_id, binding in bindings:
            if expected_id is None:
                if binding is not None:
                    raise ChapterProductionValidationError("Policy-disabled review is present.")
                continue
            if not isinstance(binding, ChapterReviewBinding):
                raise ChapterProductionValidationError("Required live review is missing.")
            if (
                binding.stage is not stage
                or binding.report_id != expected_id
                or binding.workflow_run_id != self.chapter_workflow_run_id
                or binding.chapter_id != self.chapter_id
                or binding.document_id != self.document_id
                or binding.document_version_id != self.document_version_id
                or not binding.passed
            ):
                raise ChapterProductionValidationError("Live review binding is inconsistent.")

    def finalize_revision_ready(
        self,
        *,
        policy: ChapterReviewPolicyBinding,
        document_id: str,
        current_document_version_id: str,
        version_content_hash: str,
        editor_report: ChapterReviewBinding,
        chief_editor_report: ChapterReviewBinding | None,
        lore_report: ChapterReviewBinding,
    ) -> ChapterProductionState:
        """Enter the sole ready state only after locked live facts reconcile."""

        self._require_status(ChapterProductionStatus.LORE_FINAL_REVIEW)
        if self.lore_report_id is None or self.awaiting_user:
            raise ChapterProductionValidationError("Final Lore review is not complete.")
        self.validate_review_policy(policy)
        self.validate_live_readiness(
            document_id=document_id,
            current_document_version_id=current_document_version_id,
            version_content_hash=version_content_hash,
            editor_report=editor_report,
            chief_editor_report=chief_editor_report,
            lore_report=lore_report,
        )
        return replace(
            self,
            status=ChapterProductionStatus.REVISION_READY,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.REVISION_READY],
            _ready_guard=_READY_GUARD,
        )

    def _advance_after_accepted_review(self) -> ChapterProductionState:
        target = self._next_review_status()
        return replace(self, status=target, current_node=_NODES_BY_STATUS[target])

    def _next_review_status(self) -> ChapterProductionStatus:
        if self.status is ChapterProductionStatus.EDITOR_REVIEW:
            target = (
                ChapterProductionStatus.CHIEF_FINAL_REVIEW
                if self.chief_editor_required
                else ChapterProductionStatus.LORE_FINAL_REVIEW
            )
        elif self.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW:
            target = ChapterProductionStatus.LORE_FINAL_REVIEW
        elif self.status is ChapterProductionStatus.LORE_FINAL_REVIEW:
            target = ChapterProductionStatus.LORE_FINAL_REVIEW
        else:
            raise ChapterProductionValidationError("Reviewed state cannot advance now.")
        return target

    def _new_version_for_review(
        self, document_id: str, document_version_id: str, content_hash: str
    ) -> ChapterProductionState:
        if self.document_id is None or self.document_version_id is None:
            raise ChapterProductionValidationError("Existing candidate binding is missing.")
        if document_id != self.document_id:
            raise ChapterProductionValidationError("New version belongs to another document.")
        if document_version_id == self.document_version_id:
            raise ChapterProductionValidationError("Revision must create a new immutable version.")
        return replace(
            self,
            status=ChapterProductionStatus.EDITOR_REVIEW,
            current_node=_NODES_BY_STATUS[ChapterProductionStatus.EDITOR_REVIEW],
            awaiting_user=False,
            document_id=document_id,
            document_version_id=document_version_id,
            content_hash=content_hash,
            editor_report_id=None,
            chief_editor_report_id=None,
            lore_report_id=None,
            action_request_id=None,
            action_kind=None,
            failed_from_status=None,
            failure_code=None,
        )

    def _validate_action_binding(
        self,
        action: ChapterActionBinding,
        expected_kind: ChapterActionKind,
        *,
        document_id: str | None,
        document_version_id: str | None,
        content_hash: str | None,
    ) -> None:
        if not isinstance(action, ChapterActionBinding):
            raise ChapterProductionValidationError("Live action binding is invalid.")
        if (
            action.workflow_run_id != self.chapter_workflow_run_id
            or action.chapter_id != self.chapter_id
            or action.kind is not expected_kind
            or action.request_type != _ACTION_REQUEST_TYPES[expected_kind]
            or action.status is not ActionRequestStatus.PENDING
            or action.pending_count != 1
            or action.document_id != document_id
            or action.document_version_id != document_version_id
            or action.content_hash != content_hash
            or action.current_document_id != document_id
            or action.current_document_version_id != document_version_id
            or action.current_content_hash != content_hash
        ):
            raise ChapterProductionValidationError(
                "Live action is foreign, stale, duplicated, mistargeted, or the wrong kind."
            )

    def _without_action(
        self,
        *,
        status: ChapterProductionStatus | None = None,
        clear_failure: bool = False,
    ) -> ChapterProductionState:
        target = status or self.status
        return replace(
            self,
            status=target,
            current_node=_NODES_BY_STATUS[target],
            awaiting_user=False,
            action_request_id=None,
            action_kind=None,
            failed_from_status=None if clear_failure else self.failed_from_status,
            failure_code=None if clear_failure else self.failure_code,
        )

    def _require_status(self, expected: ChapterProductionStatus) -> None:
        if self.status is not expected or self.is_terminal:
            raise ChapterProductionValidationError("Chapter-production transition is not allowed.")

    def _validate_failure_binding(self) -> None:
        if self.status is ChapterProductionStatus.FAILED:
            if (
                not isinstance(self.failed_from_status, ChapterProductionStatus)
                or self.failed_from_status in _TERMINAL_STATUSES
                or self.failed_from_status is ChapterProductionStatus.FAILED
                or not isinstance(self.failure_code, ChapterFailureCode)
                or self.awaiting_user
            ):
                raise ChapterProductionValidationError("Failure recovery binding is invalid.")
        elif self.failed_from_status is not None or self.failure_code is not None:
            raise ChapterProductionValidationError("Non-failed state contains failure data.")

    def _validate_phase_binding(self) -> None:
        phase = (
            self.failed_from_status
            if self.status is ChapterProductionStatus.FAILED
            else self.status
        )
        assert phase is not None
        has_candidate = self.document_id is not None
        has_any_report = any(
            report is not None
            for report in (
                self.editor_report_id,
                self.chief_editor_report_id,
                self.lore_report_id,
            )
        )
        if has_any_report and not has_candidate:
            raise ChapterProductionValidationError("Review reports require a candidate version.")
        if not self.chief_editor_required and self.chief_editor_report_id is not None:
            raise ChapterProductionValidationError("Policy-disabled Chief Editor cannot have a report.")
        if self.chief_editor_report_id is not None and self.editor_report_id is None:
            raise ChapterProductionValidationError("Chief-editor report requires an editor report.")
        if self.lore_report_id is not None and (
            self.editor_report_id is None
            or (self.chief_editor_required and self.chief_editor_report_id is None)
        ):
            raise ChapterProductionValidationError("Lore report requires all earlier reviews.")

        if phase is ChapterProductionStatus.DRAFTING:
            if has_any_report or self.awaiting_user:
                raise ChapterProductionValidationError("Drafting state contains future bindings.")
            return
        if phase is ChapterProductionStatus.AUTHOR_REVISION:
            if not has_candidate or self.action_kind is not ChapterActionKind.AUTHOR_REVISION:
                raise ChapterProductionValidationError("Author revision binding is incomplete.")
            if has_any_report:
                raise ChapterProductionValidationError("Author revision contains future reports.")
            return
        if phase is ChapterProductionStatus.EDITOR_REVIEW:
            if not has_candidate or self.chief_editor_report_id is not None or self.lore_report_id is not None:
                raise ChapterProductionValidationError("Editor review binding is invalid.")
            if self.awaiting_user:
                if (
                    self.action_kind is not ChapterActionKind.REVIEW_WARNING
                    or self.editor_report_id is None
                ):
                    raise ChapterProductionValidationError("Editor warning binding is invalid.")
            elif self.editor_report_id is not None:
                raise ChapterProductionValidationError("Editor review already contains a result.")
            return
        if phase is ChapterProductionStatus.CHIEF_FINAL_REVIEW:
            if not has_candidate or not self.chief_editor_required or self.editor_report_id is None:
                raise ChapterProductionValidationError("Chief-editor review binding is incomplete.")
            if self.lore_report_id is not None:
                raise ChapterProductionValidationError("Chief-editor review contains a future report.")
            if self.awaiting_user:
                if (
                    self.action_kind is not ChapterActionKind.REVIEW_WARNING
                    or self.chief_editor_report_id is None
                ):
                    raise ChapterProductionValidationError("Chief-editor warning binding is invalid.")
            elif self.chief_editor_report_id is not None:
                raise ChapterProductionValidationError("Chief-editor review already contains a result.")
            return
        if phase is ChapterProductionStatus.LORE_FINAL_REVIEW:
            if not has_candidate or self.editor_report_id is None or (
                self.chief_editor_required and self.chief_editor_report_id is None
            ):
                raise ChapterProductionValidationError("Lore review binding is incomplete.")
            if self.awaiting_user:
                if self.action_kind is not ChapterActionKind.REVIEW_WARNING or self.lore_report_id is None:
                    raise ChapterProductionValidationError("Lore warning binding is invalid.")
            return
        if phase is ChapterProductionStatus.REVIEW_REVISION:
            if not has_candidate or not has_any_report:
                raise ChapterProductionValidationError("Review revision binding is incomplete.")
            if self.awaiting_user and self.action_kind is not ChapterActionKind.REVIEW_REVISION:
                raise ChapterProductionValidationError("Review revision action is invalid.")
            return
        if phase in _FINALIZED_STATUSES:
            if (
                not has_candidate
                or self.editor_report_id is None
                or self.lore_report_id is None
                or (self.chief_editor_required and self.chief_editor_report_id is None)
                or self.awaiting_user
            ):
                raise ChapterProductionValidationError("Revision-ready binding is incomplete.")
            return
        if phase is ChapterProductionStatus.CANCELLED:
            if self.awaiting_user:
                raise ChapterProductionValidationError("Cancelled state cannot await an action.")
            return
        raise ChapterProductionValidationError("Checkpoint status is unsupported.")


@dataclass(frozen=True)
class LegacyChapterProductionSnapshot:
    """Read-only v0.8 run projection; it is never upgraded into V2 readiness."""

    workflow_run_id: str
    chapter_id: str
    status: str
    current_node: str
    awaiting_user: bool

    @classmethod
    def from_run_projection(
        cls,
        *,
        workflow_run_id: str,
        chapter_id: str,
        status: str,
        current_node: str,
        awaiting_user: bool,
    ) -> LegacyChapterProductionSnapshot:
        _canonical_uuid(workflow_run_id, "Legacy workflow run reference")
        _canonical_uuid(chapter_id, "Legacy chapter reference")
        allowed = {
            ("awaiting_approval", "approval", True),
            ("completed", "approval", False),
            ("rejected", "approval", False),
        }
        if (
            type(status) is not str
            or type(current_node) is not str
            or type(awaiting_user) is not bool
            or (status, current_node, awaiting_user) not in allowed
        ):
            raise ChapterProductionValidationError("Legacy chapter run projection is invalid.")
        return cls(workflow_run_id, chapter_id, status, current_node, awaiting_user)

    @property
    def is_revision_ready(self) -> bool:
        return False


__all__ = [
    "ChapterActionDecision",
    "ChapterActionBinding",
    "ChapterActionKind",
    "ChapterProductionState",
    "ChapterProductionStatus",
    "ChapterProductionValidationError",
    "ChapterFailureCode",
    "ChapterFailureReconciliationBinding",
    "ChapterFailureReconciliationOutcome",
    "ChapterReviewOutcome",
    "ChapterReviewBinding",
    "ChapterReviewPolicyBinding",
    "ChapterReviewStage",
    "LegacyChapterProductionSnapshot",
]
