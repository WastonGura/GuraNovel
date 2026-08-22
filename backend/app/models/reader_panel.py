"""Reader-panel persistence mappings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.core import Chapter, Document, DocumentVersion, Project, TimestampMixin, WorkflowRun
from app.workflows.reader_panel import (
    Confidence,
    ConsensusClass,
    EditorHandoffDecision,
    PanelMode,
    ReaderPanelStatus,
    Severity,
    SuggestedAction,
)


def _sql_values(enum_type: type) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_type)


class ReaderPanelSession(TimestampMixin, Base):
    """One durable reader panel run bound to exactly one workflow run."""

    __tablename__ = "reader_panel_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "workflow_run_id"],
            ["workflow_runs.project_id", "workflow_runs.id"],
            name="fk_reader_panel_sessions_project_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workflow_run_id", name="uq_reader_panel_sessions_run_id"),
        CheckConstraint(
            f"mode IN ({_sql_values(PanelMode)})",
            name="ck_reader_panel_sessions_mode",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(ReaderPanelStatus)})",
            name="ck_reader_panel_sessions_status",
        ),
        CheckConstraint(
            "btrim(source_hash) <> '' AND char_length(source_hash) = 64",
            name="ck_reader_panel_sessions_source_hash_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(config_snapshot) = 'object'",
            name="ck_reader_panel_sessions_config_snapshot_object",
        ),
        CheckConstraint(
            "jsonb_typeof(model_snapshot) = 'object'",
            name="ck_reader_panel_sessions_model_snapshot_object",
        ),
        CheckConstraint(
            "jsonb_typeof(prompt_snapshot) = 'object'",
            name="ck_reader_panel_sessions_prompt_snapshot_object",
        ),
        CheckConstraint(
            "jsonb_typeof(target_audience) = 'array'",
            name="ck_reader_panel_sessions_target_audience_array",
        ),
        CheckConstraint(
            "jsonb_typeof(test_goals) = 'array'",
            name="ck_reader_panel_sessions_test_goals_array",
        ),
        CheckConstraint(
            "step_counter >= 0",
            name="ck_reader_panel_sessions_step_counter_non_negative",
        ),
        Index("idx_reader_panel_sessions_project_id", "project_id"),
        Index("idx_reader_panel_sessions_chapter_id", "chapter_id"),
        Index("idx_reader_panel_sessions_project_status", "project_id", "status"),
        Index("idx_reader_panel_sessions_created_at", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
    )
    workflow_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    document_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default=PanelMode.STANDARD.value)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=ReaderPanelStatus.CREATED.value,
        server_default=text(f"'{ReaderPanelStatus.CREATED.value}'"),
    )
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    config_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    model_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    prompt_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    target_audience: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    test_goals: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    step_counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    current_step: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    degradation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    initial_reports_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    initial_ballots_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_ballots_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_report_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("review_reports.id", ondelete="SET NULL"),
        nullable=True,
    )

    project: Mapped[Project] = relationship(
        back_populates="reader_panel_sessions", foreign_keys=[project_id]
    )
    chapter: Mapped[Chapter | None] = relationship(back_populates="reader_panel_sessions")
    workflow_run: Mapped[WorkflowRun] = relationship(
        back_populates="reader_panel_session",
        foreign_keys=[project_id, workflow_run_id],
        overlaps="project,reader_panel_sessions",
    )
    document: Mapped[Document | None] = relationship(foreign_keys=[document_id])
    document_version: Mapped[DocumentVersion | None] = relationship(foreign_keys=[document_version_id])

    reader_runs: Mapped[list[ReaderRun]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    initial_reports: Mapped[list[ReaderInitialReport]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    issues: Mapped[list[ReaderPanelIssue]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    ballots: Mapped[list[ReaderPanelBallot]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[ReaderPanelMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class ReaderRun(TimestampMixin, Base):
    """One individual reader agent run within a ReaderPanelSession."""

    __tablename__ = "reader_runs"
    __table_args__ = (
        UniqueConstraint("session_id", "reader_profile_id", name="uq_reader_runs_session_profile"),
        CheckConstraint(
            "btrim(reader_profile_id) <> '' AND char_length(reader_profile_id) <= 64",
            name="ck_reader_runs_profile_id",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'invalid')",
            name="ck_reader_runs_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_reader_runs_retry_count_non_negative"),
        Index("idx_reader_runs_session_id", "session_id"),
        Index("idx_reader_runs_profile", "session_id", "reader_profile_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    reader_profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default=text("'pending'"))
    is_target_audience: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[ReaderPanelSession] = relationship(back_populates="reader_runs")
    initial_report: Mapped[ReaderInitialReport | None] = relationship(
        back_populates="reader_run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    ballots: Mapped[list[ReaderPanelBallot]] = relationship(
        back_populates="reader_run",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[ReaderPanelMessage]] = relationship(
        back_populates="reader_run",
    )


class ReaderInitialReport(Base):
    """Immutable blind initial reading report generated by one reader."""

    __tablename__ = "reader_initial_reports"
    __table_args__ = (
        UniqueConstraint("reader_run_id", name="uq_reader_initial_reports_run_id"),
        CheckConstraint(
            "continue_reading IN ('yes', 'maybe', 'no')",
            name="ck_reader_initial_reports_continue_reading",
        ),
        CheckConstraint(
            f"confidence IN ({_sql_values(Confidence)})",
            name="ck_reader_initial_reports_confidence",
        ),
        CheckConstraint(
            "jsonb_typeof(strengths) = 'array'",
            name="ck_reader_initial_reports_strengths_array",
        ),
        CheckConstraint(
            "jsonb_typeof(reactions) = 'array'",
            name="ck_reader_initial_reports_reactions_array",
        ),
        CheckConstraint(
            "jsonb_typeof(concerns) = 'array'",
            name="ck_reader_initial_reports_concerns_array",
        ),
        Index("idx_reader_initial_reports_session_id", "session_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    reader_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    overall_reaction: Mapped[str] = mapped_column(Text, nullable=False)
    continue_reading: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False, default=Confidence.MEDIUM.value)
    strengths: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    reactions: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    concerns: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    reader_run: Mapped[ReaderRun] = relationship(back_populates="initial_report")
    session: Mapped[ReaderPanelSession] = relationship(back_populates="initial_reports")


class ReaderPanelIssue(TimestampMixin, Base):
    """Normalized, deduplicated chapter issue extracted from initial reports."""

    __tablename__ = "reader_panel_issues"
    __table_args__ = (
        UniqueConstraint("session_id", "issue_number", name="uq_reader_panel_issues_session_number"),
        CheckConstraint("issue_number >= 1", name="ck_reader_panel_issues_number_positive"),
        CheckConstraint(
            "btrim(title) <> '' AND char_length(title) <= 256",
            name="ck_reader_panel_issues_title",
        ),
        CheckConstraint("btrim(category) <> ''", name="ck_reader_panel_issues_category"),
        CheckConstraint("btrim(symptom) <> ''", name="ck_reader_panel_issues_symptom"),
        CheckConstraint(
            "jsonb_typeof(root_cause_hypotheses) = 'array'",
            name="ck_reader_panel_issues_hypotheses_array",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence) = 'array'",
            name="ck_reader_panel_issues_evidence_array",
        ),
        CheckConstraint(
            "jsonb_typeof(source_reader_ids) = 'array'",
            name="ck_reader_panel_issues_source_readers_array",
        ),
        CheckConstraint(
            "target_audience_relevance IN ('low', 'medium', 'high')",
            name="ck_reader_panel_issues_audience_relevance",
        ),
        CheckConstraint(
            "discussion_status IN ('queued', 'discussing', 'closed', 'skipped')",
            name="ck_reader_panel_issues_discussion_status",
        ),
        CheckConstraint(
            f"consensus_class IS NULL OR consensus_class IN ({_sql_values(ConsensusClass)})",
            name="ck_reader_panel_issues_consensus_class",
        ),
        CheckConstraint(
            f"recommended_priority IS NULL OR recommended_priority IN ({_sql_values(EditorHandoffDecision)})",
            name="ck_reader_panel_issues_recommended_priority",
        ),
        CheckConstraint(
            "final_tally IS NULL OR jsonb_typeof(final_tally) = 'object'",
            name="ck_reader_panel_issues_final_tally_object",
        ),
        Index("idx_reader_panel_issues_session_id", "session_id"),
        Index("idx_reader_panel_issues_status", "session_id", "discussion_status"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    symptom: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_hypotheses: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    evidence: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    source_reader_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    target_audience_relevance: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="medium",
        server_default=text("'medium'"),
    )
    minority_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    discussion_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="queued",
        server_default=text("'queued'"),
    )
    consensus_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_priority: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_tally: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    session: Mapped[ReaderPanelSession] = relationship(back_populates="issues")
    ballots: Mapped[list[ReaderPanelBallot]] = relationship(
        back_populates="issue",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[ReaderPanelMessage]] = relationship(
        back_populates="issue",
        cascade="all, delete-orphan",
    )


class ReaderPanelBallot(Base):
    """Phase-specific ballot cast by one reader on a specific issue."""

    __tablename__ = "reader_panel_ballots"
    __table_args__ = (
        UniqueConstraint("reader_run_id", "issue_id", "phase", name="uq_reader_panel_ballots_run_issue_phase"),
        CheckConstraint(
            "phase IN ('initial', 'final')",
            name="ck_reader_panel_ballots_phase",
        ),
        CheckConstraint(
            f"severity IN ({_sql_values(Severity)})",
            name="ck_reader_panel_ballots_severity",
        ),
        CheckConstraint(
            f"suggested_action IN ({_sql_values(SuggestedAction)})",
            name="ck_reader_panel_ballots_suggested_action",
        ),
        CheckConstraint(
            f"confidence IN ({_sql_values(Confidence)})",
            name="ck_reader_panel_ballots_confidence",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence) = 'array'",
            name="ck_reader_panel_ballots_evidence_array",
        ),
        Index("idx_reader_panel_ballots_session_id", "session_id"),
        Index("idx_reader_panel_ballots_issue_phase", "issue_id", "phase"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    reader_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False, default=Confidence.MEDIUM.value)
    evidence: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    position_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    remaining_disagreement: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped[ReaderPanelSession] = relationship(back_populates="ballots")
    reader_run: Mapped[ReaderRun] = relationship(back_populates="ballots")
    issue: Mapped[ReaderPanelIssue] = relationship(back_populates="ballots")


class ReaderPanelMessage(Base):
    """Issue-scoped discussion turn by reader or moderator."""

    __tablename__ = "reader_panel_messages"
    __table_args__ = (
        UniqueConstraint("issue_id", "round_number", "turn_number", name="uq_reader_panel_messages_issue_round_turn"),
        CheckConstraint("round_number >= 1", name="ck_reader_panel_messages_round_positive"),
        CheckConstraint("turn_number >= 1", name="ck_reader_panel_messages_turn_positive"),
        CheckConstraint(
            "speaker_type IN ('reader', 'moderator')",
            name="ck_reader_panel_messages_speaker_type",
        ),
        CheckConstraint(
            "stance IS NULL OR stance IN ('support', 'oppose', 'mixed', 'abstain')",
            name="ck_reader_panel_messages_stance",
        ),
        CheckConstraint(
            "novelty IN ('new_evidence', 'new_interpretation', 'repetition', 'procedural')",
            name="ck_reader_panel_messages_novelty",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence) = 'array'",
            name="ck_reader_panel_messages_evidence_array",
        ),
        Index("idx_reader_panel_messages_session_id", "session_id"),
        Index("idx_reader_panel_messages_issue_round", "issue_id", "round_number"),
        Index(
            "idx_reader_panel_messages_idempotency",
            "issue_id",
            "idempotency_key",
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_panel_issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_type: Mapped[str] = mapped_column(Text, nullable=False)
    reader_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reader_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    stance: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    concession: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    novelty: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="new_evidence",
        server_default=text("'new_evidence'"),
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped[ReaderPanelSession] = relationship(back_populates="messages")
    issue: Mapped[ReaderPanelIssue] = relationship(back_populates="messages")
    reader_run: Mapped[ReaderRun | None] = relationship(back_populates="messages")
