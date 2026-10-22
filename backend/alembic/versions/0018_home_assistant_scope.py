"""Persist isolated home help without borrowing a project or collection."""

import sqlalchemy as sa
from alembic import op

revision = "0018_home_assistant_scope"
down_revision = "0017_setting_archive_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "studio_assistant_conversations", sa.Column("home_owner_key", sa.String(64), nullable=True)
    )
    op.drop_constraint("ck_studio_assistant_scope", "studio_assistant_conversations", type_="check")
    op.create_check_constraint(
        "ck_studio_assistant_scope",
        "studio_assistant_conversations",
        "num_nonnulls(project_id, setting_collection_id, home_owner_key) = 1 "
        "AND (project_id IS NOT NULL OR chapter_id IS NULL) "
        "AND (home_owner_key IS NULL OR home_owner_key = 'local' OR "
        "home_owner_key ~ '^user:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')",
    )
    op.create_index(
        "uq_studio_assistant_home_owner", "studio_assistant_conversations", ["home_owner_key"],
        unique=True, postgresql_where=sa.text("home_owner_key IS NOT NULL"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM studio_assistant_conversations WHERE home_owner_key IS NOT NULL)"
    )):
        raise RuntimeError(
            "Export home assistant conversations before downgrade; refusing to discard messages."
        )
    op.drop_index("uq_studio_assistant_home_owner", table_name="studio_assistant_conversations")
    op.drop_constraint("ck_studio_assistant_scope", "studio_assistant_conversations", type_="check")
    op.drop_column("studio_assistant_conversations", "home_owner_key")
    op.create_check_constraint(
        "ck_studio_assistant_scope", "studio_assistant_conversations",
        "num_nonnulls(project_id, setting_collection_id) = 1 "
        "AND (setting_collection_id IS NULL OR chapter_id IS NULL)",
    )
