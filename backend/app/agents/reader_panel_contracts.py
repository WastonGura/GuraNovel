"""Strict provider-neutral contracts for Reader Panel agents and moderation."""

from __future__ import annotations

from enum import StrEnum
import re
import unicodedata
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)



_IDENTIFIER = re.compile(r"[a-z0-9_][a-z0-9_-]{0,63}")
_SEGMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_CREDENTIAL_MATERIAL = re.compile(
    r"(?:\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|secret|token)\b"
    r"\s*[:=]\s*\S+|\bsk-[a-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATH = re.compile(r"(?:^|[^a-z0-9])[a-z]:[\\/]", re.IGNORECASE)
_UNIX_SYSTEM_PATH = re.compile(r"(?:^|[\s\"'])/(?:etc|var|usr|bin|sbin|root|home|tmp|opt|dev|proc)(?:/|\b)")
_EXTERNAL_URI_SCHEME = re.compile(r"(?:^|[^a-z0-9])(?:https?|ftp|file|gopher)://\S+", re.IGNORECASE)
_RAW_EXCEPTION_MATERIAL = re.compile(
    r"(?:\b(?:Traceback\s*\(most\s+recent\s+call\s+last\)|ZeroDivisionError|KeyError|AttributeError|ValueError|TypeError|RuntimeError|OperationalError)\b|"
    r"\bException\s*:|\bError\s*:)",
    re.IGNORECASE,
)


def _canonical_uuid(value: object) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("invalid UUID reference") from None
    if parsed.int == 0 or str(parsed) != str(value).lower():
        raise ValueError("UUID must be non-zero and canonical lowercase")
    return parsed


def validate_reader_panel_text(value: str, label: str = "text", *, max_bytes: int = 65536) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-blank string")
    if len(stripped.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds maximum allowed size of {max_bytes} bytes")
    for char in stripped:
        if unicodedata.category(char) == "Cc" and char not in "\t\n\r":
            raise ValueError(f"{label} contains illegal control characters")
    if _CREDENTIAL_MATERIAL.search(stripped):
        raise ValueError(f"{label} contains forbidden material: credentials/tokens")
    if _WINDOWS_DRIVE_PATH.search(stripped) or _UNIX_SYSTEM_PATH.search(stripped):
        raise ValueError(f"{label} contains forbidden material: filesystem paths")
    if _EXTERNAL_URI_SCHEME.search(stripped):
        raise ValueError(f"{label} contains forbidden material: external URIs")
    if _RAW_EXCEPTION_MATERIAL.search(stripped):
        raise ValueError(f"{label} contains forbidden material: raw exception traces")
    return stripped


class _StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Severity(StrEnum):
    NONE = "none"
    MINOR = "minor"
    SIGNIFICANT = "significant"
    CRITICAL = "critical"
    ABSTAIN = "abstain"


class SuggestedAction(StrEnum):
    KEEP = "keep"
    CLARIFY = "clarify"
    COMPRESS = "compress"
    EXPAND = "expand"
    MOVE = "move"
    REWRITE_LOCAL = "rewrite_local"
    SPLIT = "split"
    EXPERIMENT_AB = "experiment_ab"
    MANUAL_REVIEW = "manual_review"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ContinueReadingVote(StrEnum):
    YES = "yes"
    MAYBE = "maybe"
    NO = "no"


class DiscussionStance(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    MIXED = "mixed"
    ABSTAIN = "abstain"


class DiscussionNovelty(StrEnum):
    NEW_EVIDENCE = "new_evidence"
    NEW_INTERPRETATION = "new_interpretation"
    REPETITION = "repetition"
    PROCEDURAL = "procedural"


class TargetAudienceRelevance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConsensusClass(StrEnum):
    STRONG_CONSENSUS = "strong_consensus"
    WEAK_CONSENSUS = "weak_consensus"
    POLARIZED = "polarized"
    ACCEPTED = "accepted"
    INCONCLUSIVE = "inconclusive"


class EditorialDecision(StrEnum):
    MUST_FIX = "must_fix"
    EXPERIMENT = "experiment"
    KEEP = "keep"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"


class DiscussionStatus(StrEnum):
    QUEUED = "queued"
    DISCUSSING = "discussing"
    CLOSED = "closed"
    SKIPPED = "skipped"


class SpeakerType(StrEnum):
    READER = "reader"
    MODERATOR = "moderator"


class EvidenceRef(_StrictContractModel):
    segment_ids: list[str] = Field(min_length=1, max_length=16)
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        return validate_reader_panel_text(value, "note", max_bytes=2000)

    @field_validator("segment_ids")
    @classmethod
    def validate_segments(cls, value: list[str]) -> list[str]:
        cleaned = []
        for sid in value:
            sid_clean = sid.strip()
            if not sid_clean or not _SEGMENT_ID.fullmatch(sid_clean):
                raise ValueError("invalid segment identifier")
            cleaned.append(sid_clean)
        return cleaned


class StrengthItem(_StrictContractModel):
    summary: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return validate_reader_panel_text(value, "summary", max_bytes=1000)


class ReactionItem(_StrictContractModel):
    segment_ids: list[str] = Field(default_factory=list, max_length=16)
    reaction: str = Field(min_length=1, max_length=1000)
    emotion: str | None = Field(default=None, max_length=64)
    confusion: str | None = Field(default=None, max_length=500)

    @field_validator("reaction")
    @classmethod
    def validate_reaction(cls, value: str) -> str:
        return validate_reader_panel_text(value, "reaction", max_bytes=2000)

    @field_validator("emotion", "confusion")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_reader_panel_text(value, "reaction_detail", max_bytes=1000)


class ConcernItem(_StrictContractModel):
    category: str = Field(min_length=1, max_length=64)
    symptom: str = Field(min_length=1, max_length=1000)
    severity: Severity
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    suggested_action: SuggestedAction | None = None

    @field_validator("category", "symptom")
    @classmethod
    def validate_concern_text(cls, value: str) -> str:
        return validate_reader_panel_text(value, "concern_field", max_bytes=2000)


class ExtractedIssueItem(_StrictContractModel):
    issue_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=64)
    symptom: str = Field(min_length=1, max_length=1000)
    root_cause_hypotheses: list[str] = Field(min_length=1, max_length=8)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    source_reader_ids: list[str] = Field(default_factory=list, max_length=16)
    target_audience_relevance: TargetAudienceRelevance = TargetAudienceRelevance.MEDIUM
    minority_risk: bool = False
    discussion_status: DiscussionStatus = DiscussionStatus.QUEUED

    @field_validator("title", "category", "symptom")
    @classmethod
    def validate_issue_text(cls, value: str) -> str:
        return validate_reader_panel_text(value, "issue_field", max_bytes=2000)

    @field_validator("root_cause_hypotheses")
    @classmethod
    def validate_hypotheses(cls, value: list[str]) -> list[str]:
        return [validate_reader_panel_text(h, "hypothesis", max_bytes=1000) for h in value]


class KeyFindingItem(_StrictContractModel):
    issue_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=256)
    consensus_class: ConsensusClass
    recommended_priority: EditorialDecision
    summary: str = Field(min_length=1, max_length=1000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)

    @field_validator("title", "summary")
    @classmethod
    def validate_finding_text(cls, value: str) -> str:
        return validate_reader_panel_text(value, "finding_field", max_bytes=2000)


class ActionableRecommendationItem(_StrictContractModel):
    priority: EditorialDecision
    target_segment_ids: list[str] = Field(min_length=1, max_length=16)
    suggested_action: SuggestedAction
    instruction: str = Field(min_length=1, max_length=2000)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        return validate_reader_panel_text(value, "instruction", max_bytes=4000)


# -----------------------------------------------------------------------------
# 1. Cold-Read Initial Reading
# -----------------------------------------------------------------------------

class ReaderInitialReadingRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    reader_profile_id: str = Field(min_length=1, max_length=64)
    genre: str = Field(min_length=1, max_length=64)
    target_audience: list[str] = Field(default_factory=list, max_length=16)
    manuscript_segments: dict[str, str] = Field(min_length=1)
    test_goals: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)

    @field_validator("reader_profile_id", "genre")
    @classmethod
    def validate_names(cls, value: str) -> str:
        return validate_reader_panel_text(value, "request_field", max_bytes=256)


class ReaderInitialReadingOutput(_StrictContractModel):
    overall_reaction: str = Field(min_length=1, max_length=2000)
    continue_reading: ContinueReadingVote
    confidence: Confidence
    strengths: list[StrengthItem] = Field(default_factory=list, max_length=16)
    reactions: list[ReactionItem] = Field(default_factory=list, max_length=32)
    concerns: list[ConcernItem] = Field(default_factory=list, max_length=16)

    @field_validator("overall_reaction")
    @classmethod
    def validate_overall(cls, value: str) -> str:
        return validate_reader_panel_text(value, "overall_reaction", max_bytes=4000)


# -----------------------------------------------------------------------------
# 2. Issue Extraction (Moderator)
# -----------------------------------------------------------------------------

class ModeratorIssueExtractionRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    reader_initial_reports: dict[str, dict] = Field(min_length=1)
    manuscript_segments: dict[str, str] = Field(min_length=1)
    max_ballot_issues: int = Field(ge=1, le=16, default=8)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)


class ModeratorIssueExtractionOutput(_StrictContractModel):
    issues: list[ExtractedIssueItem] = Field(default_factory=list, max_length=16)


# -----------------------------------------------------------------------------
# 3. Blind Initial Ballot (Reader)
# -----------------------------------------------------------------------------

class ReaderBlindBallotRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    reader_profile_id: str = Field(min_length=1, max_length=64)
    issue: ExtractedIssueItem
    manuscript_segments: dict[str, str] = Field(min_length=1)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)


class ReaderBallotOutput(_StrictContractModel):
    issue_number: int = Field(ge=1)
    severity: Severity
    suggested_action: SuggestedAction
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return validate_reader_panel_text(value, "reason", max_bytes=4000)


# -----------------------------------------------------------------------------
# 4. Discussion Turn (Reader & Moderator)
# -----------------------------------------------------------------------------

class ReaderDiscussionTurnRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    reader_profile_id: str = Field(min_length=1, max_length=64)
    issue: ExtractedIssueItem
    round_number: int = Field(ge=1, le=10)
    turn_number: int = Field(ge=1, le=50)
    prior_messages: list[dict] = Field(default_factory=list, max_length=100)
    prior_ballot: dict | None = None
    manuscript_segments: dict[str, str] = Field(min_length=1)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)


class ReaderDiscussionTurnOutput(_StrictContractModel):
    stance: DiscussionStance
    claim: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    concession: str | None = Field(default=None, max_length=1000)
    proposed_action: str | None = Field(default=None, max_length=500)
    novelty: DiscussionNovelty = DiscussionNovelty.NEW_INTERPRETATION

    @field_validator("claim")
    @classmethod
    def validate_claim(cls, value: str) -> str:
        return validate_reader_panel_text(value, "claim", max_bytes=4000)

    @field_validator("concession", "proposed_action")
    @classmethod
    def validate_optional_turn_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_reader_panel_text(value, "turn_detail", max_bytes=2000)


class ModeratorDiscussionSummaryRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    issue: ExtractedIssueItem
    round_number: int = Field(ge=1, le=10)
    round_messages: list[dict] = Field(default_factory=list, max_length=50)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)


class ModeratorDiscussionSummaryOutput(_StrictContractModel):
    round_summary: str = Field(min_length=1, max_length=2000)
    remaining_disagreements: list[str] = Field(default_factory=list, max_length=8)
    suggested_focus: str = Field(min_length=1, max_length=1000)
    is_consensus_reached: bool = False

    @field_validator("round_summary", "suggested_focus")
    @classmethod
    def validate_summary_text(cls, value: str) -> str:
        return validate_reader_panel_text(value, "summary_text", max_bytes=4000)

    @field_validator("remaining_disagreements")
    @classmethod
    def validate_disagreements(cls, value: list[str]) -> list[str]:
        return [validate_reader_panel_text(d, "disagreement", max_bytes=1000) for d in value]


# -----------------------------------------------------------------------------
# 5. Final Ballot (Reader)
# -----------------------------------------------------------------------------

class ReaderFinalBallotRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    reader_profile_id: str = Field(min_length=1, max_length=64)
    issue: ExtractedIssueItem
    round_summaries: list[str] = Field(default_factory=list, max_length=10)
    initial_ballot: dict | None = None
    manuscript_segments: dict[str, str] = Field(min_length=1)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)


class ReaderFinalBallotOutput(_StrictContractModel):
    issue_number: int = Field(ge=1)
    severity: Severity
    suggested_action: SuggestedAction
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    position_changed: bool = False
    change_reason: str | None = Field(default=None, max_length=1000)
    remaining_disagreement: str | None = Field(default=None, max_length=1000)

    @field_validator("change_reason", "remaining_disagreement")
    @classmethod
    def validate_ballot_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_reader_panel_text(value, "ballot_detail", max_bytes=2000)


# -----------------------------------------------------------------------------
# 6. Report Synthesis (Moderator)
# -----------------------------------------------------------------------------

class ModeratorReportSynthesisRequest(_StrictContractModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    initial_reports: dict[str, dict] = Field(min_length=1)
    extracted_issues: list[ExtractedIssueItem] = Field(default_factory=list)
    final_consensus_results: dict[int, dict] = Field(default_factory=dict)
    minority_risk_issues: list[int] = Field(default_factory=list)

    @field_validator("project_id", "chapter_id", "workflow_run_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> UUID:
        return _canonical_uuid(value)


class ModeratorReportSynthesisOutput(_StrictContractModel):
    executive_summary: str = Field(min_length=1, max_length=4000)
    target_audience_appeal: str = Field(min_length=1, max_length=2000)
    key_findings: list[KeyFindingItem] = Field(default_factory=list, max_length=16)
    actionable_recommendations: list[ActionableRecommendationItem] = Field(default_factory=list, max_length=16)

    @field_validator("executive_summary", "target_audience_appeal")
    @classmethod
    def validate_report_text(cls, value: str) -> str:
        return validate_reader_panel_text(value, "report_text", max_bytes=8000)
