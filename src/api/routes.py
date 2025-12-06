"""
API Routes definition.
Handles Authentication, Chat operations, and Node management.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies import get_current_user
from src.core.auth_models import LoginRequest, User
from src.core.message import Message
from src.services.auth import current_auth_provider
from src.services.node import node_service

logger = logging.getLogger(__name__)

router = APIRouter()


class SendMessageRequest(BaseModel):
    """Payload for sending a message."""

    content: str
    room_id: str


# === PUBLIC ROUTES ===


@router.post("/login", response_model=User)
async def login(credentials: LoginRequest) -> type[User]:
    """
    Login endpoint.
    1. Authenticates user via AuthProvider
    2. Initializes the local NodeService
    """

    user = await current_auth_provider.authenticate(credentials)

    if not user or not user.is_authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    if not node_service.is_initialized():
        try:
            await node_service.initialize(user.username)
        except Exception as e:
            logger.error("Initialization failed: %s", e)

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Node initialization failed: {str(e)}",
            )

    return User


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Returns the node status"""
    is_ready = node_service.is_initialized()
    node_id = node_service.state.node_id if is_ready and node_service.state else None

    return {"status": "online", "initialized": is_ready, "node_id": node_id}


# === Protected routes ===


@router.get("/me", response_model=User)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    """Returns the current user profile"""
    return current_user


@router.get("/messages", response_model=List[Message])
async def get_messages(
    room_id: str = "general", limit: int = 50, offset: int = 0, current_user: User = Depends(get_current_user)
) -> List[Message]:
    """
    Retrieves all messages for a room
    """
    if not node_service.storage:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Storage not ready.")

    messages = node_service.storage.get_all_room_messages(room_id=room_id, limit=limit, offset=offset)

    return messages
