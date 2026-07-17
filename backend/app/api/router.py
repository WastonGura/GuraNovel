from fastapi import APIRouter

from app.api.routes_chapters import router as chapters_router
from app.api.routes_chapter_production import router as chapter_production_router
from app.api.routes_documents import router as documents_router
from app.api.routes_health import router as health_router
from app.api.routes_projects import router as projects_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router, tags=["health"])
api_router.include_router(documents_router, tags=["documents"])
api_router.include_router(projects_router, tags=["projects"])
api_router.include_router(chapters_router, tags=["chapters"])
api_router.include_router(chapter_production_router, tags=["chapter-production"])
