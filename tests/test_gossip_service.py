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
def mock_redis():
    """Fixture that provides a mocked Redis client."""
    mock = AsyncMock()
    mock.keys.return_value = []
    mock.get.return_value = None
    mock.set = AsyncMock()
    mock.delete = AsyncMock()
    return mock


@pytest.fixture
def gossip_service(mock_storage, mock_redis) -> GossipService:
    """Fixture that provides a GossipService instance with mocked storage and Redis."""
    with patch("src.services.gossip.redis.from_url", return_value=mock_redis):
        with patch("os.getenv", return_value="redis://localhost:6379"):
            service = GossipService(
                storage=mock_storage,
                node_id="test_node",
                node_addr="http://me:8000",
                peers=[],  # Legacy list
            )
            # Manually inject the mock redis if the patch didn't catch (double safety)
            service.redis = mock_redis
            return service


@pytest.mark.asyncio
async def test_initialization(gossip_service) -> None:
    """Test that the service is initialized correctly."""
    assert gossip_service.node_id == "test_node"
    assert gossip_service._running is False
    assert gossip_service.redis is not None


def test_stop(gossip_service) -> None:
    """Test that the stop method sets the _running flag to False."""
    gossip_service._running = True
    gossip_service.stop()
    assert gossip_service._running is False


@pytest.mark.asyncio
async def test_push_data_no_messages(gossip_service, mock_storage) -> None:
    """If there are no messages in storage, no HTTP call should be made."""
    mock_storage.get_all_messages.return_value = []

    with patch("httpx.AsyncClient") as mock_client:
        await gossip_service._push_data("http://target:8000")
        mock_storage.get_all_messages.assert_called_once()
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_push_data_with_messages(gossip_service, mock_storage) -> None:
    """If there are messages, they should be sent to the target."""
    msg_mock = MagicMock()
    msg_mock.model_dump.return_value = {"content": "hello"}
    mock_storage.get_all_messages.return_value = [msg_mock]

    mock_http_client = AsyncMock()
    mock_client_cls = MagicMock(return_value=mock_http_client)
    mock_http_client.__aenter__.return_value = mock_http_client
    mock_http_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", new=mock_client_cls):
        await gossip_service._push_data("http://target:8000")

        mock_storage.get_all_messages.assert_called_once()
        mock_http_client.post.assert_called_once_with(
            "http://target:8000/api/replication",
            json={"content": "hello"},
            headers={"X-Origin-Node": "http://me:8000"},
        )


@pytest.mark.asyncio
async def test_push_data_http_error_handling(gossip_service, mock_storage) -> None:
    """If the HTTP call fails, it should log the error and not crash."""
    msg_mock = MagicMock()
    msg_mock.model_dump.return_value = {"data": "test"}
    mock_storage.get_all_messages.return_value = [msg_mock]

    mock_http_client = AsyncMock()
    mock_http_client.__aenter__.return_value = mock_http_client
    mock_http_client.__aexit__.return_value = None
    mock_http_client.post.side_effect = httpx.HTTPError("Connection failed")

    with patch("httpx.AsyncClient", return_value=mock_http_client):
        await gossip_service._push_data("http://target:8000")
        mock_http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_start_loop_logic(gossip_service) -> None:
    """
    Tests the start loop logic.
    Mocks get_active_peers to ensure the loop logic (choice -> push_data) executes.
    """
    # Use a counter to ensure we loop at least once
    call_counter = {"count": 0}

    async def sleep_side_effect(seconds) -> None:
        call_counter["count"] += 1
        # 1st call is warmup (3s)
        # 2nd call is first loop iteration (2s) -> Stop after this
        if call_counter["count"] >= 2:
            gossip_service._running = False
        return

    gossip_service._push_data = AsyncMock()

    # Mock get_active_peers to return a list, otherwise secrets.choice isn't called
    gossip_service.get_active_peers = AsyncMock(return_value=["http://peer1:8000"])

    with (
        patch("asyncio.sleep", side_effect=sleep_side_effect) as mock_sleep,
        patch("secrets.choice") as mock_choice,
    ):
        mock_choice.return_value = "http://peer1:8000"

        await gossip_service.start()

        # Verifications
        mock_choice.assert_called_with(["http://peer1:8000"])
        gossip_service._push_data.assert_called_with("http://peer1:8000")
        assert mock_sleep.call_count >= 2


@pytest.mark.asyncio
async def test_start_loop_exception_handling(gossip_service, caplog) -> None:
    """
    Test that the main loop catches generic exceptions and continues running.
    """
    call_counter = {"count": 0}

    async def sleep_side_effect(seconds) -> None:
        call_counter["count"] += 1
        if call_counter["count"] >= 2:
            gossip_service._running = False
        return

    # Ensure get_active_peers returns something so secrets.choice is reached
    gossip_service.get_active_peers = AsyncMock(return_value=["http://peer1:8000"])

    with (
        patch("asyncio.sleep", side_effect=sleep_side_effect) as mock_sleep,
        patch("secrets.choice", side_effect=Exception("Random failure")),
        caplog.at_level("ERROR"),
    ):
        await gossip_service.start()

        # Corrected log string expectation
        assert "Gossip loop error: Random failure" in caplog.text
        assert mock_sleep.call_count >= 2


@pytest.mark.asyncio
async def test_sync_room_messages(gossip_service, mock_storage):
    """
    Test active pull of messages via sync_room_messages.
    """
    room_id = "general"
    mock_msg = {
        "message_id": "1",
        "content": "synced",
        "room_id": "general",
        "sender_id": "peer",
        "vector_clock": {},
        "created_at": 100,
    }

    # Mock get_active_peers to return a peer so logic proceeds
    gossip_service.get_active_peers = AsyncMock(return_value=["http://peer1:8000"])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [mock_msg]
        mock_client.get.return_value = mock_response

        mock_storage.message_exists.return_value = False

        await gossip_service.sync_room_messages(room_id)

        # Verify logic
        mock_client.get.assert_called()
        # Verify params
        args, kwargs = mock_client.get.call_args
        # Check that the URL matches the peer returned by get_active_peers
        assert "http://peer1:8000/api/messages" in args[0]
        assert kwargs["params"]["room_id"] == room_id
        mock_storage.add_message.assert_called_once()
