"""Studio persistence is scoped to an authoritative project and chapter."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.schemas_documents import DocumentVersionResponse
from app.api.schemas_studio import (
    DraftWriteRequest, OutlineApprovalRequest, OutlineApprovalResponse,
    FeedbackRegion, FeedbackResponse, FeedbackSubmissionResponse, FeedbackSubmitRequest,
    FeedbackWriteRequest, RestorePointFeedbackResponse, RestorePointRequest, RestorePointResponse,
)
from app.services.studio_feedback import StudioFeedbackService
from app.services.studio_restore_points import StudioRestorePoints
from app.services.studio_outline import approve_studio_outline
from app.services.studio_draft import save_studio_draft

router = APIRouter(prefix="/projects/{project_id}/chapters/{chapter_id}")


@router.put("/draft/content", response_model=DocumentVersionResponse)
async def save_draft(project_id: UUID, chapter_id: UUID, payload: DraftWriteRequest,
                     session: AsyncSession = Depends(get_db_session)):
    return await save_studio_draft(session, project_id, chapter_id, **payload.model_dump())


@router.post("/outline/approve", response_model=OutlineApprovalResponse)
async def approve_outline(project_id: UUID, chapter_id: UUID, payload: OutlineApprovalRequest,
                          session: AsyncSession = Depends(get_db_session)):
    return await approve_studio_outline(session, project_id, chapter_id, **payload.model_dump())


@router.get("/restore-points", response_model=list[RestorePointResponse])
async def list_restore_points(project_id: UUID, chapter_id: UUID,
                              session: AsyncSession = Depends(get_db_session)):
    return await StudioRestorePoints(session).list(project_id, chapter_id)


@router.post("/restore-points", response_model=RestorePointResponse)
async def create_restore_point(project_id: UUID, chapter_id: UUID, payload: RestorePointRequest,
                               session: AsyncSession = Depends(get_db_session)):
    return await StudioRestorePoints(session).create(project_id, chapter_id, **payload.model_dump())


@router.post("/restore-points/{point_id}/restore", response_model=DocumentVersionResponse)
async def restore_point(project_id: UUID, chapter_id: UUID, point_id: UUID,
                        payload: RestorePointRequest, session: AsyncSession = Depends(get_db_session)):
    return await StudioRestorePoints(session).restore(project_id, chapter_id, point_id,
                                                     **payload.model_dump())


@router.get("/restore-points/{point_id}/feedback", response_model=RestorePointFeedbackResponse)
async def read_restore_point_feedback(project_id: UUID, chapter_id: UUID, point_id: UUID,
                                      session: AsyncSession = Depends(get_db_session)):
    return await StudioRestorePoints(session).feedback(project_id, chapter_id, point_id)


@router.get("/feedback/{region}", response_model=FeedbackResponse)
async def read_feedback(project_id: UUID, chapter_id: UUID, region: FeedbackRegion,
                        session: AsyncSession = Depends(get_db_session)):
    return await StudioFeedbackService(session).read(project_id, chapter_id, region)


@router.put("/feedback/{region}", response_model=FeedbackResponse)
async def write_feedback(project_id: UUID, chapter_id: UUID, region: FeedbackRegion,
                         payload: FeedbackWriteRequest, session: AsyncSession = Depends(get_db_session)):
    return await StudioFeedbackService(session).write(project_id, chapter_id, region, payload)


@router.post("/feedback/{region}/submissions", response_model=FeedbackSubmissionResponse)
async def submit_feedback(project_id: UUID, chapter_id: UUID, region: FeedbackRegion,
                          payload: FeedbackSubmitRequest, session: AsyncSession = Depends(get_db_session)):
    return await StudioFeedbackService(session).submit(project_id, chapter_id, region, payload)


@router.get("/feedback/{region}/submissions/{submission_id}", response_model=FeedbackSubmissionResponse)
async def read_submission(project_id: UUID, chapter_id: UUID, region: FeedbackRegion,
                          submission_id: UUID, session: AsyncSession = Depends(get_db_session)):
    return await StudioFeedbackService(session).read_submission(project_id, chapter_id, region, submission_id)
