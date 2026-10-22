"""Add optional user explanations without rewriting manual archives or prose."""

import sqlalchemy as sa
from alembic import op

revision = "0017_setting_archive_messages"
down_revision = "0016_setting_restore_points"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("setting_document_restore_points", sa.Column("message", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_setting_restore_point_message_length",
        "setting_document_restore_points",
        "message IS NULL OR char_length(message) <= 160",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM setting_document_restore_points WHERE message IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Export archive messages before downgrade; refusing to discard user explanations."
        )
    op.drop_constraint(
        "ck_setting_restore_point_message_length", "setting_document_restore_points", type_="check"
    )
    op.drop_column("setting_document_restore_points", "message")
