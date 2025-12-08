"""
Unit tests for the NodeService.
Tests lifecycle management, state recovery, and interaction with sub-services.
"""

# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.node import LocalNodeService


# Fixture to provide a fresh instance of the service for each test
@pytest.fixture
def node_service():
    """Fixture that provides a fresh LocalNodeService instance."""
    return LocalNodeService()


# Helper to mock dependencies consistently and fix the Async/Sync mismatch
@pytest.fixture
def mock_dependencies():
    """
    Fixture that mocks StorageService, GossipService, and settings.
    Configures the GossipService.start method to be an AsyncMock.
    """
    with (
        patch("src.services.node.StorageService") as mock_storage_cls,
        patch("src.services.node.GossipService") as mock_gossip_cls,
        patch("src.services.node.settings"),
    ):

        # --- Configure Gossip Mock ---
        mock_gossip_instance = mock_gossip_cls.return_value

        # This satisfies asyncio.create_task(self._gossip.start())
        mock_gossip_instance.start = AsyncMock()

        # 'stop' is synchronous, so a standard MagicMock is fine.
        mock_gossip_instance.stop = MagicMock()

        # --- Configure Storage Mock ---
        mock_storage_instance = mock_storage_cls.return_value
        # Default behavior: empty rooms to avoid iteration errors
        mock_storage_instance.get_all_room_ids.return_value = []

        # Return the mocks so tests can customize return values
        yield mock_storage_cls, mock_gossip_cls


@pytest.mark.asyncio
async def test_initialize_success(node_service, mock_dependencies):
    """Test successful initialization flow."""
    (
        mock_storage_cls,
        mock_gossip_cls,
    ) = mock_dependencies
    gossip_instance = mock_gossip_cls.return_value
    storage_instance = mock_storage_cls.return_value

    await node_service.initialize("test_user")

    assert node_service.is_initialized()
    assert node_service.state.node_id == "test_user"

    assert node_service.storage is storage_instance
    # Verify Gossip started correctly
    mock_gossip_cls.assert_called_once()
    gossip_instance.start.assert_called_once()


@pytest.mark.asyncio
async def test_initialize_idempotency_and_conflict(node_service, mock_dependencies):
    """Test initialization guards (Idempotency and Conflict)."""
    # 1. First Init
    await node_service.initialize("user_a")
    assert node_service.state.node_id == "user_a"

    # 2. Same User -> Should just return (no error)
    await node_service.initialize("user_a")

    # 3. Different User -> Should Raise Error
    with pytest.raises(ValueError, match="Node is already initialized"):
        await node_service.initialize("user_b")


@pytest.mark.asyncio
async def test_state_recovery_snapshot_and_delta(node_service, mock_dependencies):
    """Test the 'Smart Recovery' logic with Snapshot + Delta."""
    mock_storage_cls, _ = mock_dependencies
    storage_instance = mock_storage_cls.return_value

    # Setup Data for Recovery
    # 1. Simulate finding a room "lobby"
    storage_instance.get_all_room_ids.return_value = ["lobby"]

    # 2. Simulate loading a snapshot for that room
    # Returns (VectorClock, timestamp)
    storage_instance.load_snapshot.return_value = ({"alice": 10, "bob": 5}, 100.0)

    # 3. Simulate finding new messages after the snapshot (Delta)
    mock_msg = MagicMock()
    mock_msg.vector_clock = {"alice": 11, "bob": 5}
    storage_instance.get_messages_after.return_value = [mock_msg]

    # Action
    await node_service.initialize("alice")

    # Assertions
    state = node_service.state
    assert "lobby" in state.room_clocks
    # Expect: alice=11 (from delta), bob=5 (from snapshot)
    assert state.room_clocks["lobby"] == {"alice": 11, "bob": 5}

    # Verify Storage calls
    storage_instance.load_snapshot.assert_called_with("lobby")
    storage_instance.get_messages_after.assert_called_with("lobby", 100.0)


@pytest.mark.asyncio
async def test_shutdown_lifecycle(node_service, mock_dependencies):
    """Test graceful shutdown."""
    mock_storage_cls, mock_gossip_cls = mock_dependencies
    gossip_instance = mock_gossip_cls.return_value
    storage_instance = mock_storage_cls.return_value

    # Init first to set up state
    await node_service.initialize("user1")

    # Inject some state to be saved
    node_service.state.room_clocks["room_X"] = {"u1": 99}

    # Action
    await node_service.shutdown()

    # Assertions
    assert not node_service.is_initialized()
    assert node_service.state is None

    # Verify Gossip stopped
    gossip_instance.stop.assert_called_once()

    # Verify Snapshot saved
    storage_instance.save_snapshot.assert_called_with("room_X", {"u1": 99})
