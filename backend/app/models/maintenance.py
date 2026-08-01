"""Project-maintenance persistence mappings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.core import Chapter, Document, Project, TimestampMixin, WorkflowRun
from app.workflows.project_maintenance import (
    AffectedItemType,
    ImpactLevel,
    ProjectMaintenanceStatus,
)


def _sql_values(enum_type: type) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_type)


class MaintenanceChange(TimestampMixin, Base):
    """One durable project change attributed to exactly one maintenance run."""

    __tablename__ = "maintenance_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "workflow_run_id"],
            ["workflow_runs.project_id", "workflow_runs.id"],
            name="fk_maintenance_changes_project_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workflow_run_id", name="uq_maintenance_changes_workflow_run_id"),
        CheckConstraint("btrim(title) <> ''", name="ck_maintenance_changes_title_nonblank"),
        CheckConstraint(
            "char_length(title) <= 512", name="ck_maintenance_changes_title_length"
        ),
        CheckConstraint(
            "btrim(original_change_request) <> ''",
            name="ck_maintenance_changes_request_nonblank",
        ),
        CheckConstraint(
            "char_length(original_change_request) <= 131072",
            name="ck_maintenance_changes_request_length",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(ProjectMaintenanceStatus)})",
            name="ck_maintenance_changes_status",
        ),
        CheckConstraint(
            "status NOT IN ('CONSISTENCY_REVIEW', 'PROJECT_UPDATED') "
            "OR applied_at IS NOT NULL",
            name="ck_maintenance_changes_postapply_timestamp",
        ),
        CheckConstraint(
            "status NOT IN ('CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', "
            "'CHIEF_EDITOR_IMPACT_ANALYSIS', 'CANCELLED') OR applied_at IS NULL",
            name="ck_maintenance_changes_preapply_timestamp",
        ),
        CheckConstraint(
            "status NOT IN ('CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', "
            "'CHIEF_EDITOR_IMPACT_ANALYSIS') OR revision_plan_document_id IS NULL",
            name="ck_maintenance_changes_early_plan",
        ),
        CheckConstraint(
            "status NOT IN ('USER_CONFIRMATION', 'APPLY_CHANGE', "
            "'CONSISTENCY_REVIEW', 'PROJECT_UPDATED', 'CANCELLED') "
            "OR revision_plan_document_id IS NOT NULL",
            name="ck_maintenance_changes_late_plan",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_maintenance_changes_metadata_object",
        ),
        CheckConstraint(
            "octet_length(metadata::text) <= 16384",
            name="ck_maintenance_changes_metadata_size",
        ),
        Index("idx_maintenance_changes_project_id", "project_id"),
        Index("idx_maintenance_changes_project_status", "project_id", "status"),
        Index("idx_maintenance_changes_created_at", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    original_change_request: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revision_plan_document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    project: Mapped[Project] = relationship(
        back_populates="maintenance_changes", foreign_keys=[project_id]
    )
    workflow_run: Mapped[WorkflowRun] = relationship(
        back_populates="maintenance_change",
        foreign_keys=[project_id, workflow_run_id],
        overlaps="project,maintenance_changes",
    )
    revision_plan_document: Mapped[Document | None] = relationship(
        foreign_keys=[revision_plan_document_id]
    )
    affected_items: Mapped[list[MaintenanceAffectedItem]] = relationship(
        back_populates="maintenance_change",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="MaintenanceAffectedItem.position",
    )


class MaintenanceAffectedItem(Base):
    """An ordered, validated impact-analysis item for a maintenance change."""

    __tablename__ = "maintenance_affected_items"
    __table_args__ = (
        UniqueConstraint(
            "maintenance_change_id",
            "position",
            name="uq_maintenance_affected_items_position",
        ),
        UniqueConstraint(
            "maintenance_change_id",
            "item_type",
            "stable_reference",
            name="uq_maintenance_affected_items_reference",
        ),
        CheckConstraint("position >= 0", name="ck_maintenance_affected_items_position"),
        CheckConstraint(
            f"item_type IN ({_sql_values(AffectedItemType)})",
            name="ck_maintenance_affected_items_type",
        ),
        CheckConstraint(
            f"impact_level IN ({_sql_values(ImpactLevel)})",
            name="ck_maintenance_affected_items_impact",
        ),
        CheckConstraint(
            "btrim(stable_reference) <> ''",
            name="ck_maintenance_affected_items_reference_nonblank",
        ),
        CheckConstraint(
            "char_length(stable_reference) <= 2048",
            name="ck_maintenance_affected_items_reference_length",
        ),
        CheckConstraint(
            "btrim(reason) <> ''", name="ck_maintenance_affected_items_reason_nonblank"
        ),
        CheckConstraint(
            "char_length(reason) <= 16384",
            name="ck_maintenance_affected_items_reason_length",
        ),
        Index("idx_maintenance_affected_items_change_id", "maintenance_change_id"),
        Index("idx_maintenance_affected_items_document_id", "existing_document_id"),
        Index("idx_maintenance_affected_items_chapter_id", "existing_chapter_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    maintenance_change_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("maintenance_changes.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    stable_reference: Mapped[str] = mapped_column(Text, nullable=False)
    impact_level: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    existing_document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    existing_chapter_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    maintenance_change: Mapped[MaintenanceChange] = relationship(
        back_populates="affected_items"
    )
    existing_document: Mapped[Document | None] = relationship(
        foreign_keys=[existing_document_id]
    )
    existing_chapter: Mapped[Chapter | None] = relationship(foreign_keys=[existing_chapter_id])
