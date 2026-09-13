"""Separate manual restore points from the automatic version history."""

import sqlalchemy as sa
from alembic import op

revision = "0006_studio_restore_points"
down_revision = "0005_reader_panel_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_restore_points",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("chapter_id", sa.UUID(), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.UUID(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_studio_restore_points_chapter", "studio_restore_points", ["chapter_id"])
    op.execute(sa.text("""
        INSERT INTO studio_restore_points (id, chapter_id, document_id, version_id, summary, created_at)
        SELECT v.id, d.chapter_id, d.id, v.id, v.change_summary, v.created_at
        FROM document_versions v JOIN documents d ON v.document_id = d.id
        JOIN chapters c ON c.id = d.chapter_id AND c.project_id = d.project_id
        WHERE d.type = 'chapter_draft' AND v.source = 'user' AND v.change_summary = '手动存档'
    """))


def downgrade() -> None:
    op.drop_table("studio_restore_points")
