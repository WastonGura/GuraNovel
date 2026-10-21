"""Isolate setting authoring conversations and version-bound proposals from Gura.

Revision ID: 0014_setting_collaboration
Revises: 0013_setting_assistant_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_setting_collaboration"
down_revision = "0013_setting_assistant_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setting_collaborations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("setting_collection_id", sa.UUID(), sa.ForeignKey("setting_collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", name="uq_setting_collaboration_document"),
    )
    op.create_table(
        "setting_collaboration_messages",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("conversation_id", sa.UUID(), sa.ForeignKey("setting_collaborations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("client_message_id", sa.Text()),
        sa.Column("request_fingerprint", sa.Text()),
        sa.Column("context", postgresql.JSONB(none_as_null=True)),
        sa.Column("proposal_id", sa.UUID()),
        sa.Column("proposal", postgresql.JSONB(none_as_null=True)),
        sa.Column("proposal_status", sa.Text()),
        sa.Column("base_content", sa.Text()),
        sa.Column("base_title", sa.Text()),
        sa.Column("accepted_version_id", sa.UUID(), sa.ForeignKey("document_versions.id", ondelete="SET NULL")),
        sa.Column("provenance", postgresql.JSONB(none_as_null=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_setting_collaboration_request"),
        sa.UniqueConstraint("proposal_id", name="uq_setting_collaboration_proposal"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_setting_collaboration_message_role"),
        sa.CheckConstraint("proposal_status IS NULL OR proposal_status IN ('pending', 'accepted', 'rejected')", name="ck_setting_collaboration_proposal_status"),
    )
    op.create_index("idx_setting_collaboration_messages_conversation", "setting_collaboration_messages", ["conversation_id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM setting_collaborations)")):
        raise RuntimeError("Export setting Agent conversations before downgrade; refusing to discard discussions or proposals.")
    op.drop_table("setting_collaboration_messages")
    op.drop_table("setting_collaborations")
