"""
Unit tests for explicit room synchronization (Anti-Entropy).
"""

# pylint: disable=duplicate-code
# pylint: disable=redefined-outer-name
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.gossip import GossipService


@pytest.fixture
def mock_storage():
    """Returns mock storage"""
    return MagicMock()


@pytest.fixture
def gossip_service(mock_storage):
    """Returns gossip service"""
    return GossipService(
        storage=mock_storage, node_id="test_node", node_addr="http://me:8000", peers=["http://peer:8000"]
    )


@pytest.mark.asyncio
async def test_sync_room_messages_success(gossip_service, mock_storage):
    """
    Test that sync_room_messages pulls data from a peer and saves it.
    """
    room_id = "general"
    remote_msg = {
        "message_id": "1",
        "room_id": "general",
        "sender_id": "peer",
        "content": "synced",
        "vector_clock": {},
        "created_at": 100.0,
    }

    # Mock HTTP Client response
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [remote_msg]
        mock_client.get.return_value = mock_response

        # Simulate message NOT existing locally
        mock_storage.message_exists.return_value = False

        await gossip_service.sync_room_messages(room_id)

        # Verify call to peer
        mock_client.get.assert_called()
        call_args = mock_client.get.call_args
        assert room_id in call_args[1]["params"]["room_id"]

        # Verify persistence
        mock_storage.add_message.assert_called_once()
        saved_msg = mock_storage.add_message.call_args[0][0]
        assert saved_msg.content == "synced"


@pytest.mark.asyncio
async def test_sync_room_messages_idempotency(gossip_service, mock_storage):
    """
    Test that we don't save duplicate messages during sync.
    """
    room_id = "general"
    remote_msg = {
        "message_id": "1",
        "content": "duplicate",
        "room_id": "general",
        "sender_id": "peer",
        "vector_clock": {},
        "created_at": 100,
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [remote_msg]
        mock_client.get.return_value = mock_response

        # Simulate message ALREADY existing locally
        mock_storage.message_exists.return_value = True

        await gossip_service.sync_room_messages(room_id)

        # Verify NO persistence
        mock_storage.add_message.assert_not_called()
