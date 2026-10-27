import logging

from fastapi import FastAPI

from app import __version__
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title=settings.app_name, version=__version__)
    app.include_router(health_router)
    app.include_router(documents_router)
    return app


app = create_app()
