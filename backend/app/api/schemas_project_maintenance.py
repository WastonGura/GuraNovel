"""Strict public contracts for project-maintenance lifecycle endpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


MaintenanceTitle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=512,
        pattern=r"^[^\r\n]+$",
    ),
]
MaintenanceChangeRequest = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]
MaintenanceScopeHint = Literal[
    "chapter",
    "character",
    "world",
    "outline",
    "foreshadowing",
    "timeline",
    "style",
]
MaintenanceDecisionValue = Literal["approve", "revise", "cancel", "accept_warning"]


class StartProjectMaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: MaintenanceTitle
    change_request: MaintenanceChangeRequest
    scope_hints: list[MaintenanceScopeHint] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def unique_scope_hints(self) -> "StartProjectMaintenanceRequest":
        if len(set(self.scope_hints)) != len(self.scope_hints):
            raise ValueError("duplicate maintenance scope hint")
        return self


class ResolveProjectMaintenanceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: MaintenanceDecisionValue


class ProjectMaintenanceAffectedItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    position: int
    type: MaintenanceScopeHint
    stable_reference: str
    impact_level: Literal["low", "medium", "high"]
    reason: str
    document_id: UUID | None
    chapter_id: UUID | None


class ProjectMaintenanceRevisionOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    sequence: int
    operation: Literal["revise", "retain"]
    document_id: UUID
    expected_version_id: UUID
    affected_item_ids: tuple[UUID, ...]
    instruction: str


class ProjectMaintenanceRevisionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    document_id: UUID
    version_id: UUID
    review_outcome: Literal["passed", "warning", "blocking"]
    summary: str
    operations: tuple[ProjectMaintenanceRevisionOperationResponse, ...]


class ProjectMaintenanceConsistencyDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    document_id: UUID
    version_id: UUID


class ProjectMaintenanceConsistencyFindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    sequence: int
    code: str
    severity: Literal["warning", "blocking"]
    blocking: bool
    affected_documents: tuple[ProjectMaintenanceConsistencyDocumentResponse, ...]
    suggested_corrective_action: str


class ProjectMaintenanceConsistencyReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    outcome: Literal["clean", "warning", "blocking"]
    findings: tuple[ProjectMaintenanceConsistencyFindingResponse, ...]


class ProjectMaintenancePendingActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    type: Literal[
        "project_maintenance_revision_confirmation",
        "project_maintenance_consistency_warning",
    ]
    status: Literal["pending"]
    confirmation_kind: Literal["revision_confirmation", "consistency_warning"]
    review_outcome: Literal["passed", "warning", "blocking"]
    allowed_decisions: tuple[MaintenanceDecisionValue, ...]


class ProjectMaintenanceRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    maintenance_change_id: UUID
    type: Literal["project_maintenance"]
    status: Literal[
        "CHANGE_REQUESTED",
        "LORE_IMPACT_ANALYSIS",
        "CHIEF_EDITOR_IMPACT_ANALYSIS",
        "REVISION_PLAN",
        "USER_CONFIRMATION",
        "APPLY_CHANGE",
        "CONSISTENCY_REVIEW",
        "PROJECT_UPDATED",
        "CANCELLED",
    ]
    current_node: str | None
    next_node: None
    awaiting_user: bool
    title: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    affected_items: tuple[ProjectMaintenanceAffectedItemResponse, ...]
    revision_plan: ProjectMaintenanceRevisionPlanResponse | None
    consistency_review: ProjectMaintenanceConsistencyReviewResponse | None
    applied_document_version_ids: tuple[UUID, ...]
    pending_action: ProjectMaintenancePendingActionResponse | None


__all__ = [
    "ProjectMaintenanceRunResponse",
    "ResolveProjectMaintenanceActionRequest",
    "StartProjectMaintenanceRequest",
]
