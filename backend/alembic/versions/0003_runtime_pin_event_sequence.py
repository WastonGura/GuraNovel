"""Add the V2 runtime pin and deterministic event sequence.

Revision ID: 0003_runtime_pin_event_sequence
Revises: 0002_maintenance_persistence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_runtime_pin_event_sequence"
down_revision: str | Sequence[str] | None = "0002_maintenance_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_events",
        sa.Column("event_sequence", sa.Integer(), nullable=True),
    )
    op.create_index(
        "uq_workflow_events_run_sequence",
        "workflow_events",
        ["workflow_run_id", "event_sequence"],
        unique=True,
        postgresql_where=sa.text("event_sequence IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workflow_events_run_sequence",
        table_name="workflow_events",
        postgresql_where=sa.text("event_sequence IS NOT NULL"),
    )
    op.drop_column("workflow_events", "event_sequence")
