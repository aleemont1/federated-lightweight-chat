"""
Unit tests for the WebSocket connection manager.
"""

from unittest.mock import AsyncMock

import pytest

from src.core.message import Message
from src.services.websocket import ConnectionManager


@pytest.mark.asyncio
async def test_connect_and_disconnect():
    """Connecting tracks the socket per room, disconnecting forgets it."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    await manager.connect(websocket, "general")
    websocket.accept.assert_awaited_once()
    assert manager.active_connections == {"general": [websocket]}

    manager.disconnect(websocket, "general")
    manager.disconnect(websocket, "general")  # double disconnect is harmless
    assert not manager.active_connections


@pytest.mark.asyncio
async def test_broadcast_only_to_the_message_room():
    """Sockets watching other rooms don't receive the message."""
    manager = ConnectionManager()
    here, elsewhere = AsyncMock(), AsyncMock()
    await manager.connect(here, "general")
    await manager.connect(elsewhere, "random")
    message = Message(room_id="general", sender_id="alice", content="hello")

    await manager.broadcast(message)

    here.send_json.assert_awaited_once_with(message.model_dump(mode="json"))
    elsewhere.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_survives_broken_socket():
    """One dead socket doesn't stop delivery to the others."""
    manager = ConnectionManager()
    broken, healthy = AsyncMock(), AsyncMock()
    broken.send_json.side_effect = RuntimeError("Socket Closed")
    await manager.connect(broken, "general")
    await manager.connect(healthy, "general")

    await manager.broadcast(Message(room_id="general", sender_id="alice", content="hello"))

    healthy.send_json.assert_awaited_once()
