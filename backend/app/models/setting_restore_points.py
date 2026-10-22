"""Manual setting archives reference, rather than replace, immutable saved versions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.core import DocumentVersion


class SettingDocumentRestorePoint(Base):
    __tablename__ = "setting_document_restore_points"
    __table_args__ = (
        UniqueConstraint("document_id", "point_number", name="uq_setting_restore_point_number"),
        CheckConstraint("point_number > 0", name="ck_setting_restore_point_number"),
        CheckConstraint(
            "jsonb_typeof(comments_snapshot) = 'array'", name="ck_setting_restore_point_comments"
        ),
        CheckConstraint(
            "message IS NULL OR char_length(message) <= 160",
            name="ck_setting_restore_point_message_length",
        ),
        Index("idx_setting_restore_points_document", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    point_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    comments_snapshot: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    version: Mapped[DocumentVersion] = relationship()
