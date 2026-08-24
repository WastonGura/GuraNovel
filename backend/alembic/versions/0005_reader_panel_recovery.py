"""Add content-free Reader Panel provider invocation ledger.

Revision ID: 0005_reader_panel_recovery
Revises: 0004_reader_panel_persistence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_reader_panel_recovery"
down_revision: str | Sequence[str] | None = "0004_reader_panel_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE reader_panel_sessions
            SET config_snapshot = jsonb_build_object(
                'max_model_calls_per_phase', GREATEST(COALESCE((config_snapshot->>'max_total_model_calls')::integer, 1), 1),
                'max_total_input_tokens',
                    GREATEST(COALESCE((config_snapshot->>'max_total_model_calls')::bigint, 1), 1)
                    * GREATEST(COALESCE((config_snapshot->>'max_input_tokens_per_call')::bigint, 1), 1),
                'max_output_tokens_per_call',
                    GREATEST(1, LEAST(COALESCE((config_snapshot->>'max_input_tokens_per_call')::integer, 4096), 4096)),
                'max_total_output_tokens',
                    GREATEST(COALESCE((config_snapshot->>'max_total_model_calls')::bigint, 1), 1)
                    * GREATEST(1, LEAST(COALESCE((config_snapshot->>'max_input_tokens_per_call')::integer, 4096), 4096)),
                'max_messages', GREATEST(COALESCE((config_snapshot->>'max_total_model_calls')::integer, 1), 1),
                'max_provider_attempts', 2,
                'max_invalid_output_repairs', 1
            ) || config_snapshot
            """
        )
    )
    op.create_table(
        "reader_panel_invocations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("work_key", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("provider_calls", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["reader_panel_sessions.id"],
            name="fk_reader_panel_invocations_session_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reader_panel_invocations"),
        sa.UniqueConstraint(
            "session_id",
            "phase",
            "work_key",
            "attempt",
            name="uq_reader_panel_invocations_work_attempt",
        ),
        sa.CheckConstraint(
            "phase IN ('initial_reading', 'issue_extraction', 'initial_ballot', 'discussion_turn', 'discussion_summary', 'final_ballot', 'report_synthesis')",
            name="ck_reader_panel_invocations_phase",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'cancelled', 'unknown_commit')",
            name="ck_reader_panel_invocations_status",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('timeout', 'rate_limited', 'unavailable', 'invalid_output', 'configuration', 'unknown', 'budget_exhausted', 'cancelled', 'unknown_commit')",
            name="ck_reader_panel_invocations_safe_error",
        ),
        sa.CheckConstraint(
            "work_key ~ '^[A-Za-z0-9:_-]{1,160}$'",
            name="ck_reader_panel_invocations_work_key",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_reader_panel_invocations_attempt_positive"),
        sa.CheckConstraint(
            "provider_calls >= 1 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ck_reader_panel_invocations_accounting_non_negative",
        ),
    )
    op.create_index(
        "idx_reader_panel_invocations_session", "reader_panel_invocations", ["session_id"]
    )
    op.create_index(
        "uq_reader_panel_invocations_success",
        "reader_panel_invocations",
        ["session_id", "phase", "work_key"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    op.drop_table("reader_panel_invocations")
    op.execute(
        sa.text(
            """
            UPDATE reader_panel_sessions
            SET config_snapshot = config_snapshot - ARRAY[
                'max_model_calls_per_phase',
                'max_total_input_tokens',
                'max_total_output_tokens',
                'max_output_tokens_per_call',
                'max_messages',
                'max_provider_attempts',
                'max_invalid_output_repairs'
            ]::text[]
            """
        )
    )
