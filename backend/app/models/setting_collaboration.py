"""Durable document authoring by a setting Agent, separate from global Gura."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, false, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SettingCollaboration(Base):
    __tablename__ = "setting_collaborations"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_setting_collaboration_document"),
        Index("uq_setting_collaboration_collection", "setting_collection_id", unique=True, postgresql_where=text("document_id IS NULL AND is_default")),
        Index("idx_setting_collaboration_collection_updated", "setting_collection_id", "updated_at", "id", postgresql_where=text("document_id IS NULL")),
        UniqueConstraint("setting_collection_id", "client_request_id", name="uq_setting_collaboration_creation"),
        CheckConstraint("NOT is_default OR document_id IS NULL", name="ck_setting_collaboration_default_scope"),
        CheckConstraint("client_request_id IS NULL OR document_id IS NULL", name="ck_setting_collaboration_request_scope"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 80", name="ck_setting_collaboration_title"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    setting_collection_id: Mapped[UUID] = mapped_column(ForeignKey("setting_collections.id", ondelete="CASCADE"))
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    agent_role: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="新对话", server_default="新对话")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    client_request_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
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
        UniqueConstraint("attempt_id", "role", name="uq_setting_collaboration_attempt_role"),
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
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("setting_collaboration_attempts.id", ondelete="SET NULL"))
    context: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    proposal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    proposal: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    proposal_status: Mapped[str | None] = mapped_column(Text)
    base_content: Mapped[str | None] = mapped_column(Text)
    base_title: Mapped[str | None] = mapped_column(Text)
    accepted_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"))
    provenance: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettingCollaborationAttempt(Base):
    """A dispatch identity is permanent, even if no complete message pair exists."""

    __tablename__ = "setting_collaboration_attempts"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_message_id", name="uq_setting_agent_attempt_request"),
        Index("idx_setting_agent_attempt_conversation", "conversation_id", "created_at"),
        Index("uq_setting_agent_active_attempt", "conversation_id", unique=True,
              postgresql_where=text("status IN ('prepared', 'dispatched', 'result_saved')")),
        CheckConstraint("status IN ('prepared', 'dispatched', 'result_saved', 'completed', 'finished')", name="ck_setting_agent_attempt_status"),
        CheckConstraint("status NOT IN ('result_saved', 'completed') OR result IS NOT NULL", name="ck_setting_agent_attempt_result"),
        CheckConstraint("status != 'completed' OR (user_message_id IS NOT NULL AND assistant_message_id IS NOT NULL)", name="ck_setting_agent_attempt_pair"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("setting_collaborations.id", ondelete="CASCADE"))
    client_message_id: Mapped[str] = mapped_column(Text)
    request_fingerprint: Mapped[str] = mapped_column(Text)
    request: Mapped[dict] = mapped_column(JSONB)
    context: Mapped[dict] = mapped_column(JSONB)
    history: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    provenance: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    user_message_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    assistant_message_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def content(self) -> str:
        return self.request["content"]

    @property
    def has_result(self) -> bool:
        return self.result is not None
