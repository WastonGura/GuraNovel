from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.app_debug,
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
