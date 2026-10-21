"""Add collection-level authoring without repurposing document conversations.

Revision ID: 0015_setting_collection_collab
Revises: 0014_setting_collaboration
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_setting_collection_collab"
down_revision = "0014_setting_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("setting_collaborations", "document_id", nullable=True)
    op.create_index("uq_setting_collaboration_collection", "setting_collaborations", ["setting_collection_id"], unique=True, postgresql_where=sa.text("document_id IS NULL"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM setting_collaborations WHERE document_id IS NULL)")):
        raise RuntimeError("Export collection Agent conversations before downgrade; refusing to discard collection discussions or proposals.")
    op.drop_index("uq_setting_collaboration_collection", table_name="setting_collaborations")
    op.alter_column("setting_collaborations", "document_id", nullable=False)
