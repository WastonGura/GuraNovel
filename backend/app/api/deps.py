from app.db.session import get_db_session
from app.core.config import settings
from app.workspace import ProjectWorkspace


def get_project_workspace() -> ProjectWorkspace:
    """Provide the configured workspace authority; clients never choose roots."""
    return ProjectWorkspace(settings.workspace_base_dir)

__all__ = ["get_db_session", "get_project_workspace"]
