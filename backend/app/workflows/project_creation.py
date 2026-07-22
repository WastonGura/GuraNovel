"""Safe, durable contracts for the project-creation workflow foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class ProjectCreationStatus(str, Enum):
    """The deliberately small foundation state machine."""

    USER_IDEA = "user_idea"
    CONCEPT_OPTIONS = "concept_options"
    # Legacy value retained solely to read #64 checkpoints; #65 never transitions to it.
    CONCEPT_REVIEWED = "concept_reviewed"
    REVISION_REQUIRED = "revision_required"
    CONCEPT_SELECTED = "concept_selected"
    COMPLETED = "completed"
    REJECTED = "rejected"


class ProjectCreationValidationError(ValueError):
    """Raised when an untrusted checkpoint does not match the durable contract."""


_NODES_BY_STATUS = {
    ProjectCreationStatus.USER_IDEA: "user_idea",
    ProjectCreationStatus.CONCEPT_OPTIONS: "concept_review",
    ProjectCreationStatus.CONCEPT_REVIEWED: "concept_reviewed",
    ProjectCreationStatus.REVISION_REQUIRED: "concept_revision",
    ProjectCreationStatus.CONCEPT_SELECTED: "concept_selected",
    ProjectCreationStatus.COMPLETED: "complete",
    ProjectCreationStatus.REJECTED: "concept_review",
}
_TERMINAL_STATUSES = {
    ProjectCreationStatus.CONCEPT_SELECTED,
    ProjectCreationStatus.COMPLETED,
    ProjectCreationStatus.REJECTED,
}


@dataclass(frozen=True)
class ProjectCreationState:
    """Validated checkpoint data with no creative or user-supplied content.

    The state is intentionally limited to workflow mechanics.  User ideas,
    generated concepts, feedback, prompts, and provider data belong in later,
    separately scoped artifacts and never in this checkpoint contract.
    """

    status: ProjectCreationStatus
    current_node: str
    awaiting_user: bool
    action_request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProjectCreationStatus):
            raise ProjectCreationValidationError("Checkpoint status is not typed.")
        if type(self.awaiting_user) is not bool:
            raise ProjectCreationValidationError("Checkpoint waiting flag is not typed.")
        if self.current_node != _NODES_BY_STATUS[self.status]:
            raise ProjectCreationValidationError("Checkpoint node does not match its status.")
        needs_action = self.status in {
            ProjectCreationStatus.CONCEPT_OPTIONS,
            ProjectCreationStatus.REVISION_REQUIRED,
        }
        if self.awaiting_user != needs_action:
            raise ProjectCreationValidationError(
                "Checkpoint waiting flag does not match its status."
            )
        if needs_action != (self.action_request_id is not None):
            raise ProjectCreationValidationError(
                "Checkpoint action reference does not match its status."
            )
        if self.action_request_id is not None:
            try:
                parsed = UUID(self.action_request_id)
            except (TypeError, ValueError, AttributeError) as error:
                raise ProjectCreationValidationError(
                    "Checkpoint action reference is invalid."
                ) from error
            if str(parsed) != self.action_request_id:
                raise ProjectCreationValidationError(
                    "Checkpoint action reference is not canonical."
                )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def to_checkpoint(self) -> dict[str, object]:
        return {
            "version": 1,
            "status": self.status.value,
            "current_node": self.current_node,
            "awaiting_user": self.awaiting_user,
            "action_request_id": self.action_request_id,
        }

    @classmethod
    def from_checkpoint(cls, payload: object) -> ProjectCreationState:
        if not isinstance(payload, dict) or set(payload) != {
            "version",
            "status",
            "current_node",
            "awaiting_user",
            "action_request_id",
        }:
            raise ProjectCreationValidationError("Checkpoint payload has an unrecognized shape.")
        if (
            type(payload["version"]) is not int
            or payload["version"] != 1
            or not isinstance(payload["status"], str)
        ):
            raise ProjectCreationValidationError(
                "Checkpoint payload has an invalid version or status."
            )
        if not isinstance(payload["current_node"], str) or not isinstance(
            payload["awaiting_user"], bool
        ):
            raise ProjectCreationValidationError("Checkpoint payload has invalid workflow fields.")
        action_request_id = payload["action_request_id"]
        if action_request_id is not None and not isinstance(action_request_id, str):
            raise ProjectCreationValidationError("Checkpoint action reference is invalid.")
        try:
            status = ProjectCreationStatus(payload["status"])
        except ValueError as error:
            raise ProjectCreationValidationError("Checkpoint status is unrecognized.") from error
        return cls(status, payload["current_node"], payload["awaiting_user"], action_request_id)
