"""Thin HTTP routes for chapter-production workflows."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ChapterGenerationComposition, get_chapter_generation_composition, get_db_session
from app.api.schemas_chapter_production import (
    ChapterProductionRunResponse,
    ResolveChapterProductionActionRequest,
    StartChapterProductionRequest,
)
from app.llm import ProviderUnavailableError
from app.services import ChapterProductionRunRead, ChapterProductionService

router = APIRouter(prefix="/projects/{project_id}/chapters/{chapter_id}/production-runs")


@router.post("", response_model=ChapterProductionRunResponse, status_code=status.HTTP_201_CREATED)
async def start_chapter_production(
    project_id: UUID,
    chapter_id: UUID,
    payload: StartChapterProductionRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    generation: ChapterGenerationComposition = Depends(get_chapter_generation_composition),
) -> ChapterProductionRunRead:
    service = ChapterProductionService(
        session, generation.provider, generation_provenance=generation.provenance
    )
    try:
        started = await service.start_production(project_id, chapter_id)
        result = await service.get_production_run(project_id, chapter_id, started.workflow_run_id)
    except BaseException:
        close = getattr(generation.provider, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
        raise
    else:
        close = getattr(generation.provider, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as error:
                raise ProviderUnavailableError() from error
        return result


@router.get("/{workflow_run_id}", response_model=ChapterProductionRunResponse)
async def get_chapter_production(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ChapterProductionRunRead:
    return await ChapterProductionService(session).get_production_run(
        project_id, chapter_id, workflow_run_id
    )


@router.post(
    "/{workflow_run_id}/actions/{action_id}/resolve", response_model=ChapterProductionRunResponse
)
async def resolve_chapter_production_action(
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_id: UUID,
    payload: ResolveChapterProductionActionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ChapterProductionRunRead:
    service = ChapterProductionService(session)
    await service.resolve_action(project_id, chapter_id, workflow_run_id, action_id, payload.decision)
    return await service.get_production_run(project_id, chapter_id, workflow_run_id)
