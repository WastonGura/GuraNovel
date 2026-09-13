"""Bind explicit Studio outline approval to an immutable version."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_outline_approval"
down_revision = "0008_restore_point_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column("approved_outline_version_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("chapters", "approved_outline_version_id")
