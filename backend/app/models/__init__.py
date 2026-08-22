"""MVP ORM model registry.

Import this package before using ``Base.metadata`` so every model is
registered, including when Alembic later autogenerates migrations.
"""

from app.models.core import (
    ActionRequest,
    AgentConversation,
    AgentMessage,
    Chapter,
    Document,
    DocumentVersion,
    Project,
    ReviewReport,
    User,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.models.enums import (
    ActionRequestStatus,
    DocumentSource,
    DocumentType,
    IssueSeverity,
    ReviewMode,
    WorkflowType,
)
from app.models.maintenance import MaintenanceAffectedItem, MaintenanceChange
from app.models.reader_panel import (
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
    ReaderPanelMessage,
    ReaderPanelSession,
    ReaderRun,
)

__all__ = [
    "ActionRequest",
    "ActionRequestStatus",
    "AgentConversation",
    "AgentMessage",
    "Chapter",
    "Document",
    "DocumentSource",
    "DocumentType",
    "DocumentVersion",
    "IssueSeverity",
    "MaintenanceAffectedItem",
    "MaintenanceChange",
    "Project",
    "ReaderInitialReport",
    "ReaderPanelBallot",
    "ReaderPanelIssue",
    "ReaderPanelMessage",
    "ReaderPanelSession",
    "ReaderRun",
    "ReviewMode",
    "ReviewReport",
    "User",
    "WorkflowCheckpoint",
    "WorkflowEvent",
    "WorkflowRun",
    "WorkflowType",
]
