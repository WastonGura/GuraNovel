from fastapi import APIRouter

from app.api.routes_chapters import router as chapters_router
from app.api.routes_chapter_production import router as chapter_production_router
from app.api.routes_chapter_production_v2 import router as chapter_production_v2_router
from app.api.routes_documents import router as documents_router
from app.api.routes_health import router as health_router
from app.api.routes_projects import router as projects_router
from app.api.routes_project_creation import router as project_creation_router
from app.api.routes_project_maintenance import router as project_maintenance_router
from app.api.routes_reader_panel import router as reader_panel_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router, tags=["health"])
api_router.include_router(documents_router, tags=["documents"])
api_router.include_router(projects_router, tags=["projects"])
api_router.include_router(project_creation_router, tags=["project-creation"])
api_router.include_router(project_maintenance_router, tags=["project-maintenance"])
api_router.include_router(chapters_router, tags=["chapters"])
api_router.include_router(chapter_production_router, tags=["chapter-production"])
api_router.include_router(chapter_production_v2_router, tags=["chapter-production-v2"])
api_router.include_router(reader_panel_router, tags=["reader-panel"])
