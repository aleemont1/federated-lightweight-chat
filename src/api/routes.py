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
from src.services.websocket import manager

logger = logging.getLogger(__name__)

router = APIRouter()


class SendMessageRequest(BaseModel):
    """Payload for sending a message."""

    content: str
    room_id: str


# === PUBLIC ROUTES ===


@router.post("/login", response_model=User)
async def login(credentials: LoginRequest) -> User:
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

    return user


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


@router.post("/messages", response_model=Message, status_code=status.HTTP_201_CREATED)
async def send_message(
    payload: SendMessageRequest, current_user: User = Depends(get_current_user)
) -> Message:

    if not node_service.state or not node_service.storage:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Node service unavailable."
        )

    node_service.state.increment_clock(payload.room_id)
    current_clock = node_service.state.get_clock(payload.room_id)

    new_msg = Message(
        room_id=payload.room_id,
        sender_id=current_user.username,
        content=payload.content,
        vector_clock=current_clock,
    )

    node_service.storage.add_message(new_msg)

    await manager.publish(new_msg, payload.room_id)

    return new_msg


# === Replication route (for P2P) ===


@router.post("/replication")
async def replication_endpoint(message: Message) -> Dict[str, str]:
    """
    Endpoint called by other nodes to gossip messages.
    """
    if not node_service.is_initialized() or not node_service.state or not node_service.storage:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Node not ready to receive replication."
        )

    if node_service.storage.message_exists(message.message_id):
        return {"status": "ignored"}

    # Merge my vector clocks with the received one.
    node_service.state.update_clock(message.room_id, message.vector_clock)

    # Save
    node_service.storage.add_message(message)

    await manager.publish(message, message.room_id)

    logger.info("Replicated message %s from %s", message.message_id, message.sender_id)

    return {"status": "replicated"}
