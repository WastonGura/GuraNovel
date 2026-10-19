"""Upload staged covers before creation or metadata autosave, and serve stable references."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_project_workspace
from app.workspace import ProjectWorkspace
from app.workspace.cover_store import CoverStore, CoverTooLargeError, MAX_UPLOAD_BYTES

router = APIRouter(prefix="/covers")


class CoverUploadResponse(BaseModel):
    cover: str


@router.post("", response_model=CoverUploadResponse, status_code=201)
async def upload_cover(request: Request, workspace: ProjectWorkspace = Depends(get_project_workspace)) -> CoverUploadResponse:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
            raise CoverTooLargeError()
        content.extend(chunk)
    cover = await run_in_threadpool(CoverStore(workspace).save, bytes(content))
    return CoverUploadResponse(cover=cover)


@router.get("/{identity}")
async def read_cover(identity: str, request: Request, w: int | None = None, workspace: ProjectWorkspace = Depends(get_project_workspace)) -> Response:
    data = await run_in_threadpool(CoverStore(workspace).read, identity, w)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{identity}-{w or "original"}"'}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="image/webp", headers=headers)
