from app.core.config import settings
from app.db.session import get_db_session
from app.llm import (
    ChapterGenerationProvenance,
    ChapterGenerationProvider,
    FakeChapterGenerationProvider,
    OpenAICompatibleChapterGenerationProvider,
    ProviderConfigurationError,
)
from app.workspace import ProjectWorkspace
from app.agents import (
    ArchivistAgent,
    ChiefEditorAgent,
    DeterministicMaintenanceProvider,
    LoreAgent,
    PlotArchitectAgent,
    WorldbuildingAgent,
)
from app.agents.composition import ProjectCreationComposition
from app.services.project_maintenance_service import ProjectMaintenanceComposition


class ChapterGenerationComposition:
    """Trusted provider and provenance selected exclusively by server settings."""

    def __init__(
        self, provider: ChapterGenerationProvider, provenance: ChapterGenerationProvenance
    ) -> None:
        self.provider = provider
        self.provenance = provenance


def get_chapter_generation_composition(
    configured_settings= settings,
) -> ChapterGenerationComposition:
    """Construct the configured provider without making a network request."""
    if configured_settings.chapter_generation_provider == "fake":
        return ChapterGenerationComposition(
            FakeChapterGenerationProvider(),
            ChapterGenerationProvenance("fake", "deterministic-fake-v1", "chapter-production-v1"),
        )
    base_url = configured_settings.openai_compatible_base_url
    api_key = configured_settings.openai_compatible_api_key
    model = configured_settings.openai_compatible_model
    timeout = configured_settings.openai_compatible_timeout_seconds
    api_key_value = api_key.get_secret_value() if api_key is not None else ""
    if not base_url or not api_key_value or not model or timeout is None:
        raise ProviderConfigurationError()
    try:
        provenance = ChapterGenerationProvenance(
            "openai_compatible", model, "chapter-production-v1"
        )
        provider = OpenAICompatibleChapterGenerationProvider(
            base_url=base_url,
            api_key=api_key_value,
            model=model,
            timeout_seconds=timeout,
        )
    except Exception as error:
        raise ProviderConfigurationError() from error
    return ChapterGenerationComposition(provider, provenance)


def get_project_workspace() -> ProjectWorkspace:
    """Provide the configured workspace authority; clients never choose roots."""
    return ProjectWorkspace(settings.workspace_base_dir)


def get_project_creation_composition() -> ProjectCreationComposition:
    """The route owns a local-only composition; clients cannot select providers."""
    return ProjectCreationComposition()


def get_project_maintenance_composition() -> ProjectMaintenanceComposition:
    """Provide a credential-free server-owned composition for maintenance routes."""

    provider = DeterministicMaintenanceProvider()
    return ProjectMaintenanceComposition(
        LoreAgent(provider),
        ChiefEditorAgent(provider),
        PlotArchitectAgent(provider),
        WorldbuildingAgent(provider),
        ArchivistAgent(provider),
    )

__all__ = [
    "ChapterGenerationComposition",
    "get_chapter_generation_composition",
    "get_db_session",
    "get_project_workspace",
    "get_project_creation_composition",
    "get_project_maintenance_composition",
]
