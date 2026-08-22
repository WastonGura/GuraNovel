"""Add reader-panel persistence tables, constraints, and indexes.

Revision ID: 0004_reader_panel_persistence
Revises: 0003_runtime_pin_event_sequence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_reader_panel_persistence"
down_revision: str | Sequence[str] | None = "0003_runtime_pin_event_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. reader_panel_sessions
    op.create_table(
        "reader_panel_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("chapter_id", sa.UUID(), nullable=True),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("document_version_id", sa.UUID(), nullable=True),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'created'"), nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("model_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("prompt_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("target_audience", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("test_goals", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("step_counter", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("current_step", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("degradation_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("initial_reports_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_ballots_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_ballots_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_report_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_reader_panel_sessions_project_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], name="fk_reader_panel_sessions_chapter_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_reader_panel_sessions_document_id", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], name="fk_reader_panel_sessions_version_id", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["review_report_id"], ["review_reports.id"], name="fk_reader_panel_sessions_review_report_id", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_run_id"],
            ["workflow_runs.project_id", "workflow_runs.id"],
            name="fk_reader_panel_sessions_project_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reader_panel_sessions"),
        sa.UniqueConstraint("workflow_run_id", name="uq_reader_panel_sessions_run_id"),
        sa.CheckConstraint("mode IN ('off', 'quick', 'standard', 'panel')", name="ck_reader_panel_sessions_mode"),
        sa.CheckConstraint(
            "status IN ('created', 'preparing', 'independent_reading', 'initial_reports_locked', 'issue_extraction', 'initial_balloting', 'initial_ballots_locked', 'discussing', 'final_balloting', 'final_ballots_locked', 'report_generating', 'completed', 'degraded_completed', 'failed', 'cancelled')",
            name="ck_reader_panel_sessions_status",
        ),
        sa.CheckConstraint("btrim(source_hash) <> '' AND char_length(source_hash) = 64", name="ck_reader_panel_sessions_source_hash_sha256"),
        sa.CheckConstraint("jsonb_typeof(config_snapshot) = 'object'", name="ck_reader_panel_sessions_config_snapshot_object"),
        sa.CheckConstraint("jsonb_typeof(model_snapshot) = 'object'", name="ck_reader_panel_sessions_model_snapshot_object"),
        sa.CheckConstraint("jsonb_typeof(prompt_snapshot) = 'object'", name="ck_reader_panel_sessions_prompt_snapshot_object"),
        sa.CheckConstraint("jsonb_typeof(target_audience) = 'array'", name="ck_reader_panel_sessions_target_audience_array"),
        sa.CheckConstraint("jsonb_typeof(test_goals) = 'array'", name="ck_reader_panel_sessions_test_goals_array"),
        sa.CheckConstraint("step_counter >= 0", name="ck_reader_panel_sessions_step_counter_non_negative"),
    )
    op.create_index("idx_reader_panel_sessions_project_id", "reader_panel_sessions", ["project_id"])
    op.create_index("idx_reader_panel_sessions_chapter_id", "reader_panel_sessions", ["chapter_id"])
    op.create_index("idx_reader_panel_sessions_project_status", "reader_panel_sessions", ["project_id", "status"])
    op.create_index("idx_reader_panel_sessions_created_at", "reader_panel_sessions", [sa.text("created_at DESC")])

    # 2. reader_runs
    op.create_table(
        "reader_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("reader_profile_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("is_target_audience", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["reader_panel_sessions.id"], name="fk_reader_runs_session_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_reader_runs"),
        sa.UniqueConstraint("session_id", "reader_profile_id", name="uq_reader_runs_session_profile"),
        sa.CheckConstraint("btrim(reader_profile_id) <> '' AND char_length(reader_profile_id) <= 64", name="ck_reader_runs_profile_id"),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'invalid')", name="ck_reader_runs_status"),
        sa.CheckConstraint("retry_count >= 0", name="ck_reader_runs_retry_count_non_negative"),
    )
    op.create_index("idx_reader_runs_session_id", "reader_runs", ["session_id"])
    op.create_index("idx_reader_runs_profile", "reader_runs", ["session_id", "reader_profile_id"])

    # 3. reader_initial_reports
    op.create_table(
        "reader_initial_reports",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("reader_run_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("overall_reaction", sa.Text(), nullable=False),
        sa.Column("continue_reading", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("strengths", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("reactions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("concerns", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["reader_run_id"], ["reader_runs.id"], name="fk_reader_initial_reports_run_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["reader_panel_sessions.id"], name="fk_reader_initial_reports_session_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_reader_initial_reports"),
        sa.UniqueConstraint("reader_run_id", name="uq_reader_initial_reports_run_id"),
        sa.CheckConstraint("continue_reading IN ('yes', 'maybe', 'no')", name="ck_reader_initial_reports_continue_reading"),
        sa.CheckConstraint("confidence IN ('low', 'medium', 'high')", name="ck_reader_initial_reports_confidence"),
        sa.CheckConstraint("jsonb_typeof(strengths) = 'array'", name="ck_reader_initial_reports_strengths_array"),
        sa.CheckConstraint("jsonb_typeof(reactions) = 'array'", name="ck_reader_initial_reports_reactions_array"),
        sa.CheckConstraint("jsonb_typeof(concerns) = 'array'", name="ck_reader_initial_reports_concerns_array"),
    )
    op.create_index("idx_reader_initial_reports_session_id", "reader_initial_reports", ["session_id"])

    # 4. reader_panel_issues
    op.create_table(
        "reader_panel_issues",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("symptom", sa.Text(), nullable=False),
        sa.Column("root_cause_hypotheses", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("source_reader_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("target_audience_relevance", sa.Text(), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("minority_risk", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("discussion_status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("consensus_class", sa.Text(), nullable=True),
        sa.Column("recommended_priority", sa.Text(), nullable=True),
        sa.Column("final_tally", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["reader_panel_sessions.id"], name="fk_reader_panel_issues_session_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_reader_panel_issues"),
        sa.UniqueConstraint("session_id", "issue_number", name="uq_reader_panel_issues_session_number"),
        sa.CheckConstraint("issue_number >= 1", name="ck_reader_panel_issues_number_positive"),
        sa.CheckConstraint("btrim(title) <> '' AND char_length(title) <= 256", name="ck_reader_panel_issues_title"),
        sa.CheckConstraint("btrim(category) <> ''", name="ck_reader_panel_issues_category"),
        sa.CheckConstraint("btrim(symptom) <> ''", name="ck_reader_panel_issues_symptom"),
        sa.CheckConstraint("jsonb_typeof(root_cause_hypotheses) = 'array'", name="ck_reader_panel_issues_hypotheses_array"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'array'", name="ck_reader_panel_issues_evidence_array"),
        sa.CheckConstraint("jsonb_typeof(source_reader_ids) = 'array'", name="ck_reader_panel_issues_source_readers_array"),
        sa.CheckConstraint("target_audience_relevance IN ('low', 'medium', 'high')", name="ck_reader_panel_issues_audience_relevance"),
        sa.CheckConstraint("discussion_status IN ('queued', 'discussing', 'closed', 'skipped')", name="ck_reader_panel_issues_discussion_status"),
        sa.CheckConstraint("consensus_class IS NULL OR consensus_class IN ('strong_consensus', 'weak_consensus', 'polarized', 'accepted', 'inconclusive')", name="ck_reader_panel_issues_consensus_class"),
        sa.CheckConstraint("recommended_priority IS NULL OR recommended_priority IN ('must_fix', 'experiment', 'keep', 'manual_review', 'rejected')", name="ck_reader_panel_issues_recommended_priority"),
        sa.CheckConstraint("final_tally IS NULL OR jsonb_typeof(final_tally) = 'object'", name="ck_reader_panel_issues_final_tally_object"),
    )
    op.create_index("idx_reader_panel_issues_session_id", "reader_panel_issues", ["session_id"])
    op.create_index("idx_reader_panel_issues_status", "reader_panel_issues", ["session_id", "discussion_status"])

    # 5. reader_panel_ballots
    op.create_table(
        "reader_panel_ballots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("reader_run_id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("position_changed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("remaining_disagreement", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["reader_panel_sessions.id"], name="fk_reader_panel_ballots_session_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reader_run_id"], ["reader_runs.id"], name="fk_reader_panel_ballots_reader_run_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["reader_panel_issues.id"], name="fk_reader_panel_ballots_issue_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_reader_panel_ballots"),
        sa.UniqueConstraint("reader_run_id", "issue_id", "phase", name="uq_reader_panel_ballots_run_issue_phase"),
        sa.CheckConstraint("phase IN ('initial', 'final')", name="ck_reader_panel_ballots_phase"),
        sa.CheckConstraint("severity IN ('none', 'minor', 'significant', 'critical', 'abstain')", name="ck_reader_panel_ballots_severity"),
        sa.CheckConstraint("suggested_action IN ('keep', 'clarify', 'compress', 'expand', 'move', 'rewrite_local', 'split', 'experiment_ab', 'manual_review')", name="ck_reader_panel_ballots_suggested_action"),
        sa.CheckConstraint("confidence IN ('low', 'medium', 'high')", name="ck_reader_panel_ballots_confidence"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'array'", name="ck_reader_panel_ballots_evidence_array"),
    )
    op.create_index("idx_reader_panel_ballots_session_id", "reader_panel_ballots", ["session_id"])
    op.create_index("idx_reader_panel_ballots_issue_phase", "reader_panel_ballots", ["issue_id", "phase"])

    # 6. reader_panel_messages
    op.create_table(
        "reader_panel_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("speaker_type", sa.Text(), nullable=False),
        sa.Column("reader_run_id", sa.UUID(), nullable=True),
        sa.Column("stance", sa.Text(), nullable=True),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("concession", sa.Text(), nullable=True),
        sa.Column("proposed_action", sa.Text(), nullable=True),
        sa.Column("novelty", sa.Text(), server_default=sa.text("'new_evidence'"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["reader_panel_sessions.id"], name="fk_reader_panel_messages_session_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["reader_panel_issues.id"], name="fk_reader_panel_messages_issue_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reader_run_id"], ["reader_runs.id"], name="fk_reader_panel_messages_reader_run_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_reader_panel_messages"),
        sa.UniqueConstraint("issue_id", "round_number", "turn_number", name="uq_reader_panel_messages_issue_round_turn"),
        sa.CheckConstraint("round_number >= 1", name="ck_reader_panel_messages_round_positive"),
        sa.CheckConstraint("turn_number >= 1", name="ck_reader_panel_messages_turn_positive"),
        sa.CheckConstraint("speaker_type IN ('reader', 'moderator')", name="ck_reader_panel_messages_speaker_type"),
        sa.CheckConstraint("stance IS NULL OR stance IN ('support', 'oppose', 'mixed', 'abstain')", name="ck_reader_panel_messages_stance"),
        sa.CheckConstraint("novelty IN ('new_evidence', 'new_interpretation', 'repetition', 'procedural')", name="ck_reader_panel_messages_novelty"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'array'", name="ck_reader_panel_messages_evidence_array"),
    )
    op.create_index("idx_reader_panel_messages_session_id", "reader_panel_messages", ["session_id"])
    op.create_index("idx_reader_panel_messages_issue_round", "reader_panel_messages", ["issue_id", "round_number"])
    op.create_index(
        "idx_reader_panel_messages_idempotency",
        "reader_panel_messages",
        ["issue_id", "idempotency_key"],
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("reader_panel_messages")
    op.drop_table("reader_panel_ballots")
    op.drop_table("reader_panel_issues")
    op.drop_table("reader_initial_reports")
    op.drop_table("reader_runs")
    op.drop_table("reader_panel_sessions")
