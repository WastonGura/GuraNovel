"""Add project-maintenance persistence.

Revision ID: 0002_maintenance_persistence
Revises: 0001_initial_mvp_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_maintenance_persistence"
down_revision: str | Sequence[str] | None = "0001_initial_mvp_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflow_runs_project_id_id", "workflow_runs", ["project_id", "id"]
    )
    op.create_table(
        "maintenance_changes",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_change_request", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision_plan_document_id", sa.UUID(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "btrim(title) <> ''", name="ck_maintenance_changes_title_nonblank"
        ),
        sa.CheckConstraint(
            "char_length(title) <= 512", name="ck_maintenance_changes_title_length"
        ),
        sa.CheckConstraint(
            "btrim(original_change_request) <> ''",
            name="ck_maintenance_changes_request_nonblank",
        ),
        sa.CheckConstraint(
            "char_length(original_change_request) <= 131072",
            name="ck_maintenance_changes_request_length",
        ),
        sa.CheckConstraint(
            "status IN ('CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', "
            "'CHIEF_EDITOR_IMPACT_ANALYSIS', 'REVISION_PLAN', 'USER_CONFIRMATION', "
            "'APPLY_CHANGE', 'CONSISTENCY_REVIEW', 'PROJECT_UPDATED', 'CANCELLED')",
            name="ck_maintenance_changes_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('CONSISTENCY_REVIEW', 'PROJECT_UPDATED') "
            "OR applied_at IS NOT NULL",
            name="ck_maintenance_changes_postapply_timestamp",
        ),
        sa.CheckConstraint(
            "status NOT IN ('CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', "
            "'CHIEF_EDITOR_IMPACT_ANALYSIS', 'CANCELLED') OR applied_at IS NULL",
            name="ck_maintenance_changes_preapply_timestamp",
        ),
        sa.CheckConstraint(
            "status NOT IN ('CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', "
            "'CHIEF_EDITOR_IMPACT_ANALYSIS') OR revision_plan_document_id IS NULL",
            name="ck_maintenance_changes_early_plan",
        ),
        sa.CheckConstraint(
            "status NOT IN ('USER_CONFIRMATION', 'APPLY_CHANGE', "
            "'CONSISTENCY_REVIEW', 'PROJECT_UPDATED', 'CANCELLED') "
            "OR revision_plan_document_id IS NOT NULL",
            name="ck_maintenance_changes_late_plan",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_maintenance_changes_metadata_object",
        ),
        sa.CheckConstraint(
            "octet_length(metadata::text) <= 16384",
            name="ck_maintenance_changes_metadata_size",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "workflow_run_id"],
            ["workflow_runs.project_id", "workflow_runs.id"],
            name="fk_maintenance_changes_project_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revision_plan_document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id", name="uq_maintenance_changes_workflow_run_id"
        ),
    )
    op.create_index(
        "idx_maintenance_changes_project_id", "maintenance_changes", ["project_id"]
    )
    op.create_index(
        "idx_maintenance_changes_project_status",
        "maintenance_changes",
        ["project_id", "status"],
    )
    op.create_index(
        "idx_maintenance_changes_created_at",
        "maintenance_changes",
        [sa.text("created_at DESC")],
    )
    op.create_table(
        "maintenance_affected_items",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("maintenance_change_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("stable_reference", sa.Text(), nullable=False),
        sa.Column("impact_level", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("existing_document_id", sa.UUID(), nullable=True),
        sa.Column("existing_chapter_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("position >= 0", name="ck_maintenance_affected_items_position"),
        sa.CheckConstraint(
            "item_type IN ('chapter', 'character', 'world', 'outline', "
            "'foreshadowing', 'timeline', 'style')",
            name="ck_maintenance_affected_items_type",
        ),
        sa.CheckConstraint(
            "impact_level IN ('low', 'medium', 'high')",
            name="ck_maintenance_affected_items_impact",
        ),
        sa.CheckConstraint(
            "btrim(stable_reference) <> ''",
            name="ck_maintenance_affected_items_reference_nonblank",
        ),
        sa.CheckConstraint(
            "char_length(stable_reference) <= 2048",
            name="ck_maintenance_affected_items_reference_length",
        ),
        sa.CheckConstraint(
            "btrim(reason) <> ''", name="ck_maintenance_affected_items_reason_nonblank"
        ),
        sa.CheckConstraint(
            "char_length(reason) <= 16384",
            name="ck_maintenance_affected_items_reason_length",
        ),
        sa.ForeignKeyConstraint(
            ["maintenance_change_id"], ["maintenance_changes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["existing_document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["existing_chapter_id"], ["chapters.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "maintenance_change_id",
            "position",
            name="uq_maintenance_affected_items_position",
        ),
        sa.UniqueConstraint(
            "maintenance_change_id",
            "item_type",
            "stable_reference",
            name="uq_maintenance_affected_items_reference",
        ),
    )
    op.create_index(
        "idx_maintenance_affected_items_change_id",
        "maintenance_affected_items",
        ["maintenance_change_id"],
    )
    op.create_index(
        "idx_maintenance_affected_items_document_id",
        "maintenance_affected_items",
        ["existing_document_id"],
    )
    op.create_index(
        "idx_maintenance_affected_items_chapter_id",
        "maintenance_affected_items",
        ["existing_chapter_id"],
    )


def downgrade() -> None:
    op.drop_table("maintenance_affected_items")
    op.drop_table("maintenance_changes")
    op.drop_constraint(
        "uq_workflow_runs_project_id_id", "workflow_runs", type_="unique"
    )
