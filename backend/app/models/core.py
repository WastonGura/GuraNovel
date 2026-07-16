"""SQLAlchemy mappings for the MVP persistence schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ActionRequestStatus


class TimestampMixin:
    """Columns shared by records whose lifetime includes modifications."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)

    projects: Mapped[list[Project]] = relationship(back_populates="owner")
    workflow_runs: Mapped[list[WorkflowRun]] = relationship(back_populates="user")
    document_versions: Mapped[list[DocumentVersion]] = relationship(back_populates="actor_user")
    resolved_action_requests: Mapped[list[ActionRequest]] = relationship(back_populates="resolved_by")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("idx_projects_owner_id", "owner_id"),
        Index("idx_projects_status", "status"),
        Index("idx_projects_metadata_gin", "metadata", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    owner_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    genre: Mapped[str | None] = mapped_column(Text)
    target_platform: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default=text("'draft'"))
    workspace_root: Mapped[str] = mapped_column(Text, nullable=False)
    current_workflow_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict,
                                            server_default=text("'{}'::jsonb"))

    owner: Mapped[User | None] = relationship(back_populates="projects")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="project", cascade="all, delete-orphan")
    workflow_runs: Mapped[list[WorkflowRun]] = relationship(back_populates="project")
    documents: Mapped[list[Document]] = relationship(back_populates="project")
    action_requests: Mapped[list[ActionRequest]] = relationship(back_populates="project")
    conversations: Mapped[list[AgentConversation]] = relationship(back_populates="project")
    review_reports: Mapped[list[ReviewReport]] = relationship(back_populates="project")


class Chapter(TimestampMixin, Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", name="uq_chapter_project_number"),
        Index("idx_chapters_project_id", "project_id"),
        Index("idx_chapters_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4,
                                     server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OUTLINE_DISCUSSION", server_default=text("'OUTLINE_DISCUSSION'"))
    current_outline_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_draft_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    final_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    summary_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict,
                                            server_default=text("'{}'::jsonb"))

    project: Mapped[Project] = relationship(back_populates="chapters")
    workflow_runs: Mapped[list[WorkflowRun]] = relationship(back_populates="chapter")
    documents: Mapped[list[Document]] = relationship(back_populates="chapter")
    action_requests: Mapped[list[ActionRequest]] = relationship(back_populates="chapter")
    conversations: Mapped[list[AgentConversation]] = relationship(back_populates="chapter")
    review_reports: Mapped[list[ReviewReport]] = relationship(back_populates="chapter")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("idx_workflow_runs_project_id", "project_id"), Index("idx_workflow_runs_chapter_id", "chapter_id"),
        Index("idx_workflow_runs_type_status", "workflow_type", "status"), Index("idx_workflow_runs_awaiting_user", "awaiting_user"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"))
    workflow_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_node: Mapped[str | None] = mapped_column(Text)
    next_node: Mapped[str | None] = mapped_column(Text)
    awaiting_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))

    project: Mapped[Project | None] = relationship(back_populates="workflow_runs")
    chapter: Mapped[Chapter | None] = relationship(back_populates="workflow_runs")
    user: Mapped[User | None] = relationship(back_populates="workflow_runs")
    checkpoints: Mapped[list[WorkflowCheckpoint]] = relationship(back_populates="workflow_run", cascade="all, delete-orphan")
    events: Mapped[list[WorkflowEvent]] = relationship(back_populates="workflow_run", cascade="all, delete-orphan")
    action_requests: Mapped[list[ActionRequest]] = relationship(back_populates="workflow_run", cascade="all, delete-orphan")
    document_versions: Mapped[list[DocumentVersion]] = relationship(back_populates="workflow_run")
    conversations: Mapped[list[AgentConversation]] = relationship(back_populates="workflow_run")
    messages: Mapped[list[AgentMessage]] = relationship(back_populates="workflow_run")
    review_reports: Mapped[list[ReviewReport]] = relationship(back_populates="workflow_run")


class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    __table_args__ = (UniqueConstraint("workflow_run_id", "checkpoint_index", name="uq_workflow_checkpoint_index"), Index("idx_workflow_checkpoints_run_id", "workflow_run_id"), Index("idx_workflow_checkpoints_state_gin", "state_json", postgresql_using="gin"))

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    workflow_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    checkpoint_index: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str | None] = mapped_column(Text)
    state_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="checkpoints")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (Index("idx_workflow_events_run_id", "workflow_run_id"), Index("idx_workflow_events_type", "event_type"))

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    workflow_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    node_name: Mapped[str | None] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, default="system", server_default=text("'system'"))
    actor_id: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="events")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "path", name="uq_document_project_path"),
        Index("idx_documents_project_id", "project_id"), Index("idx_documents_chapter_id", "chapter_id"),
        Index("idx_documents_type", "type"), Index("idx_documents_path", "path"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("document_versions.id", name="fk_documents_current_version", ondelete="SET NULL", use_alter=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))

    project: Mapped[Project] = relationship(back_populates="documents")
    chapter: Mapped[Chapter | None] = relationship(back_populates="documents")
    current_version: Mapped[DocumentVersion | None] = relationship(foreign_keys=[current_version_id], post_update=True)
    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document", foreign_keys="DocumentVersion.document_id", cascade="all, delete-orphan")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        Index("idx_document_versions_document_id", "document_id"), Index("idx_document_versions_source", "source"),
        Index("idx_document_versions_workflow_run_id", "workflow_run_id"),
        Index("idx_document_versions_created_at", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    agent_role: Mapped[str | None] = mapped_column(Text)
    workflow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="versions", foreign_keys=[document_id])
    parent_version: Mapped[DocumentVersion | None] = relationship(remote_side=[id], back_populates="child_versions")
    child_versions: Mapped[list[DocumentVersion]] = relationship(back_populates="parent_version")
    actor_user: Mapped[User | None] = relationship(back_populates="document_versions")
    workflow_run: Mapped[WorkflowRun | None] = relationship(back_populates="document_versions")


class ActionRequest(Base):
    __tablename__ = "action_requests"
    __table_args__ = (Index("idx_action_requests_workflow_run_id", "workflow_run_id"), Index("idx_action_requests_status", "status"), Index("idx_action_requests_project_chapter", "project_id", "chapter_id"))

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    workflow_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"))
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=ActionRequestStatus.PENDING.value, server_default=text("'pending'"))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    default_option: Mapped[str | None] = mapped_column(Text)
    user_decision: Mapped[str | None] = mapped_column(Text)
    user_feedback: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[UUID | None] = mapped_column("resolved_by", PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="action_requests")
    project: Mapped[Project | None] = relationship(back_populates="action_requests")
    chapter: Mapped[Chapter | None] = relationship(back_populates="action_requests")
    resolved_by: Mapped[User | None] = relationship(back_populates="resolved_action_requests")


class AgentConversation(TimestampMixin, Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (Index("idx_agent_conversations_project_id", "project_id"), Index("idx_agent_conversations_chapter_id", "chapter_id"), Index("idx_agent_conversations_role", "agent_role"))

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"))
    workflow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    agent_role: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active", server_default=text("'active'"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))

    project: Mapped[Project] = relationship(back_populates="conversations")
    chapter: Mapped[Chapter | None] = relationship(back_populates="conversations")
    workflow_run: Mapped[WorkflowRun | None] = relationship(back_populates="conversations")
    messages: Mapped[list[AgentMessage]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (Index("idx_agent_messages_conversation_id", "conversation_id"), Index("idx_agent_messages_workflow_run_id", "workflow_run_id"), Index("idx_agent_messages_created_at", "created_at"), Index("idx_agent_messages_structured_gin", "structured_content", postgresql_using="gin"))

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False)
    workflow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(Text, nullable=False)
    agent_role: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_content: Mapped[dict | list | None] = mapped_column(JSONB)
    model_provider: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation: Mapped[AgentConversation] = relationship(back_populates="messages")
    workflow_run: Mapped[WorkflowRun | None] = relationship(back_populates="messages")


class ReviewReport(Base):
    __tablename__ = "review_reports"
    __table_args__ = (
        Index("idx_review_reports_project_id", "project_id"), Index("idx_review_reports_chapter_id", "chapter_id"),
        Index("idx_review_reports_mode", "review_mode"), Index("idx_review_reports_passed", "passed"),
        Index("idx_review_reports_blocking_gin", "blocking_issues", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"))
    workflow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    review_mode: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_agent_role: Mapped[str] = mapped_column(Text, nullable=False)
    target_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"))
    target_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    blocking_issues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    notes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    suggested_actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    raw_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    report_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="review_reports")
    chapter: Mapped[Chapter | None] = relationship(back_populates="review_reports")
    workflow_run: Mapped[WorkflowRun | None] = relationship(back_populates="review_reports")
    target_document: Mapped[Document | None] = relationship(foreign_keys=[target_document_id])
    target_version: Mapped[DocumentVersion | None] = relationship(foreign_keys=[target_version_id])
    report_document: Mapped[Document | None] = relationship(foreign_keys=[report_document_id])
