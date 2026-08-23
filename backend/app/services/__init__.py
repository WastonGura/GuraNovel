"""Lazy, compatibility-preserving application service exports."""

from importlib import import_module as _import_module
from threading import RLock as _RLock


_EXPORT_MODULES = {
    "DocumentService": "app.services.document_service",
    "ChapterSegmentSnapshotMismatchError": "app.services.document_service",
    "ChapterService": "app.services.chapter_service",
    "ChapterProductionCommitIndeterminateError": "app.services.chapter_production_service",
    "ChapterProductionRunRead": "app.services.chapter_production_service",
    "ChapterProductionResolved": "app.services.chapter_production_service",
    "ChapterProductionService": "app.services.chapter_production_service",
    "ChapterProductionStarted": "app.services.chapter_production_service",
    "ChapterProductionV2CommitIndeterminateError": "app.services.chapter_production_v2_service",
    "ChapterProductionV2Finalized": "app.services.chapter_production_v2_service",
    "ChapterProductionV2ProviderError": "app.services.chapter_production_v2_service",
    "ChapterProductionV2ReconciliationError": "app.services.chapter_production_v2_service",
    "ChapterProductionV2Service": "app.services.chapter_production_v2_service",
    "ChapterProductionV2ReviewProviderError": "app.services.chapter_production_v2_service",
    "ChapterProductionV2Started": "app.services.chapter_production_v2_service",
    "ChapterProductionV2Updated": "app.services.chapter_production_v2_service",
    "ChapterProductionV2ValidationError": "app.services.chapter_production_v2_service",
    "ProjectCommitIndeterminateError": "app.services.project_service",
    "ProjectService": "app.services.project_service",
    "ProjectWorkspaceCleanupError": "app.services.project_service",
    "ProjectCreationPendingActionRead": "app.services.project_creation_service",
    "ProjectCreationRunRead": "app.services.project_creation_service",
    "ProjectCreationService": "app.services.project_creation_service",
    "ProjectMaintenanceCommitIndeterminateError": (
        "app.services.project_maintenance_foundation_service"
    ),
    "ProjectMaintenanceFoundationService": (
        "app.services.project_maintenance_foundation_service"
    ),
    "ProjectMaintenanceWaiting": "app.services.project_maintenance_foundation_service",
    "MaintenanceAffectedItemCreate": "app.services.maintenance_change_service",
    "MaintenanceChangeCommitIndeterminateError": "app.services.maintenance_change_service",
    "MaintenanceChangeService": "app.services.maintenance_change_service",
    "MaintenanceChangeValidationError": "app.services.maintenance_change_service",
    "ProjectMaintenanceComposition": "app.services.project_maintenance_service",
    "ProjectMaintenanceService": "app.services.project_maintenance_service",
    "ProjectMaintenanceStarted": "app.services.project_maintenance_service",
    "ReaderPanelService": "app.services.reader_panel_service",
    "ReaderPanelServiceError": "app.services.reader_panel_service",
    "ReaderPanelNotFoundError": "app.services.reader_panel_service",
    "ReaderPanelInvalidStateError": "app.services.reader_panel_service",
    "ReaderPanelQuorumError": "app.services.reader_panel_service",
    "ReaderPanelStaleVersionError": "app.services.reader_panel_service",
    "ReaderPanelSessionResult": "app.services.reader_panel_service",
}

__all__ = list(_EXPORT_MODULES)
_EXPORT_LOCK = _RLock()


def __getattr__(name: str) -> object:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}", name=name
        ) from None
    with _EXPORT_LOCK:
        if name not in globals():
            globals()[name] = getattr(_import_module(module_name), name)
        return globals()[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
