from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.schemas_documents import DocumentVersionResponse
from app.api.schemas_outline_options import (OutlineGenerationRequest, OutlineGenerationResponse,
    OutlineSelectionRequest, OutlineSelectionResponse)
from app.api.schemas_studio import DraftWriteRequest
from app.services.studio_outline_generation import (batch_response, scoped_run, create_batch,
    list_batches, select_candidate, save_selected_outline, cancel_uncertain_batch)

router = APIRouter(prefix="/projects/{project_id}/chapters/{chapter_id}/outline")


@router.post("/generations", response_model=OutlineGenerationResponse, status_code=201)
async def generate(project_id: UUID, chapter_id: UUID, payload: OutlineGenerationRequest,
                   session: AsyncSession = Depends(get_db_session)):
    return await create_batch(session, project_id, chapter_id, **payload.model_dump())


@router.get("/generations", response_model=list[OutlineGenerationResponse])
async def list_generations(project_id: UUID, chapter_id: UUID, session: AsyncSession = Depends(get_db_session)):
    return await list_batches(session, project_id, chapter_id)


@router.get("/generations/{run_id}", response_model=OutlineGenerationResponse)
async def get_generation(project_id: UUID, chapter_id: UUID, run_id: UUID, session: AsyncSession = Depends(get_db_session)):
    return batch_response(await scoped_run(session, project_id, chapter_id, run_id))


@router.post("/generations/{run_id}/cancel", response_model=OutlineGenerationResponse)
async def cancel_generation(project_id: UUID, chapter_id: UUID, run_id: UUID, session: AsyncSession = Depends(get_db_session)):
    return await cancel_uncertain_batch(session, project_id, chapter_id, run_id)


@router.post("/generations/{run_id}/select", response_model=OutlineSelectionResponse)
async def choose_candidate(project_id: UUID, chapter_id: UUID, run_id: UUID, payload: OutlineSelectionRequest,
                           session: AsyncSession = Depends(get_db_session)):
    return await select_candidate(session, project_id, chapter_id, run_id, **payload.model_dump())


@router.put("/content", response_model=DocumentVersionResponse)
async def save_outline(project_id: UUID, chapter_id: UUID, payload: DraftWriteRequest,
                       session: AsyncSession = Depends(get_db_session)):
    return await save_selected_outline(session, project_id, chapter_id, **payload.model_dump())
