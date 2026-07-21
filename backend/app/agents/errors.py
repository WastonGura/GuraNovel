"""Safe failures for agent-profile loading."""

from app.core.errors import AppError, ConflictError


class ProfileRegistryError(AppError):
    """Raised when an allowlisted agent profile cannot be safely used."""

    status_code = 503
    code = "agent_profile_unavailable"
    default_message = "The requested agent profile is unavailable."

    def __init__(self) -> None:
        super().__init__()


class ConceptArtifactWorkflowError(ConflictError):
    """Raised when concept output lacks a matching project-creation workflow run."""

    code = "concept_artifact_workflow_invalid"
    default_message = "The concept artifact cannot be stored for this workflow."

    def __init__(self) -> None:
        super().__init__()
