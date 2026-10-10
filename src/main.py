"""
Main entry point for the FastAPI application.
Configures lifespan events and mounts routers.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
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
# Anti-entropy makes a request every few seconds; don't log each one.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Starts the node as NODE_ID (set by the orchestrator) and stops it on shutdown."""
    if settings.node_id:
        await node_service.initialize(settings.node_id)
    else:
        logger.warning("NODE_ID not set: serving without a node.")
    yield
    await node_service.shutdown()


app = FastAPI(title="FLC Node", description="Federated Lightweight Chat API", lifespan=lifespan)
app.include_router(api_router, prefix="/api")
app.mount("/static", StaticFiles(directory="src/static"), name="static")


@app.get("/")
async def root() -> FileResponse:
    """Serves the chat client."""
    return FileResponse("src/static/index.html")
