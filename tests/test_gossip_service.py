"""
Unit tests specific for the GossipService logic.
Focuses on the internal loop, peer selection, and HTTP interaction.
"""

# pylint: disable=redefined-outer-name
# pylint: disable=protected-access

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.gossip import GossipService


@pytest.fixture
def mock_storage() -> MagicMock:
    """Fixture that provides a mocked StorageService."""
    return MagicMock()


@pytest.fixture
def gossip_service(mock_storage) -> GossipService:
    """Fixture that provides a GossipService instance with mocked storage."""
    peers = ["http://peer1:8000", "http://peer2:8000"]
    return GossipService(
        storage=mock_storage,
        node_id="test_node",
        node_addr="http://me:8000",
        peers=peers,
    )


@pytest.mark.asyncio
async def test_initialization(gossip_service) -> None:
    """Test that the service is initialized with correct parameters."""
    assert gossip_service.node_id == "test_node"
    assert gossip_service.peers == ["http://peer1:8000", "http://peer2:8000"]
    assert gossip_service._running is False


def test_stop(gossip_service) -> None:
    """Test that the stop method sets the _running flag to False."""
    gossip_service._running = True
    gossip_service.stop()
    assert gossip_service._running is False


@pytest.mark.asyncio
async def test_push_data_no_messages(gossip_service, mock_storage) -> None:
    """If there are no messages in storage, no HTTP call should be made."""
    mock_storage.get_all_messages.return_value = []

    # Patch httpx to ensure it is not called
    with patch("httpx.AsyncClient") as mock_client:
        await gossip_service._push_data("http://target:8000")

        # Verify: Storage consulted, but HTTP client never instantiated
        mock_storage.get_all_messages.assert_called_once()
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_push_data_with_messages(gossip_service, mock_storage) -> None:
    """If there are messages, they should be sent to the target."""
    # Setup mock data
    msg_mock = MagicMock()
    msg_mock.model_dump.return_value = {"content": "hello"}
    mock_storage.get_all_messages.return_value = [msg_mock]

    # Setup HTTP Client mock (Async Context Manager)
    mock_http_client = AsyncMock()
    mock_client_cls = MagicMock(return_value=mock_http_client)
    # Handle async with client:
    mock_http_client.__aenter__.return_value = mock_http_client
    mock_http_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", new=mock_client_cls):
        await gossip_service._push_data("http://target:8000")

        # Verify
        # 1. Messages retrieved?
        mock_storage.get_all_messages.assert_called_once()
        # 2. Correct post request made?
        mock_http_client.post.assert_called_once_with(
            "http://target:8000/replication",
            json={"content": "hello"},
            headers={"X-Origin-Node": "http://me:8000"},
        )


@pytest.mark.asyncio
async def test_push_data_http_error_handling(gossip_service, mock_storage) -> None:
    """If the HTTP call fails, it should log the error and not crash."""
    msg_mock = MagicMock()
    msg_mock.model_dump.return_value = {"data": "test"}
    mock_storage.get_all_messages.return_value = [msg_mock]

    # Setup client that raises exception
    mock_http_client = AsyncMock()
    mock_http_client.__aenter__.return_value = mock_http_client
    mock_http_client.__aexit__.return_value = None

    # Create a mock side_effect
    error_instance = httpx.HTTPError("Connection failed")
    mock_http_client.post.side_effect = error_instance

    with patch("httpx.AsyncClient", return_value=mock_http_client):
        # Should not raise exceptions (code has try/except)
        await gossip_service._push_data("http://target:8000")

        # If we are here, test passed (exception caught)
        mock_http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_start_loop_logic(gossip_service) -> None:
    """
    Tests the start loop logic.
    We need to be creative to test an infinite loop.
    """

    # This function will be called instead of sleep
    async def break_loop_after_one_iteration(seconds) -> None:
        # If it's the first call (warmup 3s), let it pass
        if seconds == 3:
            return
        # On second call (inside loop, 2s), stop everything
        gossip_service._running = False
        return

    # Replace real _push_data with a mock to verify the call
    # Using AsyncMock because it is awaited
    gossip_service._push_data = AsyncMock()

    with (
        patch("asyncio.sleep", side_effect=break_loop_after_one_iteration) as mock_sleep,
        patch("secrets.choice") as mock_choice,
    ):

        mock_choice.return_value = "http://peer1:8000"

        await gossip_service.start()

        # Verifications
        # 1. Did it choose a peer?
        mock_choice.assert_called_with(gossip_service.peers)

        # 2. Did it call push_data towards that peer?
        gossip_service._push_data.assert_called_with("http://peer1:8000")

        # 3. Did it sleep? (At least twice: warmup + loop)
        assert mock_sleep.call_count >= 2


@pytest.mark.asyncio
async def test_start_loop_exception_handling(gossip_service, caplog) -> None:
    """
    Test that the main loop catches generic exceptions and continues running (or logs them).
    We force an exception in 'secrets.choice' or '_push_data' and ensure the loop doesn't crash immediately.
    """

    # We want the loop to run once, hit exception, then stop on the next sleep
    async def break_loop_after_exception(seconds):
        if seconds == 3:
            return  # warmup
        # We are inside the loop now. After the exception is caught, it sleeps.
        # We stop here.
        gossip_service._running = False
        return

    # Force an exception during the loop body execution
    with (
        patch("asyncio.sleep", side_effect=break_loop_after_exception) as mock_sleep,
        patch("secrets.choice", side_effect=Exception("Random failure")),
        caplog.at_level("ERROR"),  # Capture ERROR logs
    ):
        await gossip_service.start()

        # Verify the exception was caught and logged
        assert "Gossip error: Random failure" in caplog.text
        assert mock_sleep.call_count >= 2
