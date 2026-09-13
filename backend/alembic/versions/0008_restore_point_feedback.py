"""Keep feedback with a restore point without inventing historical comments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_restore_point_feedback"
down_revision = "0007_studio_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("studio_restore_points", sa.Column("feedback_snapshot", postgresql.JSONB(none_as_null=True), nullable=True))


def downgrade() -> None:
    op.drop_column("studio_restore_points", "feedback_snapshot")
