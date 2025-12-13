"""
Unit tests for internal service implementation details and edge cases.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import APIError

from src.services.gossip import GossipService
from src.services.orchestrator import OrchestratorService
from src.services.websocket import ConnectionManager

# --- Orchestrator Edge Cases ---


@pytest.fixture
def mock_docker():
    with patch("src.services.orchestrator.docker.from_env") as mock:
        yield mock


def test_orchestrator_restarts_stopped_container(mock_docker):
    """
    Test that if a container exists but is stopped, it gets restarted.
    Target: src/services/orchestrator.py:54-55
    """
    orch = OrchestratorService(max_users=5)
    client = mock_docker.return_value

    # Simulate existing but stopped container
    mock_container = MagicMock()
    mock_container.status = "exited"
    client.containers.get.return_value = mock_container

    orch.spawn_user_node("alice")

    mock_container.start.assert_called_once()
    client.containers.run.assert_not_called()


def test_orchestrator_api_error_handling(mock_docker):
    """
    Test that Docker API errors are caught and re-raised as RuntimeError.
    Target: src/services/orchestrator.py:95-97
    """
    orch = OrchestratorService(max_users=5)
    client = mock_docker.return_value

    # Container not found, proceed to run...
    from docker.errors import NotFound

    client.containers.get.side_effect = NotFound("missing")

    # ... but Run fails
    client.containers.run.side_effect = APIError("Docker Socket Error")

    with pytest.raises(RuntimeError, match="Failed to spawn node"):
        orch.spawn_user_node("bob")


# --- Gossip Edge Cases ---


@pytest.mark.asyncio
async def test_gossip_sync_parsing_error(caplog):
    """
    Test that invalid messages from peers are logged but don't crash sync.
    Target: src/services/gossip.py:138-139
    """
    mock_storage = MagicMock()
    mock_redis = AsyncMock()

    # Setup Gossip
    with patch("src.services.gossip.redis.from_url", return_value=mock_redis):
        with patch("os.getenv"):
            service = GossipService(mock_storage, "me", "http://me", [])

    # Mock Peers
    service.get_active_peers = AsyncMock(return_value=["http://peer"])

    # Mock HTTP to return bad data
    with patch("httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = client

        # FIX: Explicitly create a MagicMock (synchronous) for the response
        # so .json() is a sync call returning the list immediately.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"invalid": "data"}]

        client.get.return_value = mock_response

        await service.sync_room_messages("general")

        # Verify we caught the parsing exception (validation error on Message model)
        assert "Failed to parse synced message" in caplog.text


# --- WebSocket Edge Cases ---


@pytest.mark.asyncio
async def test_websocket_broadcast_error():
    """
    Test that errors sending to a specific socket don't crash the broadcast.
    Target: src/services/websocket.py:84
    """
    mgr = ConnectionManager()

    # Add a mock socket that raises exception on send
    bad_socket = AsyncMock()
    bad_socket.send_json.side_effect = Exception("Socket Closed")

    mgr.active_connections["general"] = [bad_socket]

    msg = MagicMock()
    msg.model_dump.return_value = {}

    # Should not raise exception
    await mgr.broadcast_to_local(msg, "general")

    # Verify we tried
    bad_socket.send_json.assert_called()


@pytest.mark.asyncio
async def test_redis_listener_connection_error(caplog):
    """
    Test handling of Redis connection errors in the listener loop.
    Target: src/services/websocket.py:117-119
    """
    # Ensure we capture logs from the websocket module
    caplog.set_level(logging.ERROR, logger="src.services.websocket")

    with patch("src.services.websocket.redis_client") as mock_redis:
        mgr = ConnectionManager()

        # Mock the pubsub context manager and object
        mock_pubsub = MagicMock()
        # Ensure subscribe is async and raises exception
        mock_pubsub.subscribe = AsyncMock(side_effect=Exception("Redis Down"))

        # CRITICAL FIX: Ensure pubsub() call is synchronous and returns our mock object,
        # NOT a coroutine (which AsyncMock would do by default).
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        # We need to run the task
        await mgr._subscribe_to_redis("general")

        # Retrieve the background task and await it.
        # Since the listener catches the exception and exits, awaiting it
        # guarantees the logging logic has executed.
        task = mgr.pubsub_tasks["general"]
        await task

        assert "Redis listener CRITICAL error" in caplog.text
