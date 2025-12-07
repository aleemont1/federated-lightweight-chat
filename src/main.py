"""
Main entry point for the FastAPI application.
Configures lifespan events and mounts routers.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import router as api_router
from src.config.settings import settings
from src.services.node import node_service

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Disable these warnings as they are false positives caused by fasapi syntax
# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application Lifecycle Manager.
    Handles startup (node init if configured) and shutdown (gossip stop, snapshot).
    """
    logger.info("Starting FLC Node...")

    # Auto-initialize if running in Docker/Cloud with fixed ID
    if settings.node_id:
        logger.info("Auto-initializing node: %s", settings.node_id)
        try:
            await node_service.initialize(settings.node_id)
        except ValueError as e:
            logger.warning("Initialization skipped: %s", e)

    yield

    logger.info("Shutting down FLC Node...")
    await node_service.shutdown()


def create_app() -> FastAPI:
    """Factory to create the app."""
    application = FastAPI(
        title=settings.app_name,
        description="Federated Lightweight Chat API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict this
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    application.include_router(api_router, prefix="/api")

    # Static Files (Frontend)
    try:
        application.mount("/static", StaticFiles(directory="src/static"), name="static")

        @application.get("/")
        async def root() -> FileResponse:
            return FileResponse("src/static/index.html")

    except RuntimeError:
        logger.warning("'src/static' directory not found. UI will not be served.")

    return application


app = create_app()
