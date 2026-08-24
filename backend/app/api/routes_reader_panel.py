"""Thin, exact-scope HTTP routes for Reader Panel lifecycle operations."""

from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import get_reader_panel_service
from app.api.schemas_reader_panel import (
    ReaderPanelDetailResponse,
    ReaderPanelEmptyRequest,
    ReaderPanelStartRequest,
)
from app.services.reader_panel_service import ReaderPanelInvalidStateError, ReaderPanelService
from app.workflows.reader_panel import PanelMode, get_mode_preset_config


router = APIRouter(prefix="/projects/{project_id}/chapters/{chapter_id}/reader-panels")

_ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"description": "Reader panel scope not found"},
    status.HTTP_409_CONFLICT: {"description": "Reader panel lifecycle conflict"},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Invalid request or budget"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Provider unavailable"},
}


@router.post(
    "",
    response_model=ReaderPanelDetailResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def start_reader_panel(
    project_id: UUID,
    chapter_id: UUID,
    payload: ReaderPanelStartRequest,
    service: ReaderPanelService = Depends(get_reader_panel_service),
) -> object:
    mode = PanelMode(payload.mode)
    config = get_mode_preset_config(mode)
    if payload.config_overrides is not None:
        config = replace(
            config,
            **payload.config_overrides.model_dump(exclude_none=True),
        )
    result = await service.initialize_session(
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=payload.document_id,
        document_version_id=payload.document_version_id,
        mode=mode,
        config=config,
        test_goals=payload.test_goals,
        target_audience=payload.target_audience,
        idempotency_key=payload.idempotency_key,
    )
    if result is None:
        result = {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "document_id": payload.document_id,
            "document_version_id": payload.document_version_id,
            "mode": mode.value,
            "status": "off",
            "is_noop": True,
        }
    if isinstance(result, dict) or result.is_noop:
        content = ReaderPanelDetailResponse.model_validate(result).model_dump(
            mode="json", exclude_none=True
        )
        content.update({"session_id": None, "workflow_run_id": None})
        return JSONResponse(content=content, status_code=status.HTTP_201_CREATED)
    if result.session_id is None:
        raise ReaderPanelInvalidStateError()
    return await service.get_scoped_session(
        project_id,
        chapter_id,
        result.session_id,
    )


@router.get(
    "",
    response_model=list[ReaderPanelDetailResponse],
    response_model_exclude_none=True,
    responses=_ERROR_RESPONSES,
)
async def list_reader_panels(
    project_id: UUID,
    chapter_id: UUID,
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=20, ge=1, le=100),
    include_initial_reports: bool = Query(default=False),
    include_transcript: bool = Query(default=False),
    data_limit: int = Query(default=50, ge=1, le=200),
    service: ReaderPanelService = Depends(get_reader_panel_service),
) -> list[dict]:
    return await service.list_scoped_sessions(
        project_id,
        chapter_id,
        offset=offset,
        limit=limit,
        include_initial_reports=include_initial_reports,
        include_transcript=include_transcript,
        data_limit=data_limit,
    )


@router.get(
    "/{session_id}",
    response_model=ReaderPanelDetailResponse,
    response_model_exclude_none=True,
    responses=_ERROR_RESPONSES,
)
async def get_reader_panel(
    project_id: UUID,
    chapter_id: UUID,
    session_id: UUID,
    include_initial_reports: bool = Query(default=False),
    include_transcript: bool = Query(default=False),
    data_limit: int = Query(default=50, ge=1, le=200),
    service: ReaderPanelService = Depends(get_reader_panel_service),
) -> dict:
    return await service.get_scoped_session(
        project_id,
        chapter_id,
        session_id,
        include_initial_reports=include_initial_reports,
        include_transcript=include_transcript,
        data_limit=data_limit,
    )


@router.post(
    "/{session_id}/cancel",
    response_model=ReaderPanelDetailResponse,
    response_model_exclude_none=True,
    responses=_ERROR_RESPONSES,
)
async def cancel_reader_panel(
    project_id: UUID,
    chapter_id: UUID,
    session_id: UUID,
    _payload: ReaderPanelEmptyRequest,
    service: ReaderPanelService = Depends(get_reader_panel_service),
) -> dict:
    return await service.cancel_scoped_session(project_id, chapter_id, session_id)


@router.post(
    "/{session_id}/resume",
    response_model=ReaderPanelDetailResponse,
    response_model_exclude_none=True,
    responses=_ERROR_RESPONSES,
)
async def resume_reader_panel(
    project_id: UUID,
    chapter_id: UUID,
    session_id: UUID,
    _payload: ReaderPanelEmptyRequest,
    service: ReaderPanelService = Depends(get_reader_panel_service),
) -> dict:
    return await service.resume_scoped_session(project_id, chapter_id, session_id)
