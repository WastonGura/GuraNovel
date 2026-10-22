"""Permit explicit global assistant sessions while preserving all existing history."""

import sqlalchemy as sa
from alembic import op

revision = "0019_global_assistant_sessions"
down_revision = "0018_home_assistant_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_studio_assistant_home_owner", table_name="studio_assistant_conversations")
    op.create_index(
        "idx_studio_assistant_global_owner_updated", "studio_assistant_conversations",
        ["home_owner_key", "updated_at"],
        postgresql_where=sa.text("home_owner_key IS NOT NULL"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM studio_assistant_conversations "
        "WHERE home_owner_key IS NOT NULL GROUP BY home_owner_key HAVING count(*) > 1)"
    )):
        raise RuntimeError(
            "Export global assistant sessions before downgrade; refusing to discard conversations."
        )
    op.drop_index("idx_studio_assistant_global_owner_updated", table_name="studio_assistant_conversations")
    op.create_index(
        "uq_studio_assistant_home_owner", "studio_assistant_conversations", ["home_owner_key"],
        unique=True, postgresql_where=sa.text("home_owner_key IS NOT NULL"),
    )
