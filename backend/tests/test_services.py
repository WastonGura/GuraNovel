EXPECTED_EXPORTS = {
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


def test_services_package_preserves_every_public_export_identity() -> None:
    import importlib

    import app.services as services

    namespace: dict[str, object] = {}
    exec("from app.services import *", namespace)

    assert services.__all__ == list(EXPECTED_EXPORTS)
    for name, module_name in EXPECTED_EXPORTS.items():
        expected = getattr(importlib.import_module(module_name), name)
        assert namespace[name] is expected
        assert getattr(services, name) is expected


def test_services_package_rejects_unknown_exports() -> None:
    import app.services as services

    for name in ("not_a_service", "import_module", "Any"):
        try:
            getattr(services, name)
        except AttributeError as error:
            assert error.name == name
        else:
            raise AssertionError(f"unknown service export was accepted: {name}")
