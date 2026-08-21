from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.agents import (
    ArchivistAgent,
    ChiefEditorAgent,
    ChiefEditorChapterFinalAgent,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    DeterministicMaintenanceProvider,
    EditorAgent,
    LoreAgent,
    LoreChapterFinalAgent,
    PlotArchitectAgent,
    RevisionAgent,
    WorldbuildingAgent,
    WriterAgent,
)
from app.agents.composition import ProjectCreationComposition
from app.core.config import settings
from app.db.session import get_db_session
from app.llm import (
    ChapterGenerationProvenance,
    ChapterGenerationProvider,
    FakeChapterGenerationProvider,
    OpenAICompatibleChapterGenerationProvider,
    ProviderConfigurationError,
)
from app.models import User
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.chapter_production_v2_service import ChapterProductionV2Service
from app.services.project_maintenance_service import ProjectMaintenanceComposition
from app.workspace import ProjectWorkspace


class ChapterGenerationComposition:
    """Trusted provider and provenance selected exclusively by server settings."""

    def __init__(
        self, provider: ChapterGenerationProvider, provenance: ChapterGenerationProvenance
    ) -> None:
        self.provider = provider
        self.provenance = provenance


def get_chapter_generation_composition(
    configured_settings=settings,
) -> ChapterGenerationComposition:
    """Construct the configured provider without making a network request."""
    if configured_settings.chapter_generation_provider == "fake":
        provider = FakeChapterGenerationProvider()
        return ChapterGenerationComposition(
            provider,
            provider.provenance,
        )
    base_url = configured_settings.openai_compatible_base_url
    api_key = configured_settings.openai_compatible_api_key
    model = configured_settings.openai_compatible_model
    timeout = configured_settings.openai_compatible_timeout_seconds
    api_key_value = api_key.get_secret_value() if api_key is not None else ""
    if not base_url or not api_key_value or not model or timeout is None:
        raise ProviderConfigurationError()
    provider = None
    try:
        provider = OpenAICompatibleChapterGenerationProvider(
            base_url=base_url,
            api_key=api_key_value,
            model=model,
            timeout_seconds=timeout,
        )
    except Exception:
        pass
    if provider is None:
        raise ProviderConfigurationError() from None
    return ChapterGenerationComposition(provider, provider.provenance)


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


class ChapterProductionV2Composition:
    """Server-selected composition for Chapter Production V2."""

    def __init__(
        self,
        *,
        writer_agent: WriterAgent,
        revision_agent: RevisionAgent | None = None,
        editor_agent: EditorAgent | None = None,
        chief_editor_agent: ChiefEditorChapterFinalAgent | None = None,
        lore_agent: LoreChapterFinalAgent | None = None,
        chief_editor_required: bool = True,
        phase_session_source: ChapterPhaseSessionSource | None = None,
    ) -> None:
        self.writer_agent = writer_agent
        self.revision_agent = revision_agent
        self.editor_agent = editor_agent
        self.chief_editor_agent = chief_editor_agent
        self.lore_agent = lore_agent
        self.chief_editor_required = chief_editor_required
        self.phase_session_source = phase_session_source

    def create_service(self, session: AsyncSession) -> ChapterProductionV2Service:
        phase_source = self.phase_session_source
        if phase_source is None:
            bind = getattr(session, "bind", None)
            if isinstance(bind, AsyncEngine):
                try:
                    phase_source = ChapterPhaseSessionSource(bind)
                except Exception:
                    phase_source = None
            if phase_source is None:
                try:
                    from app.db.session import engine as default_engine

                    if isinstance(default_engine, AsyncEngine):
                        phase_source = ChapterPhaseSessionSource(default_engine)
                except Exception:
                    phase_source = None
        return ChapterProductionV2Service(
            session,
            writer_agent=self.writer_agent,
            revision_agent=self.revision_agent,
            editor_agent=self.editor_agent,
            chief_editor_agent=self.chief_editor_agent,
            lore_agent=self.lore_agent,
            chief_editor_required=self.chief_editor_required,
            phase_session_source=phase_source,
        )


def get_chapter_production_v2_composition() -> ChapterProductionV2Composition:
    """Server-owned deterministic composition for V2 chapter production."""
    writer_provider = DeterministicChapterWriterProvider()
    review_provider = DeterministicChapterReviewProvider()
    try:
        from app.db.session import engine as default_engine

        phase_source = ChapterPhaseSessionSource(default_engine)
    except Exception:
        phase_source = None
    return ChapterProductionV2Composition(
        writer_agent=WriterAgent(writer_provider),
        revision_agent=RevisionAgent(writer_provider),
        editor_agent=EditorAgent(review_provider),
        chief_editor_agent=ChiefEditorChapterFinalAgent(review_provider),
        lore_agent=LoreChapterFinalAgent(review_provider),
        chief_editor_required=True,
        phase_session_source=phase_source,
    )


async def get_chapter_production_v2_service(
    session: AsyncSession = Depends(get_db_session),
    composition: ChapterProductionV2Composition = Depends(get_chapter_production_v2_composition),
) -> ChapterProductionV2Service:
    return composition.create_service(session)


async def get_default_actor_user_id(session: AsyncSession = Depends(get_db_session)) -> UUID:
    """Resolve an actor user for server operations, creating a default if missing."""
    user = (await session.scalars(select(User).order_by(User.created_at.asc()).limit(1))).first()
    if user is None:
        user = User(username="default_user", display_name="Default User")
        session.add(user)
        await session.flush()
    return user.id


__all__ = [
    "ChapterGenerationComposition",
    "ChapterProductionV2Composition",
    "get_chapter_generation_composition",
    "get_chapter_production_v2_composition",
    "get_chapter_production_v2_service",
    "get_db_session",
    "get_default_actor_user_id",
    "get_project_creation_composition",
    "get_project_maintenance_composition",
    "get_project_workspace",
]
