"""Keep recoverable setting documents and all their versions.

Revision ID: 0012_setting_document_trash
Revises: 0011_setting_collections
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_setting_document_trash"
down_revision = "0011_setting_collections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "deleted_at")
