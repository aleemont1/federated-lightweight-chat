"""
Unit tests for the GossipService class.
Tests initialization, start/stop logic, data pushing, and error handling.
"""

# Disable this warning as it's due to pytest's syntax
# pylint: disable=redefined-outer-name

# Disable this warning as it's necessary to access the protected values only in this unit test scope.
# pylint: disable=protected-access

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.gossip import GossipService


@pytest.fixture
def mock_storage() -> MagicMock:
    """Fixture to mock StorageService."""
    storage = MagicMock()
    storage.get_all_messages.return_value = []
    return storage


@pytest.fixture
def gossip_service(mock_storage) -> GossipService:
    """Fixture to create a GossipService instance."""
    return GossipService(
        storage=mock_storage,
        node_id="test_node",
        node_addr="http://localhost:8000",
        peers=["http://peer1:8000", "http://peer2:8000"],
    )


@pytest.mark.asyncio
async def test_initialization(gossip_service) -> None:
    """Test correct initialization of the service."""
    assert gossip_service.node_id == "test_node"
    assert gossip_service.node_addr == "http://localhost:8000"
    assert len(gossip_service.peers) == 2
    assert gossip_service._running is False


def test_stop(gossip_service) -> None:
    """Test the stop method sets running flag to False."""
    gossip_service._running = True
    gossip_service.stop()
    assert gossip_service._running is False


@pytest.mark.asyncio
async def test_push_data_no_messages(gossip_service, mock_storage) -> None:
    """Test that _push_data returns early if no messages to sync."""
    mock_storage.get_all_messages.return_value = []

    # Patch httpx.AsyncClient to ensure no request is made
    with patch("httpx.AsyncClient", autospec=True) as mock_client_cls:
        await gossip_service._push_data("http://target:8000")

        mock_storage.get_all_messages.assert_called_once()
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_push_data_success(gossip_service, mock_storage) -> None:
    """Test successful data push to a target."""
    # Mock a message object
    mock_msg = MagicMock()
    mock_msg.model_dump.return_value = {"id": "1", "content": "test"}
    mock_storage.get_all_messages.return_value = [mock_msg]

    # Mock httpx client and post method
    mock_client = AsyncMock()
    mock_client_cls = MagicMock(return_value=mock_client)
    # Need to mock context manager behavior for `async with httpx.AsyncClient...`
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", new=mock_client_cls):
        await gossip_service._push_data("http://target:8000")

        mock_client.post.assert_called_once_with(
            "http://target:8000/replication",
            json={"id": "1", "content": "test"},
            headers={"X-Origin-Node": "http://localhost:8000"},
        )


@pytest.mark.asyncio
async def test_push_data_http_error(gossip_service, mock_storage, caplog) -> None:
    """Test handling of HTTP errors during push."""
    # Ensure we capture logs to verify error handling

    caplog.set_level(logging.DEBUG)

    mock_msg = MagicMock()
    mock_msg.model_dump.return_value = {"data": "test"}
    mock_storage.get_all_messages.return_value = [mock_msg]

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    # Simulate HTTP error
    # Note: httpx.HTTPError requires a message in newer versions
    error = httpx.HTTPError("Connection failed")
    mock_client.post.side_effect = error

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Should not raise exception, but log it
        await gossip_service._push_data("http://target:8000")

        # Check if the error was logged (using partial match since log format varies)
        assert "Failed to push gossip" in caplog.text
        assert "Connection failed" in caplog.text


@pytest.mark.asyncio
async def test_start_loop_execution(gossip_service) -> None:
    """
    Test that start() runs the loop and calls _push_data.
    We mock asyncio.sleep to raise CancelledError to exit the loop gracefully
    after one iteration, simulating a stop signal or task cancellation.
    """

    # Mock secrets.choice to return a specific peer
    with (
        patch("secrets.choice", return_value="http://peer1:8000"),
        patch.object(gossip_service, "_push_data", new_callable=AsyncMock) as mock_push,
        patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]) as mock_sleep,
    ):

        try:
            await gossip_service.start()
        except asyncio.CancelledError:
            pass  # Expected to break the loop

        # Verify startup sequence
        assert gossip_service._running is False  # Should be False after loop breaks/exception

        # Verify logic inside loop
        mock_push.assert_called_with("http://peer1:8000")

        # Verify sleep called (initial warmup + loop sleep)
        assert mock_sleep.call_count >= 1
