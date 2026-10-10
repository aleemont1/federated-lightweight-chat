"""
API Routes definition.
Handles chat operations, node health, real-time WebSockets and node-to-node sync.

Browser routes require the session cookie set by /auth. /health is public and /internal
is not routed by the gateway, so only other nodes on the Docker network reach it.
"""

import hmac
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.requests import HTTPConnection
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.core.message import Message
from src.services.node import node_service
from src.services.storage import StorageService, Summary
from src.services.websocket import manager

logger = logging.getLogger(__name__)

router = APIRouter()


SESSION_COOKIE = "flc_session"


class SendMessageRequest(BaseModel):
    """Payload for sending a message."""

    content: str = Field(min_length=1, max_length=4000)
    room_id: str = Field(pattern=r"^[a-z0-9-]{1,32}$")


def _is_token(value: str) -> bool:
    """Constant-time check against this node's token. Fails closed when none is configured."""
    return bool(settings.node_token) and hmac.compare_digest(value.encode(), settings.node_token.encode())


def has_session(conn: HTTPConnection) -> bool:
    """True if the request carries the session cookie of this node's user."""
    return _is_token(conn.cookies.get(SESSION_COOKIE, ""))


def require_session(conn: HTTPConnection) -> None:
    """Dependency: 401 unless the browser logged in through the orchestrator."""
    if not has_session(conn):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Log in through the orchestrator."
        )


def get_storage() -> StorageService:
    """Dependency: the node's storage, or 503 while the node is still initializing."""
    if not node_service.storage:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Node not initialized.")
    return node_service.storage


@router.get("/auth")
async def auth(token: str) -> RedirectResponse:
    """Target of the orchestrator's login link: trades the URL token for a session cookie."""
    if not _is_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login link.")
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(SESSION_COOKIE, token, path="/", samesite="lax", httponly=True, secure=settings.tls)
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    """Ends the session in this browser only: the node keeps running for the others."""
    # Back to "/": without a session the page sends the browser on to the orchestrator.
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax", httponly=True, secure=settings.tls)
    return response


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Returns the node status"""
    node_id = node_service.state.node_id if node_service.state else None
    return {"status": "online", "initialized": node_id is not None, "node_id": node_id}


@router.get("/rooms", response_model=List[str], dependencies=[Depends(require_session)])
async def get_rooms(storage: StorageService = Depends(get_storage)) -> List[str]:
    """Rooms this node holds messages for."""
    return storage.get_known_rooms()


@router.get("/messages", response_model=List[Message], dependencies=[Depends(require_session)])
async def get_messages(
    room_id: str = "general",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    storage: StorageService = Depends(get_storage),
) -> List[Message]:
    """A page of a room's history in causal order (offset=0 is the newest page)."""
    return storage.get_room_messages(room_id=room_id, limit=limit, offset=offset)


@router.post(
    "/messages",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_session), Depends(get_storage)],
)
async def send_message(payload: SendMessageRequest) -> Message:
    """Creates a message on this node and replicates it."""
    return await node_service.send(payload.room_id, payload.content)


# === WebSocket Route ===


@router.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str) -> None:
    """
    Real-time chat endpoint: pushes the room's new messages to the browser.
    Messages are sent upstream via POST /messages; we only read to notice disconnects.
    """
    if not has_session(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect(websocket, room_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, room_id)


# === Anti-entropy route (node to node) ===


@router.post("/internal/sync", response_model=List[Message])
async def internal_sync(have: Summary, storage: StorageService = Depends(get_storage)) -> List[Message]:
    """
    Called by peers during anti-entropy: given what the peer holds,
    returns the messages it is missing.
    """
    return storage.missing_messages(have)
