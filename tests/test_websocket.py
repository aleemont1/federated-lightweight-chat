"""
Unit tests for the Redis-backed WebSocket Manager.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.message import Message
from src.services.websocket import ConnectionManager


@pytest.fixture
def mock_redis():
    """Mock the Redis client."""
    redis = AsyncMock()
    redis.pubsub.return_value = AsyncMock()
    return redis


@pytest.fixture
def manager(mock_redis):
    """Initialize manager with mocked Redis."""
    # We patch the 'redis_client' imported in the module
    with patch("src.services.websocket.redis_client", mock_redis):
        mgr = ConnectionManager()
        # Reset internal state cleanly for each test
        mgr.active_connections = {}
        mgr.pubsub_tasks = {}
        yield mgr


@pytest.mark.asyncio
async def test_connect_and_disconnect(manager):
    """Test connecting adds to local state and disconnect removes it."""
    websocket = AsyncMock()
    room_id = "general"

    # Mock _subscribe to prevent actual background tasks in this unit test
    with patch.object(manager, "_subscribe_to_redis", new_callable=AsyncMock) as mock_sub:
        await manager.connect(websocket, room_id)
        assert websocket in manager.active_connections[room_id]
        mock_sub.assert_awaited_once_with(room_id)

    # Disconnect
    # Mock _unsubscribe to prevent error on missing task
    with patch.object(manager, "_unsubscribe_from_redis") as mock_unsub:
        manager.disconnect(websocket, room_id)
        # Verify socket removed
        assert room_id not in manager.active_connections or not manager.active_connections[room_id]
        mock_unsub.assert_called_once_with(room_id)


@pytest.mark.asyncio
async def test_broadcast_local(manager):
    """Test that broadcast_to_local sends message to connected sockets."""
    websocket = AsyncMock()
    room_id = "general"
    # FIX: Use a Message object, not a dict
    message = Message(room_id=room_id, sender_id="alice", content="hello")

    # Manually setup connection state to avoid complexity of connect()
    manager.active_connections[room_id] = [websocket]

    await manager.broadcast_to_local(message, room_id)

    # The manager should serialize the message to JSON (dict) before sending
    expected_payload = message.model_dump(mode="json")
    websocket.send_json.assert_awaited_once_with(expected_payload)


@pytest.mark.asyncio
async def test_publish(manager, mock_redis):
    """Test that publish sends the message to Redis."""
    room_id = "general"
    # FIX: Use a Message object, not a dict
    message = Message(room_id=room_id, sender_id="alice", content="hello")

    await manager.publish(message, room_id)

    mock_redis.publish.assert_awaited_once()
    # Check arguments: channel and serialized message
    args = mock_redis.publish.call_args
    assert args[0][0] == f"chat:{room_id}"
    assert '"content":"hello"' in args[0][1] or "'content': 'hello'" in args[0][1]  # Check JSON substring
