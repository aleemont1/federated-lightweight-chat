"""
Unit tests for the Redis-backed WebSocket Manager.
"""

# Disable these false positives as they are caused by pytest syntax
# pylint: disable=redefined-outer-name

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.message import Message
from src.services.websocket import ConnectionManager


@pytest.fixture
def mock_redis():
    """
    Mock the Redis client.
    We need precise mocking because redis.asyncio has mixed sync/async methods.
    """
    redis = MagicMock()

    # pubsub() is a synchronous method returning a PubSub object
    mock_pubsub_instance = MagicMock()

    # subscribe and unsubscribe are async methods on the PubSub object
    mock_pubsub_instance.subscribe = AsyncMock()
    mock_pubsub_instance.unsubscribe = AsyncMock()

    # listen() returns an async iterator
    mock_pubsub_instance.listen = MagicMock()

    redis.pubsub.return_value = mock_pubsub_instance

    # publish is an async method on the client
    redis.publish = AsyncMock()

    return redis


@pytest.fixture
def manager(mock_redis):
    """Initialize manager with mocked Redis."""
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
    with patch.object(manager, "_unsubscribe_from_redis") as mock_unsub:
        manager.disconnect(websocket, room_id)
        assert room_id not in manager.active_connections or not manager.active_connections[room_id]
        mock_unsub.assert_called_once_with(room_id)


@pytest.mark.asyncio
async def test_broadcast_local(manager):
    """Test that broadcast_to_local sends message to connected sockets."""
    websocket = AsyncMock()
    room_id = "general"
    message = Message(room_id=room_id, sender_id="alice", content="hello")

    manager.active_connections[room_id] = [websocket]

    await manager.broadcast_to_local(message, room_id)

    expected_payload = message.model_dump(mode="json")
    websocket.send_json.assert_awaited_once_with(expected_payload)


@pytest.mark.asyncio
async def test_publish(manager, mock_redis):
    """Test that publish sends the message to Redis."""
    room_id = "general"
    message = Message(room_id=room_id, sender_id="alice", content="hello")

    await manager.publish(message, room_id)

    mock_redis.publish.assert_awaited_once()
    args = mock_redis.publish.call_args
    assert args[0][0] == f"chat:{room_id}"
    assert '"content":"hello"' in args[0][1] or "'content': 'hello'" in args[0][1]


@pytest.mark.asyncio
async def test_redis_listener_resilience(manager, mock_redis, caplog):
    """
    Test that the Redis listener survives a malformed message and continues processing.
    """
    room_id = "general"

    # 1. Setup the mock pubsub to yield messages
    mock_pubsub = mock_redis.pubsub.return_value

    bad_message = {"type": "message", "data": "invalid_json"}
    # Valid JSON string inside 'data'
    good_message_payload = json.dumps(
        {
            "content": "recovered",
            "room_id": "general",
            "sender_id": "test",
            "message_id": "1",
            "vector_clock": {},
            "created_at": 123.456,
        }
    )
    good_message = {"type": "message", "data": good_message_payload}

    # Async iterator that yields messages then sleeps to keep loop alive briefly
    async def msg_gen():
        yield bad_message
        yield good_message
        await asyncio.sleep(0.01)  # Yield control

    # Configure listen() to return the async generator object (result of calling msg_gen)
    mock_pubsub.listen.side_effect = msg_gen

    # 2. Mock broadcast to verify success (we don't want to test WebSocket sending here)
    manager.broadcast_to_local = AsyncMock()

    # Disable this warning as the protected method only needs to be accessed in the scope of this test.
    # pylint: disable=protected-access
    # 3. Start the subscription
    # We call the real method here, which starts the background task
    await manager._subscribe_to_redis(room_id)

    # 4. Wait for the background task to process the generator
    await asyncio.sleep(0.05)

    # 5. Verifications

    # Check if the bad message was logged (proving the first loop iteration ran and caught exception)
    assert "Could not parse Redis message" in caplog.text

    # Check if the good message was processed (proving the loop didn't crash)
    assert manager.broadcast_to_local.call_count == 1

    # Inspect the argument passed to broadcast_to_local to ensure it's correct
    args = manager.broadcast_to_local.call_args[0]
    assert args[0].content == "recovered"
