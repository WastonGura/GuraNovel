"""Keep explicit setting archives separate from automatic document revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_setting_restore_points"
down_revision = "0015_setting_collection_collab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setting_document_restore_points",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.UUID(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("point_number", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("comments_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", "point_number", name="uq_setting_restore_point_number"),
        sa.CheckConstraint("point_number > 0", name="ck_setting_restore_point_number"),
        sa.CheckConstraint(
            "jsonb_typeof(comments_snapshot) = 'array'", name="ck_setting_restore_point_comments"
        ),
    )
    op.create_index(
        "idx_setting_restore_points_document", "setting_document_restore_points", ["document_id"]
    )
    # Existing automatic revisions deliberately remain unlisted until a user archives.


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM setting_document_restore_points)")
    ):
        raise RuntimeError(
            "Export manual setting archives before downgrade; refusing to discard restore points."
        )
    op.drop_table("setting_document_restore_points")
