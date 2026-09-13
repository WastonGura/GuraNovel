"""Explicit user restore points, separate from automatic document versions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class StudioRestorePoint(Base):
    __tablename__ = "studio_restore_points"
    __table_args__ = (Index("idx_studio_restore_points_chapter", "chapter_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL marks older points whose feedback was never recorded.
    feedback_snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StudioFeedback(Base):
    __tablename__ = "studio_feedback"
    __table_args__ = (
        CheckConstraint("region IN ('outline', 'draft')", name="ck_studio_feedback_region"),
        CheckConstraint("revision > 0", name="ck_studio_feedback_revision"),
        CheckConstraint("jsonb_typeof(comments) = 'array'", name="ck_studio_feedback_comments"),
    )

    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True)
    region: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    source_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    comments: Mapped[list[dict]] = mapped_column(JSONB)
    requirements: Mapped[str] = mapped_column(Text)
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_fingerprint: Mapped[str] = mapped_column(String(64))


class StudioFeedbackSubmission(Base):
    __tablename__ = "studio_feedback_submissions"
    __table_args__ = (
        Index("idx_studio_feedback_submissions_chapter", "chapter_id"),
        CheckConstraint("region IN ('outline', 'draft')", name="ck_studio_feedback_submission_region"),
        CheckConstraint("jsonb_typeof(comments) = 'array'", name="ck_studio_feedback_submission_comments"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    chapter_id: Mapped[UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    region: Mapped[str] = mapped_column(Text)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    source_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    feedback_revision: Mapped[int] = mapped_column(Integer)
    comments: Mapped[list[dict]] = mapped_column(JSONB)
    requirements: Mapped[str] = mapped_column(Text)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudioAssistantConversation(Base):
    __tablename__ = "studio_assistant_conversations"
    __table_args__ = (
        Index("idx_studio_assistant_conversations_project", "project_id"),
        Index("idx_studio_assistant_conversations_chapter", "chapter_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["StudioAssistantMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="StudioAssistantMessage.created_at",
        lazy="selectin",
    )


class StudioAssistantMessage(Base):
    __tablename__ = "studio_assistant_messages"
    __table_args__ = (
        Index("idx_studio_assistant_messages_conversation", "conversation_id"),
        Index("idx_studio_assistant_messages_created", "created_at"),
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_studio_assistant_messages_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("studio_assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    tool_results: Mapped[list[dict] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation: Mapped[StudioAssistantConversation] = relationship(back_populates="messages")

