"""Add isolated collection contexts to existing assistant conversations.

Revision ID: 0013_setting_assistant_scope
Revises: 0012_setting_document_trash
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_setting_assistant_scope"
down_revision = "0012_setting_document_trash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("studio_assistant_conversations", sa.Column("setting_collection_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_studio_assistant_collection", "studio_assistant_conversations", "setting_collections", ["setting_collection_id"], ["id"], ondelete="CASCADE")
    op.alter_column("studio_assistant_conversations", "project_id", nullable=True)
    op.create_check_constraint("ck_studio_assistant_scope", "studio_assistant_conversations", "num_nonnulls(project_id, setting_collection_id) = 1 AND (setting_collection_id IS NULL OR chapter_id IS NULL)")
    op.create_index("uq_studio_assistant_collection", "studio_assistant_conversations", ["setting_collection_id"], unique=True, postgresql_where=sa.text("setting_collection_id IS NOT NULL"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM studio_assistant_conversations WHERE setting_collection_id IS NOT NULL)")):
        raise RuntimeError("Export collection assistant conversations before downgrade; refusing to discard messages.")
    op.drop_index("uq_studio_assistant_collection", table_name="studio_assistant_conversations")
    op.drop_constraint("ck_studio_assistant_scope", "studio_assistant_conversations", type_="check")
    op.drop_constraint("fk_studio_assistant_collection", "studio_assistant_conversations", type_="foreignkey")
    op.drop_column("studio_assistant_conversations", "setting_collection_id")
    op.alter_column("studio_assistant_conversations", "project_id", nullable=False)
