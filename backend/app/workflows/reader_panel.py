"""Fail-closed, content-free Reader Panel state machine and deterministic contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any
from uuid import UUID


class PanelMode(str, Enum):
    OFF = "off"
    QUICK = "quick"
    STANDARD = "standard"
    PANEL = "panel"


class ReaderPanelStatus(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    INDEPENDENT_READING = "independent_reading"
    INITIAL_REPORTS_LOCKED = "initial_reports_locked"
    ISSUE_EXTRACTION = "issue_extraction"
    INITIAL_BALLOTING = "initial_balloting"
    INITIAL_BALLOTS_LOCKED = "initial_ballots_locked"
    DISCUSSING = "discussing"
    FINAL_BALLOTING = "final_balloting"
    FINAL_BALLOTS_LOCKED = "final_ballots_locked"
    REPORT_GENERATING = "report_generating"
    COMPLETED = "completed"
    DEGRADED_COMPLETED = "degraded_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReaderPanelInvocationPhase(str, Enum):
    INITIAL_READING = "initial_reading"
    ISSUE_EXTRACTION = "issue_extraction"
    INITIAL_BALLOT = "initial_ballot"
    DISCUSSION_TURN = "discussion_turn"
    DISCUSSION_SUMMARY = "discussion_summary"
    FINAL_BALLOT = "final_ballot"
    REPORT_SYNTHESIS = "report_synthesis"


class ReaderPanelInvocationStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN_COMMIT = "unknown_commit"


class ReaderPanelSafeError(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    UNKNOWN_COMMIT = "unknown_commit"


class Severity(str, Enum):
    NONE = "none"
    MINOR = "minor"
    SIGNIFICANT = "significant"
    CRITICAL = "critical"
    ABSTAIN = "abstain"


class SuggestedAction(str, Enum):
    KEEP = "keep"
    CLARIFY = "clarify"
    COMPRESS = "compress"
    EXPAND = "expand"
    MOVE = "move"
    REWRITE_LOCAL = "rewrite_local"
    SPLIT = "split"
    EXPERIMENT_AB = "experiment_ab"
    MANUAL_REVIEW = "manual_review"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConsensusClass(str, Enum):
    STRONG_CONSENSUS = "strong_consensus"
    WEAK_CONSENSUS = "weak_consensus"
    POLARIZED = "polarized"
    ACCEPTED = "accepted"
    INCONCLUSIVE = "inconclusive"


class RiskFlag(str, Enum):
    MINORITY_HIGH_RISK = "minority_high_risk"


class EditorHandoffDecision(str, Enum):
    MUST_FIX = "must_fix"
    EXPERIMENT = "experiment"
    KEEP = "keep"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"


class ReaderPanelValidationError(ValueError):
    """Raised when Reader Panel state machine transitions, configs, or checkpoints are invalid."""


def _canonical_uuid(value: str | UUID | None, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value).lower()
    if not isinstance(value, str):
        raise ReaderPanelValidationError(f"{field_name} must be a valid UUID string.")
    cleaned = value.strip().lower()
    try:
        parsed = UUID(cleaned)
    except ValueError as exc:
        raise ReaderPanelValidationError(f"{field_name} must be a valid canonical UUID.") from exc
    return str(parsed).lower()


_HEX_64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _validate_content_hash(value: str | None, field_name: str = "Source hash") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX_64_RE.match(value.strip()):
        raise ReaderPanelValidationError(
            f"{field_name} must be a valid 64-character hex SHA-256 hash."
        )
    return value.strip().lower()


_TERMINAL_STATUSES = {
    ReaderPanelStatus.COMPLETED,
    ReaderPanelStatus.DEGRADED_COMPLETED,
    ReaderPanelStatus.FAILED,
    ReaderPanelStatus.CANCELLED,
}

_LEGAL_TRANSITIONS: dict[ReaderPanelStatus, set[ReaderPanelStatus]] = {
    ReaderPanelStatus.CREATED: {
        ReaderPanelStatus.PREPARING,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.PREPARING: {
        ReaderPanelStatus.INDEPENDENT_READING,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.INDEPENDENT_READING: {
        ReaderPanelStatus.INITIAL_REPORTS_LOCKED,
        ReaderPanelStatus.DEGRADED_COMPLETED,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.INITIAL_REPORTS_LOCKED: {
        ReaderPanelStatus.ISSUE_EXTRACTION,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.ISSUE_EXTRACTION: {
        ReaderPanelStatus.INITIAL_BALLOTING,
        ReaderPanelStatus.REPORT_GENERATING,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.INITIAL_BALLOTING: {
        ReaderPanelStatus.INITIAL_BALLOTS_LOCKED,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.INITIAL_BALLOTS_LOCKED: {
        ReaderPanelStatus.DISCUSSING,
        ReaderPanelStatus.FINAL_BALLOTING,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.DISCUSSING: {
        ReaderPanelStatus.FINAL_BALLOTING,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.FINAL_BALLOTING: {
        ReaderPanelStatus.FINAL_BALLOTS_LOCKED,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.FINAL_BALLOTS_LOCKED: {
        ReaderPanelStatus.REPORT_GENERATING,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.REPORT_GENERATING: {
        ReaderPanelStatus.COMPLETED,
        ReaderPanelStatus.DEGRADED_COMPLETED,
        ReaderPanelStatus.CANCELLED,
        ReaderPanelStatus.FAILED,
    },
    ReaderPanelStatus.COMPLETED: set(),
    ReaderPanelStatus.DEGRADED_COMPLETED: set(),
    ReaderPanelStatus.FAILED: set(),
    ReaderPanelStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class ReaderPanelConfig:
    mode: PanelMode
    reader_count: int
    reader_profile_ids: list[str]
    max_ballot_issues: int
    max_discussion_issues: int
    max_rounds_per_issue: int
    min_valid_readers: int
    max_total_model_calls: int = 100
    max_model_calls_per_phase: int = 20
    max_total_input_tokens: int = 2_000_000
    max_total_output_tokens: int = 1_000_000
    max_input_tokens_per_call: int = 8192
    max_output_tokens_per_call: int = 4096
    max_messages: int = 200
    max_provider_attempts: int = 3
    max_invalid_output_repairs: int = 1
    max_execution_seconds: int = 3600
    blind_panel: bool = True
    include_transcript: bool = True
    allow_retest: bool = True
    strong_consensus_threshold: float = 0.75
    weak_consensus_threshold: float = 0.60
    polarization_threshold: float = 0.30

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PanelMode):
            object.__setattr__(self, "mode", PanelMode(str(self.mode).lower()))
        integer_fields = {
            "reader_count": self.reader_count,
            "max_ballot_issues": self.max_ballot_issues,
            "max_discussion_issues": self.max_discussion_issues,
            "max_rounds_per_issue": self.max_rounds_per_issue,
            "min_valid_readers": self.min_valid_readers,
            "max_total_model_calls": self.max_total_model_calls,
            "max_model_calls_per_phase": self.max_model_calls_per_phase,
            "max_total_input_tokens": self.max_total_input_tokens,
            "max_total_output_tokens": self.max_total_output_tokens,
            "max_input_tokens_per_call": self.max_input_tokens_per_call,
            "max_output_tokens_per_call": self.max_output_tokens_per_call,
            "max_messages": self.max_messages,
            "max_provider_attempts": self.max_provider_attempts,
            "max_invalid_output_repairs": self.max_invalid_output_repairs,
            "max_execution_seconds": self.max_execution_seconds,
        }
        if any(type(value) is not int for value in integer_fields.values()):
            raise ReaderPanelValidationError("Reader panel numeric budgets must be exact integers.")
        if (
            not isinstance(self.reader_profile_ids, list)
            or len(self.reader_profile_ids) != self.reader_count
            or len(self.reader_profile_ids) != len(set(self.reader_profile_ids))
            or any(not isinstance(item, str) or not item for item in self.reader_profile_ids)
        ):
            raise ReaderPanelValidationError("Reader profiles must match reader_count exactly.")
        if self.min_valid_readers < 0 or (
            self.mode != PanelMode.OFF and self.min_valid_readers == 0
        ):
            raise ReaderPanelValidationError("min_valid_readers must be positive when enabled.")
        if self.reader_count < self.min_valid_readers:
            raise ReaderPanelValidationError("reader_count cannot be less than min_valid_readers.")
        budgets = (
            self.max_total_model_calls,
            self.max_model_calls_per_phase,
            self.max_total_input_tokens,
            self.max_total_output_tokens,
            self.max_input_tokens_per_call,
            self.max_output_tokens_per_call,
            self.max_messages,
            self.max_provider_attempts,
        )
        if self.mode != PanelMode.OFF and any(value <= 0 for value in budgets):
            raise ReaderPanelValidationError("Reader panel hard budgets must be positive.")
        if self.max_invalid_output_repairs != 1:
            raise ReaderPanelValidationError("Invalid structured output allows exactly one repair.")
        if any(
            type(value) is not bool
            for value in (self.blind_panel, self.include_transcript, self.allow_retest)
        ):
            raise ReaderPanelValidationError("Reader panel flags must be booleans.")
        thresholds = (
            self.strong_consensus_threshold,
            self.weak_consensus_threshold,
            self.polarization_threshold,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
            for value in thresholds
        ):
            raise ReaderPanelValidationError("Consensus thresholds must be numeric ratios.")


def get_mode_preset_config(mode: PanelMode | str) -> ReaderPanelConfig:
    if isinstance(mode, str):
        try:
            mode = PanelMode(mode.lower())
        except ValueError as exc:
            raise ReaderPanelValidationError(f"Unknown panel mode: {mode}") from exc

    if mode == PanelMode.OFF:
        return ReaderPanelConfig(
            mode=PanelMode.OFF,
            reader_count=0,
            reader_profile_ids=[],
            max_ballot_issues=0,
            max_discussion_issues=0,
            max_rounds_per_issue=0,
            min_valid_readers=0,
            max_total_model_calls=0,
            max_input_tokens_per_call=0,
            max_execution_seconds=0,
            blind_panel=False,
            include_transcript=False,
            allow_retest=False,
        )
    elif mode == PanelMode.QUICK:
        return ReaderPanelConfig(
            mode=PanelMode.QUICK,
            reader_count=2,
            reader_profile_ids=["general_immersive", "low_patience"],
            max_ballot_issues=3,
            max_discussion_issues=2,
            max_rounds_per_issue=1,
            min_valid_readers=2,
            max_total_model_calls=30,
            max_input_tokens_per_call=8192,
            max_execution_seconds=1200,
            blind_panel=True,
            include_transcript=True,
            allow_retest=True,
            strong_consensus_threshold=0.75,
            weak_consensus_threshold=0.60,
            polarization_threshold=0.30,
        )
    elif mode == PanelMode.STANDARD:
        return ReaderPanelConfig(
            mode=PanelMode.STANDARD,
            reader_count=4,
            reader_profile_ids=[
                "general_immersive",
                "low_patience",
                "character_emotion",
                "genre_experienced",
            ],
            max_ballot_issues=6,
            max_discussion_issues=4,
            max_rounds_per_issue=2,
            min_valid_readers=3,
            max_total_model_calls=80,
            max_input_tokens_per_call=8192,
            max_execution_seconds=2400,
            blind_panel=True,
            include_transcript=True,
            allow_retest=True,
            strong_consensus_threshold=0.75,
            weak_consensus_threshold=0.60,
            polarization_threshold=0.30,
        )
    elif mode == PanelMode.PANEL:
        return ReaderPanelConfig(
            mode=PanelMode.PANEL,
            reader_count=6,
            reader_profile_ids=[
                "general_immersive",
                "low_patience",
                "genre_experienced",
                "character_emotion",
                "style_sensitive",
                "newcomer",
            ],
            max_ballot_issues=8,
            max_discussion_issues=6,
            max_rounds_per_issue=3,
            min_valid_readers=4,
            max_total_model_calls=150,
            max_input_tokens_per_call=12288,
            max_execution_seconds=3600,
            blind_panel=True,
            include_transcript=True,
            allow_retest=True,
            strong_consensus_threshold=0.75,
            weak_consensus_threshold=0.60,
            polarization_threshold=0.30,
        )
    raise ReaderPanelValidationError(f"Unhandled panel mode: {mode}")


def is_mode_off(mode: PanelMode | str) -> bool:
    if isinstance(mode, str):
        return mode.lower() == PanelMode.OFF.value
    return mode == PanelMode.OFF


@dataclass(frozen=True)
class BallotVote:
    reader_id: str
    severity: Severity
    suggested_action: SuggestedAction
    confidence: Confidence | str = Confidence.MEDIUM
    is_target_audience: bool = False
    has_fatal_risk: bool = False
    position_changed: bool = False
    change_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, Severity):
            object.__setattr__(self, "severity", Severity(str(self.severity).lower()))
        if not isinstance(self.suggested_action, SuggestedAction):
            object.__setattr__(
                self, "suggested_action", SuggestedAction(str(self.suggested_action).lower())
            )
        if isinstance(self.confidence, str):
            try:
                object.__setattr__(self, "confidence", Confidence(self.confidence.lower()))
            except ValueError:
                object.__setattr__(self, "confidence", Confidence.MEDIUM)


@dataclass(frozen=True)
class IssueConsensusResult:
    consensus_class: ConsensusClass
    risk_flags: list[RiskFlag]
    total_votes: int
    valid_votes: int
    severity_distribution: dict[Severity, int]
    target_audience_votes: int
    target_audience_distribution: dict[Severity, int]
    recommended_priority: EditorHandoffDecision | str


def classify_issue_consensus(
    votes: list[BallotVote],
    strong_threshold: float = 0.75,
    weak_threshold: float = 0.60,
    polarization_threshold: float = 0.30,
) -> IssueConsensusResult:
    overall_dist: dict[Severity, int] = {s: 0 for s in Severity}
    target_dist: dict[Severity, int] = {s: 0 for s in Severity}

    target_count = 0
    minority_risk = False

    for v in votes:
        overall_dist[v.severity] += 1
        if v.is_target_audience:
            target_count += 1
            target_dist[v.severity] += 1

        # Check minority high risk condition
        if (
            v.severity == Severity.CRITICAL
            and (v.confidence == Confidence.HIGH or v.has_fatal_risk)
        ) or v.has_fatal_risk:
            minority_risk = True

    total = len(votes)
    # Valid non-abstain votes
    valid_count = total - overall_dist[Severity.ABSTAIN]

    if valid_count == 0:
        return IssueConsensusResult(
            consensus_class=ConsensusClass.INCONCLUSIVE,
            risk_flags=[RiskFlag.MINORITY_HIGH_RISK] if minority_risk else [],
            total_votes=total,
            valid_votes=0,
            severity_distribution=overall_dist,
            target_audience_votes=target_count,
            target_audience_distribution=target_dist,
            recommended_priority="manual_review",
        )

    sig_or_crit = overall_dist[Severity.SIGNIFICANT] + overall_dist[Severity.CRITICAL]
    any_issue = sig_or_crit + overall_dist[Severity.MINOR]
    low_issue = overall_dist[Severity.NONE] + overall_dist[Severity.MINOR]

    ratio_sig_crit = sig_or_crit / valid_count
    ratio_any_issue = any_issue / valid_count
    ratio_low = low_issue / valid_count

    consensus_class: ConsensusClass
    if ratio_sig_crit >= strong_threshold:
        consensus_class = ConsensusClass.STRONG_CONSENSUS
    elif ratio_low >= polarization_threshold and ratio_sig_crit >= polarization_threshold:
        consensus_class = ConsensusClass.POLARIZED
    elif ratio_any_issue >= weak_threshold:
        consensus_class = ConsensusClass.WEAK_CONSENSUS
    elif low_issue > sig_or_crit:
        consensus_class = ConsensusClass.ACCEPTED
    else:
        consensus_class = ConsensusClass.INCONCLUSIVE

    risk_flags: list[RiskFlag] = []
    if minority_risk:
        risk_flags.append(RiskFlag.MINORITY_HIGH_RISK)

    # Determine recommended editor priority
    if consensus_class == ConsensusClass.STRONG_CONSENSUS or minority_risk:
        recommended_priority = EditorHandoffDecision.MUST_FIX
    elif consensus_class == ConsensusClass.POLARIZED:
        recommended_priority = EditorHandoffDecision.EXPERIMENT
    elif consensus_class == ConsensusClass.ACCEPTED:
        recommended_priority = EditorHandoffDecision.KEEP
    else:
        recommended_priority = EditorHandoffDecision.MANUAL_REVIEW

    return IssueConsensusResult(
        consensus_class=consensus_class,
        risk_flags=risk_flags,
        total_votes=total,
        valid_votes=valid_count,
        severity_distribution=overall_dist,
        target_audience_votes=target_count,
        target_audience_distribution=target_dist,
        recommended_priority=recommended_priority,
    )


@dataclass(frozen=True)
class ReaderPanelState:
    session_id: str
    project_id: str
    document_id: str
    document_version_id: str
    source_hash: str
    status: ReaderPanelStatus
    config: ReaderPanelConfig
    stale: bool = False
    current_step: str = ""
    step_counter: int = 0
    initial_reports_locked: bool = False
    initial_ballots_locked: bool = False
    final_ballots_locked: bool = False
    extracted_issue_ids: list[str] = field(default_factory=list)
    balloted_issue_ids: list[str] = field(default_factory=list)
    discussed_issue_ids: list[str] = field(default_factory=list)
    active_reader_ids: list[str] = field(default_factory=list)
    failed_reader_ids: list[str] = field(default_factory=list)
    retry_count: int = 0
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _canonical_uuid(self.session_id, "Session ID"))
        object.__setattr__(self, "project_id", _canonical_uuid(self.project_id, "Project ID"))
        object.__setattr__(self, "document_id", _canonical_uuid(self.document_id, "Document ID"))
        object.__setattr__(
            self,
            "document_version_id",
            _canonical_uuid(self.document_version_id, "Document version ID"),
        )
        object.__setattr__(
            self, "source_hash", _validate_content_hash(self.source_hash, "Source hash")
        )
        if not isinstance(self.status, ReaderPanelStatus):
            object.__setattr__(self, "status", ReaderPanelStatus(str(self.status).lower()))

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @classmethod
    def create(
        cls,
        session_id: str,
        project_id: str,
        document_id: str,
        document_version_id: str,
        source_hash: str,
        config: ReaderPanelConfig,
    ) -> ReaderPanelState:
        return cls(
            session_id=session_id,
            project_id=project_id,
            document_id=document_id,
            document_version_id=document_version_id,
            source_hash=source_hash,
            status=ReaderPanelStatus.CREATED,
            config=config,
            active_reader_ids=list(config.reader_profile_ids),
        )

    def transition_to(
        self, new_status: ReaderPanelStatus | str, step_name: str | None = None
    ) -> ReaderPanelState:
        if isinstance(new_status, str):
            try:
                new_status = ReaderPanelStatus(new_status.lower())
            except ValueError as exc:
                raise ReaderPanelValidationError(f"Unknown status: {new_status}") from exc

        if self.is_terminal:
            raise ReaderPanelValidationError(
                f"Terminal state cannot transition: {self.status} -> {new_status}"
            )

        allowed = _LEGAL_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ReaderPanelValidationError(
                f"Illegal state transition: {self.status.value} -> {new_status.value}. Allowed: {[s.value for s in allowed]}"
            )

        new_reports_locked = self.initial_reports_locked or (
            new_status == ReaderPanelStatus.INITIAL_REPORTS_LOCKED
        )
        new_initial_ballots_locked = self.initial_ballots_locked or (
            new_status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED
        )
        new_final_ballots_locked = self.final_ballots_locked or (
            new_status == ReaderPanelStatus.FINAL_BALLOTS_LOCKED
        )

        return replace(
            self,
            status=new_status,
            current_step=step_name or new_status.value,
            step_counter=self.step_counter + 1,
            initial_reports_locked=new_reports_locked,
            initial_ballots_locked=new_initial_ballots_locked,
            final_ballots_locked=new_final_ballots_locked,
        )

    def mark_stale(self) -> ReaderPanelState:
        return replace(self, stale=True)

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "document_id": self.document_id,
            "document_version_id": self.document_version_id,
            "source_hash": self.source_hash,
            "status": self.status.value,
            "stale": self.stale,
            "current_step": self.current_step,
            "step_counter": self.step_counter,
            "initial_reports_locked": self.initial_reports_locked,
            "initial_ballots_locked": self.initial_ballots_locked,
            "final_ballots_locked": self.final_ballots_locked,
            "extracted_issue_ids": list(self.extracted_issue_ids),
            "balloted_issue_ids": list(self.balloted_issue_ids),
            "discussed_issue_ids": list(self.discussed_issue_ids),
            "active_reader_ids": list(self.active_reader_ids),
            "failed_reader_ids": list(self.failed_reader_ids),
            "retry_count": self.retry_count,
            "failure_reason": self.failure_reason,
            "config": {
                "mode": self.config.mode.value,
                "reader_count": self.config.reader_count,
                "reader_profile_ids": list(self.config.reader_profile_ids),
                "max_ballot_issues": self.config.max_ballot_issues,
                "max_discussion_issues": self.config.max_discussion_issues,
                "max_rounds_per_issue": self.config.max_rounds_per_issue,
                "min_valid_readers": self.config.min_valid_readers,
                "max_total_model_calls": self.config.max_total_model_calls,
                "max_model_calls_per_phase": self.config.max_model_calls_per_phase,
                "max_total_input_tokens": self.config.max_total_input_tokens,
                "max_total_output_tokens": self.config.max_total_output_tokens,
                "max_input_tokens_per_call": self.config.max_input_tokens_per_call,
                "max_output_tokens_per_call": self.config.max_output_tokens_per_call,
                "max_messages": self.config.max_messages,
                "max_provider_attempts": self.config.max_provider_attempts,
                "max_invalid_output_repairs": self.config.max_invalid_output_repairs,
                "max_execution_seconds": self.config.max_execution_seconds,
                "blind_panel": self.config.blind_panel,
                "include_transcript": self.config.include_transcript,
                "allow_retest": self.config.allow_retest,
                "strong_consensus_threshold": self.config.strong_consensus_threshold,
                "weak_consensus_threshold": self.config.weak_consensus_threshold,
                "polarization_threshold": self.config.polarization_threshold,
            },
        }

    @classmethod
    def from_checkpoint_dict(cls, data: dict[str, Any]) -> ReaderPanelState:
        if not isinstance(data, dict):
            raise ReaderPanelValidationError("Invalid checkpoint data: must be a dict.")

        required_keys = {
            "session_id",
            "project_id",
            "document_id",
            "document_version_id",
            "source_hash",
            "status",
            "config",
        }
        missing = required_keys - data.keys()
        if missing:
            raise ReaderPanelValidationError(
                f"Invalid checkpoint: missing required fields: {missing}"
            )

        raw_config = data["config"]
        if not isinstance(raw_config, dict):
            raise ReaderPanelValidationError("Invalid checkpoint config: must be a dict.")

        config_keys = {
            "mode",
            "reader_count",
            "reader_profile_ids",
            "max_ballot_issues",
            "max_discussion_issues",
            "max_rounds_per_issue",
            "min_valid_readers",
            "max_total_model_calls",
            "max_model_calls_per_phase",
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_input_tokens_per_call",
            "max_output_tokens_per_call",
            "max_messages",
            "max_provider_attempts",
            "max_invalid_output_repairs",
            "max_execution_seconds",
            "blind_panel",
            "include_transcript",
            "allow_retest",
            "strong_consensus_threshold",
            "weak_consensus_threshold",
            "polarization_threshold",
        }
        if set(raw_config) != config_keys:
            raise ReaderPanelValidationError("Invalid checkpoint config shape.")

        try:
            config = ReaderPanelConfig(
                mode=PanelMode(raw_config["mode"]),
                reader_count=raw_config["reader_count"],
                reader_profile_ids=raw_config["reader_profile_ids"],
                max_ballot_issues=raw_config["max_ballot_issues"],
                max_discussion_issues=raw_config["max_discussion_issues"],
                max_rounds_per_issue=raw_config["max_rounds_per_issue"],
                min_valid_readers=raw_config["min_valid_readers"],
                max_total_model_calls=raw_config["max_total_model_calls"],
                max_model_calls_per_phase=raw_config["max_model_calls_per_phase"],
                max_total_input_tokens=raw_config["max_total_input_tokens"],
                max_total_output_tokens=raw_config["max_total_output_tokens"],
                max_input_tokens_per_call=raw_config["max_input_tokens_per_call"],
                max_output_tokens_per_call=raw_config["max_output_tokens_per_call"],
                max_messages=raw_config["max_messages"],
                max_provider_attempts=raw_config["max_provider_attempts"],
                max_invalid_output_repairs=raw_config["max_invalid_output_repairs"],
                max_execution_seconds=raw_config["max_execution_seconds"],
                blind_panel=raw_config["blind_panel"],
                include_transcript=raw_config["include_transcript"],
                allow_retest=raw_config["allow_retest"],
                strong_consensus_threshold=raw_config["strong_consensus_threshold"],
                weak_consensus_threshold=raw_config["weak_consensus_threshold"],
                polarization_threshold=raw_config["polarization_threshold"],
            )
            return cls(
                session_id=data["session_id"],
                project_id=data["project_id"],
                document_id=data["document_id"],
                document_version_id=data["document_version_id"],
                source_hash=data["source_hash"],
                status=ReaderPanelStatus(data["status"]),
                config=config,
                stale=bool(data.get("stale", False)),
                current_step=str(data.get("current_step", "")),
                step_counter=int(data.get("step_counter", 0)),
                initial_reports_locked=bool(data.get("initial_reports_locked", False)),
                initial_ballots_locked=bool(data.get("initial_ballots_locked", False)),
                final_ballots_locked=bool(data.get("final_ballots_locked", False)),
                extracted_issue_ids=list(data.get("extracted_issue_ids", [])),
                balloted_issue_ids=list(data.get("balloted_issue_ids", [])),
                discussed_issue_ids=list(data.get("discussed_issue_ids", [])),
                active_reader_ids=list(data.get("active_reader_ids", [])),
                failed_reader_ids=list(data.get("failed_reader_ids", [])),
                retry_count=int(data.get("retry_count", 0)),
                failure_reason=data.get("failure_reason"),
            )
        except Exception as exc:
            raise ReaderPanelValidationError(f"Invalid checkpoint data: {exc}") from exc
