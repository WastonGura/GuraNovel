def test_services_package_preserves_document_and_project_public_exports() -> None:
    namespace: dict[str, object] = {}

    exec("from app.services import *", namespace)

    assert "DocumentService" in namespace
    assert "ProjectService" in namespace
    assert "ProjectCommitIndeterminateError" in namespace
    assert "ProjectWorkspaceCleanupError" in namespace
