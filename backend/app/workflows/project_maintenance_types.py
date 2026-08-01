"""Dependency-light shared enum types for project-maintenance boundaries."""

from enum import Enum


class ProjectMaintenanceStatus(str, Enum):
    CHANGE_REQUESTED = "CHANGE_REQUESTED"
    LORE_IMPACT_ANALYSIS = "LORE_IMPACT_ANALYSIS"
    CHIEF_EDITOR_IMPACT_ANALYSIS = "CHIEF_EDITOR_IMPACT_ANALYSIS"
    REVISION_PLAN = "REVISION_PLAN"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    APPLY_CHANGE = "APPLY_CHANGE"
    CONSISTENCY_REVIEW = "CONSISTENCY_REVIEW"
    PROJECT_UPDATED = "PROJECT_UPDATED"
    CANCELLED = "CANCELLED"


class AffectedItemType(str, Enum):
    CHAPTER = "chapter"
    CHARACTER = "character"
    WORLD = "world"
    OUTLINE = "outline"
    FORESHADOWING = "foreshadowing"
    TIMELINE = "timeline"
    STYLE = "style"


class ImpactLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


__all__ = ["AffectedItemType", "ImpactLevel", "ProjectMaintenanceStatus"]
