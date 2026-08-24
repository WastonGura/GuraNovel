"""Strict HTTP schemas for the bounded Reader Panel API."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.agents.reader_panel_contracts import (
    ConcernItem,
    ConsensusClass,
    DiscussionStatus,
    EditorialDecision,
    EvidenceRef,
    ReactionItem,
    StrengthItem,
    SuggestedAction,
    TargetAudienceRelevance,
    validate_reader_panel_text,
)
from app.workflows.reader_panel import PanelMode, get_mode_preset_config


BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
IdempotencyKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9:_-]+$",
    ),
]
SegmentId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class ReaderPanelConfigOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    max_ballot_issues: int | None = Field(default=None, ge=1, le=8)
    max_discussion_issues: int | None = Field(default=None, ge=0, le=6)
    max_rounds_per_issue: int | None = Field(default=None, ge=0, le=3)
    min_valid_readers: int | None = Field(default=None, ge=1, le=6)


class ReaderPanelStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    document_version_id: UUID
    mode: Literal["off", "quick", "standard", "panel"] = "standard"
    config_overrides: ReaderPanelConfigOverrides | None = None
    test_goals: list[BoundedText] = Field(default_factory=list, max_length=16)
    target_audience: list[BoundedText] = Field(default_factory=list, max_length=16)
    idempotency_key: IdempotencyKey | None = None

    @model_validator(mode="after")
    def validate_combined_config(self) -> ReaderPanelStartRequest:
        config = get_mode_preset_config(PanelMode(self.mode))
        if self.config_overrides is not None:
            config = replace(config, **self.config_overrides.model_dump(exclude_none=True))
        if (
            config.min_valid_readers > config.reader_count
            or config.max_discussion_issues > config.max_ballot_issues
        ):
            raise ValueError("invalid reader panel config combination")
        return self


class ReaderPanelEmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReaderPanelActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    priority: EditorialDecision
    target_segment_ids: list[SegmentId] = Field(min_length=1, max_length=16)
    suggested_action: SuggestedAction
    instruction: str = Field(min_length=1, max_length=2000)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        return validate_reader_panel_text(value, "instruction", max_bytes=4000)


class ReaderPanelReviewReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    summary: str = Field(min_length=1, max_length=4000)
    blocking_issues: list[ReaderPanelBlockingIssueResponse] = Field(
        default_factory=list, max_length=16
    )
    warnings: list[str] = Field(default_factory=list, max_length=32)
    notes: list[str] = Field(default_factory=list, max_length=16)
    suggested_actions: list[ReaderPanelActionResponse] = Field(default_factory=list, max_length=16)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return validate_reader_panel_text(value, "summary", max_bytes=8000)

    @field_validator("warnings", "notes")
    @classmethod
    def validate_text_lists(cls, values: list[str]) -> list[str]:
        return [
            validate_reader_panel_text(value, "report_text", max_bytes=2000) for value in values
        ]


class ReaderPanelBlockingIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    issue_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=256)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return validate_reader_panel_text(value, "issue_title", max_bytes=1000)


class ReaderPanelInitialReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    overall_reaction: str = Field(min_length=1, max_length=2000)
    continue_reading: Literal["yes", "maybe", "no"]
    confidence: Literal["low", "medium", "high"]
    strengths: list[StrengthItem] = Field(default_factory=list, max_length=16)
    reactions: list[ReactionItem] = Field(default_factory=list, max_length=32)
    concerns: list[ConcernItem] = Field(default_factory=list, max_length=16)

    @field_validator("overall_reaction")
    @classmethod
    def validate_overall_reaction(cls, value: str) -> str:
        return validate_reader_panel_text(value, "overall_reaction", max_bytes=4000)


class ReaderPanelMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    issue_id: UUID
    round_number: int = Field(ge=1)
    turn_number: int = Field(ge=1)
    speaker_type: Literal["reader", "moderator"]
    stance: Literal["support", "oppose", "mixed", "abstain"] | None = None
    claim: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    concession: str | None = Field(default=None, max_length=1000)
    proposed_action: str | None = Field(default=None, max_length=500)
    novelty: Literal["new_evidence", "new_interpretation", "repetition", "procedural"]
    created_at: datetime | None = None

    @field_validator("claim")
    @classmethod
    def validate_claim(cls, value: str) -> str:
        return validate_reader_panel_text(value, "claim", max_bytes=4000)

    @field_validator("concession", "proposed_action")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_reader_panel_text(value, "message_text", max_bytes=2000)

    @model_validator(mode="after")
    def validate_speaker_stance(self) -> ReaderPanelMessageResponse:
        if (self.speaker_type == "reader") != (self.stance is not None):
            raise ValueError("stance must be present only for reader turns")
        return self


class ReaderPanelIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    issue_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=64)
    symptom: str = Field(min_length=1, max_length=1000)
    root_cause_hypotheses: list[str] = Field(min_length=1, max_length=8)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    target_audience_relevance: TargetAudienceRelevance
    minority_risk: bool
    discussion_status: DiscussionStatus
    consensus_class: ConsensusClass | None = None
    recommended_priority: EditorialDecision | None = None

    @field_validator("title", "category", "symptom")
    @classmethod
    def validate_issue_text(cls, value: str) -> str:
        return validate_reader_panel_text(value, "issue_text", max_bytes=2000)

    @field_validator("root_cause_hypotheses")
    @classmethod
    def validate_hypotheses(cls, values: list[str]) -> list[str]:
        return [validate_reader_panel_text(value, "hypothesis", max_bytes=1000) for value in values]


class ReaderPanelDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    session_id: UUID | None = None
    workflow_run_id: UUID | None = None
    project_id: UUID
    chapter_id: UUID
    document_id: UUID
    document_version_id: UUID
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mode: Literal["off", "quick", "standard", "panel"]
    status: str
    is_noop: bool = False
    stale: bool = False
    degradation_reason: str | None = None
    failure_reason: str | None = None
    planned_readers: int = Field(default=0, ge=0)
    completed_readers: int = Field(default=0, ge=0)
    failed_readers: int = Field(default=0, ge=0)
    issue_count: int = Field(default=0, ge=0)
    initial_ballot_count: int = Field(default=0, ge=0)
    final_ballot_count: int = Field(default=0, ge=0)
    discussion_message_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("discussion_message_count", "message_count"),
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    review_report: ReaderPanelReviewReportResponse | None = None
    issues: list[ReaderPanelIssueResponse] = Field(default_factory=list, max_length=16)
    initial_reports: list[ReaderPanelInitialReportResponse] | None = None
    transcript: list[ReaderPanelMessageResponse] | None = Field(
        default=None,
        validation_alias=AliasChoices("transcript", "discussion_transcript"),
    )
    permitted_operations: list[Literal["cancel", "resume"]] = Field(default_factory=list)
