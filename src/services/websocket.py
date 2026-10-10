"""
WebSocket Connection Manager.
Tracks this node's browser connections per room and pushes new messages to them.
Cross-node delivery is the replication service's job (see gossip.py).
"""

import logging
from typing import Dict, List

from fastapi import WebSocket

from src.core.message import Message

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Keeps the open WebSockets of this node, grouped by room."""

    def __init__(self) -> None:
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str) -> None:
        """Accepts a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.setdefault(room_id, []).append(websocket)
        logger.info("WS Connected to %s. Total: %d", room_id, len(self.active_connections[room_id]))

    def disconnect(self, websocket: WebSocket, room_id: str) -> None:
        """Removes a WebSocket connection."""
        connections = self.active_connections.get(room_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.active_connections.pop(room_id, None)

    async def broadcast(self, message: Message) -> None:
        """Sends a message to the local clients watching its room."""
        payload = message.model_dump(mode="json")
        for connection in list(self.active_connections.get(message.room_id, [])):
            try:
                await connection.send_json(payload)
            except Exception as e:
                logger.warning("Error sending to WS: %s", e)


# Singleton instance
manager = ConnectionManager()
