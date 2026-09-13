"""Persist mutable Studio feedback separately from immutable submissions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0007_studio_feedback"
down_revision = "0006_studio_restore_points"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_feedback",
        sa.Column("chapter_id", sa.UUID(), sa.ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("region", sa.Text(), primary_key=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version_id", sa.UUID(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("comments", postgresql.JSONB(), nullable=False),
        sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.CheckConstraint("region IN ('outline', 'draft')", name="ck_studio_feedback_region"),
        sa.CheckConstraint("revision > 0", name="ck_studio_feedback_revision"),
        sa.CheckConstraint("jsonb_typeof(comments) = 'array'", name="ck_studio_feedback_comments"),
    )
    op.create_table(
        "studio_feedback_submissions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("chapter_id", sa.UUID(), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version_id", sa.UUID(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feedback_revision", sa.Integer(), nullable=False),
        sa.Column("comments", postgresql.JSONB(), nullable=False),
        sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("region IN ('outline', 'draft')", name="ck_studio_feedback_submission_region"),
        sa.CheckConstraint("jsonb_typeof(comments) = 'array'", name="ck_studio_feedback_submission_comments"),
    )
    op.create_index("idx_studio_feedback_submissions_chapter", "studio_feedback_submissions", ["chapter_id"])


def downgrade() -> None:
    op.drop_table("studio_feedback_submissions")
    op.drop_table("studio_feedback")
