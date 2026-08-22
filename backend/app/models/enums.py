"""Enumerations defined by the MVP database schema.

The database intentionally stores these values as ``TEXT``.  Keeping the
enums at the application boundary avoids introducing PostgreSQL enum types
that are not part of the documented schema.
"""

from enum import Enum


class StrEnum(str, Enum):
    """A string enum suitable for validation and JSON serialization."""


class WorkflowType(StrEnum):
    PROJECT_CREATION = "project_creation"
    CHAPTER_PRODUCTION = "chapter_production"
    PROJECT_MAINTENANCE = "project_maintenance"
    READER_PANEL = "reader_panel"


class DocumentType(StrEnum):
    PROJECT_YAML = "project_yaml"
    PITCH = "pitch"
    SYNOPSIS = "synopsis"
    STYLE_GUIDE = "style_guide"
    WORLD_OVERVIEW = "world_overview"
    POWER_SYSTEM = "power_system"
    FACTIONS = "factions"
    GEOGRAPHY = "geography"
    HISTORY = "history"
    CHARACTER_PROFILE = "character_profile"
    MAIN_CAST = "main_cast"
    FULL_OUTLINE = "full_outline"
    VOLUME_OUTLINE = "volume_outline"
    FIRST_30_CHAPTERS = "first_30_chapters"
    CHAPTER_OUTLINE_OPTIONS = "chapter_outline_options"
    CHAPTER_SELECTED_OUTLINE = "chapter_selected_outline"
    CHAPTER_DRAFT = "chapter_draft"
    CHAPTER_FINAL = "chapter_final"
    CHAPTER_SUMMARY = "chapter_summary"
    ARCHIVE_UPDATE = "archive_update"
    REVIEW_ARTIFACT = "review_artifact"
    FORESHADOWING = "foreshadowing"
    UNRESOLVED_THREADS = "unresolved_threads"
    GLOSSARY = "glossary"
    MAINTENANCE_PLAN = "maintenance_plan"
    MAINTENANCE_REPORT = "maintenance_report"
    READER_PANEL_REPORT = "reader_panel_report"
    READER_PANEL_SUMMARY = "reader_panel_summary"


class DocumentSource(StrEnum):
    USER = "user"
    CONCEPT_AGENT = "concept_agent"
    CHIEF_EDITOR_AGENT = "chief_editor_agent"
    PROTAGONIST_AGENT = "protagonist_agent"
    WORLDBUILDING_AGENT = "worldbuilding_agent"
    PLOT_ARCHITECT_AGENT = "plot_architect_agent"
    STYLE_GUIDE_AGENT = "style_guide_agent"
    OUTLINE_AGENT = "outline_agent"
    WRITER_AGENT = "writer_agent"
    EDITOR_AGENT = "editor_agent"
    LORE_AGENT = "lore_agent"
    ARCHIVIST_AGENT = "archivist_agent"
    READER_AGENT = "reader_agent"
    MODERATOR_AGENT = "moderator_agent"
    SYSTEM = "system"


class ReviewMode(StrEnum):
    PROJECT_INITIAL_LORE = "project_initial_lore"
    PROJECT_CHIEF_FINAL = "project_chief_final"
    CHAPTER_OUTLINE_LORE = "chapter_outline_lore"
    CHAPTER_OUTLINE_CHIEF = "chapter_outline_chief"
    CHAPTER_EDITOR = "chapter_editor"
    CHAPTER_CHIEF_FINAL = "chapter_chief_final"
    CHAPTER_FINAL_LORE = "chapter_final_lore"
    MAINTENANCE_LORE_IMPACT = "maintenance_lore_impact"
    MAINTENANCE_CHIEF_IMPACT = "maintenance_chief_impact"
    MAINTENANCE_CONSISTENCY = "maintenance_consistency"
    READER_PANEL = "reader_panel"


class IssueSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    NOTE = "note"


class ActionRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISED = "revised"
    FORCE_APPROVED = "force_approved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
