"""Durable document authoring by a setting Agent, separate from global Gura."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SettingCollaboration(Base):
    __tablename__ = "setting_collaborations"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_setting_collaboration_document"),
        Index("uq_setting_collaboration_collection", "setting_collection_id", unique=True, postgresql_where=text("document_id IS NULL")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    setting_collection_id: Mapped[UUID] = mapped_column(ForeignKey("setting_collections.id", ondelete="CASCADE"))
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    agent_role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    messages: Mapped[list["SettingCollaborationMessage"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="SettingCollaborationMessage.created_at",
    )


class SettingCollaborationMessage(Base):
    __tablename__ = "setting_collaboration_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_message_id", name="uq_setting_collaboration_request"),
        UniqueConstraint("proposal_id", name="uq_setting_collaboration_proposal"),
        Index("idx_setting_collaboration_messages_conversation", "conversation_id"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_setting_collaboration_message_role"),
        CheckConstraint("proposal_status IS NULL OR proposal_status IN ('pending', 'accepted', 'rejected')", name="ck_setting_collaboration_proposal_status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("setting_collaborations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    client_message_id: Mapped[str | None] = mapped_column(Text)
    request_fingerprint: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    proposal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    proposal: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    proposal_status: Mapped[str | None] = mapped_column(Text)
    base_content: Mapped[str | None] = mapped_column(Text)
    base_title: Mapped[str | None] = mapped_column(Text)
    accepted_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"))
    provenance: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
