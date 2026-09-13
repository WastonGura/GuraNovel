"""Add Studio assistant conversations and messages tables."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0010_studio_assistant"
down_revision = "0009_outline_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_assistant_conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.UUID(), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_studio_assistant_conversations_project",
        "studio_assistant_conversations",
        ["project_id"],
    )
    op.create_index(
        "idx_studio_assistant_conversations_chapter",
        "studio_assistant_conversations",
        ["chapter_id"],
    )

    op.create_table(
        "studio_assistant_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey("studio_assistant_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_results", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_studio_assistant_messages_role",
        ),
    )
    op.create_index(
        "idx_studio_assistant_messages_conversation",
        "studio_assistant_messages",
        ["conversation_id"],
    )
    op.create_index(
        "idx_studio_assistant_messages_created",
        "studio_assistant_messages",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("studio_assistant_messages")
    op.drop_table("studio_assistant_conversations")
