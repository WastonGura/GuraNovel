"""Checkpoint setting Agent dispatches without changing old conversations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_setting_agent_attempts"
down_revision = "0019_global_assistant_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setting_collaboration_attempts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("conversation_id", sa.UUID(), sa.ForeignKey("setting_collaborations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_message_id", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("history", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("result", postgresql.JSONB(none_as_null=True)),
        sa.Column("provenance", postgresql.JSONB(none_as_null=True)),
        sa.Column("user_message_id", sa.UUID()),
        sa.Column("assistant_message_id", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_setting_agent_attempt_request"),
        sa.CheckConstraint("status IN ('prepared', 'dispatched', 'result_saved', 'completed', 'finished')", name="ck_setting_agent_attempt_status"),
        sa.CheckConstraint("status NOT IN ('result_saved', 'completed') OR result IS NOT NULL", name="ck_setting_agent_attempt_result"),
        sa.CheckConstraint("status != 'completed' OR (user_message_id IS NOT NULL AND assistant_message_id IS NOT NULL)", name="ck_setting_agent_attempt_pair"),
    )
    op.create_index("idx_setting_agent_attempt_conversation", "setting_collaboration_attempts", ["conversation_id", "created_at"])
    op.create_index("uq_setting_agent_active_attempt", "setting_collaboration_attempts", ["conversation_id"], unique=True,
                    postgresql_where=sa.text("status IN ('prepared', 'dispatched', 'result_saved')"))
    op.add_column("setting_collaboration_messages", sa.Column("attempt_id", sa.UUID()))
    op.create_foreign_key("fk_setting_collaboration_message_attempt", "setting_collaboration_messages", "setting_collaboration_attempts", ["attempt_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_setting_collaboration_attempt_role", "setting_collaboration_messages", ["attempt_id", "role"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM setting_collaboration_attempts)")):
        raise RuntimeError("Export setting Agent attempts before downgrade; refusing to discard dispatch identities or results.")
    op.drop_constraint("uq_setting_collaboration_attempt_role", "setting_collaboration_messages", type_="unique")
    op.drop_constraint("fk_setting_collaboration_message_attempt", "setting_collaboration_messages", type_="foreignkey")
    op.drop_column("setting_collaboration_messages", "attempt_id")
    op.drop_table("setting_collaboration_attempts")
