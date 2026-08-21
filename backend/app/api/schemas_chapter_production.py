"""Pydantic schemas for chapter-production workflow endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# --- V1 legacy schemas ---


class StartChapterProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveChapterProductionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]


class ChapterProductionActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    options: list[str]
    default_option: str | None
    user_decision: str | None


class ChapterProductionEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    node_name: str | None
    message: str | None
    payload: dict


class ChapterProductionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    current_node: str | None
    next_node: str | None
    awaiting_user: bool
    actions: list[ChapterProductionActionResponse]
    events: list[ChapterProductionEventResponse]
    outline_document_id: UUID | None
    draft_document_id: UUID | None


# --- V2 schemas ---


class ResumeChapterProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TriggerChapterReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinalizeChapterProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReconcileChapterProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveChapterProductionV2ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "accept",
        "request_feedback_revision",
        "submit_manual_edit",
        "proceed_with_warnings",
        "request_review_revision",
        "accept_warning",
        "request_revision",
    ]
    feedback: str | None = None
    target_segment_ids: list[UUID] | None = None
    content: str | None = None
    report_ids: list[UUID] | None = None


class ChapterProductionV2StartedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    workflow_run_id: UUID
    action_request_id: UUID
    outline_document_id: UUID
    outline_version_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID


class ChapterProductionV2UpdatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    workflow_run_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID
    action_request_id: UUID | None = None


class ChapterProductionV2FinalizedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    workflow_run_id: UUID
    final_document_id: UUID
    final_version_id: UUID


class ChapterProductionStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    chapter_workflow_run_id: UUID
    chapter_id: UUID
    status: str
    current_node: str
    awaiting_user: bool
    review_policy_version: str
    chief_editor_required: bool
    document_id: UUID | None = None
    document_version_id: UUID | None = None
    content_hash: str | None = None
    editor_report_id: UUID | None = None
    chief_editor_report_id: UUID | None = None
    lore_report_id: UUID | None = None
    action_request_id: UUID | None = None
    action_kind: str | None = None
    failed_from_status: str | None = None
    failure_code: str | None = None


class ChapterProductionRunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    workflow_run_id: UUID
    project_id: UUID
    chapter_id: UUID
    status: str
    current_node: str | None = None
    started_at: datetime
    updated_at: datetime


__all__ = [
    "ChapterProductionActionResponse",
    "ChapterProductionEventResponse",
    "ChapterProductionRunResponse",
    "ChapterProductionRunSummaryResponse",
    "ChapterProductionStateResponse",
    "ChapterProductionV2FinalizedResponse",
    "ChapterProductionV2StartedResponse",
    "ChapterProductionV2UpdatedResponse",
    "FinalizeChapterProductionRequest",
    "ReconcileChapterProductionRequest",
    "ResolveChapterProductionActionRequest",
    "ResolveChapterProductionV2ActionRequest",
    "ResumeChapterProductionRequest",
    "StartChapterProductionRequest",
    "TriggerChapterReviewRequest",
]
