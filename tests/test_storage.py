"""Unit tests for storage service and CRUD ops on DB."""

# Disabling pylint warning as it is a false positive due to pytest fixtures.
# pylint: disable=redefined-outer-name
import time

import pytest

from src.core.message import Message
from src.services.storage import StorageService


@pytest.fixture
def storage_service(tmp_path):
    """
    Creates a temporary file-based DB.
    tmp_path is a built-in pytest fixture that provides a temporary directory
    """
    db_file = tmp_path / "test_node.db"
    return StorageService(str(db_file))


def test_add_and_retreive_message(storage_service):
    """
    Tests message addition and retrieval
    """
    msg = Message(
        room_id="test_room",
        sender_id="test_sender",
        content="Unit testing",
        vector_clock={"node_a": 1, "node_b": 2},
    )

    storage_service.add_message(msg)

    assert storage_service.message_exists(msg.message_id)
    retrieved_msg = storage_service.get_all_messages()

    assert len(retrieved_msg) == 1
    loaded_msg = retrieved_msg[0]

    assert loaded_msg.message_id == msg.message_id
    assert loaded_msg.room_id == "test_room"
    assert loaded_msg.sender_id == "test_sender"
    assert loaded_msg.content == "Unit testing"

    assert isinstance(loaded_msg.vector_clock, dict)
    assert loaded_msg.vector_clock["node_a"] == 1


def test_save_and_load_snapshot(storage_service):
    """Test saving and loading vector clock snapshots."""
    room_id = "general"
    vector_clock = {"alice": 10, "bob": 5}

    # 1. Save Snapshot
    storage_service.save_snapshot(room_id, vector_clock)

    # 2. Load Snapshot
    loaded_clock, timestamp = storage_service.load_snapshot(room_id)

    assert loaded_clock == vector_clock
    assert timestamp > 0.0

    # 3. Overwrite Snapshot (should replace old one)
    new_clock = {"alice": 12, "bob": 6}
    storage_service.save_snapshot(room_id, new_clock)

    loaded_clock_2, timestamp_2 = storage_service.load_snapshot(room_id)
    assert loaded_clock_2 == new_clock
    assert timestamp_2 >= timestamp


def test_load_snapshot_not_found(storage_service):
    """Test loading a snapshot that doesn't exist."""
    clock, timestamp = storage_service.load_snapshot("unknown_room")
    assert clock is None
    assert timestamp == 0.0


def test_get_messages_after(storage_service):
    """Test retrieving messages after a certain timestamp (Delta Recovery)."""
    room_id = "chat1"
    base_time = time.time()

    # Create messages with different timestamps
    msg1 = Message(room_id=room_id, sender_id="a", content="old", created_at=base_time - 100)
    msg2 = Message(room_id=room_id, sender_id="a", content="snapshot_point", created_at=base_time)
    msg3 = Message(room_id=room_id, sender_id="a", content="new1", created_at=base_time + 10)
    msg4 = Message(room_id=room_id, sender_id="a", content="new2", created_at=base_time + 20)

    # Add all to DB
    for m in [msg1, msg2, msg3, msg4]:
        storage_service.add_message(m)

    # Query: Get messages strictly AFTER base_time (simulating recovery from snapshot at msg2)
    # Note: float comparison can be tricky, usually we use the snapshot timestamp
    delta_messages = storage_service.get_messages_after(room_id, base_time)

    assert len(delta_messages) == 2
    assert delta_messages[0].content == "new1"
    assert delta_messages[1].content == "new2"


def test_get_all_room_ids_mixed_sources(storage_service):
    """
    Test retrieving room IDs from both messages table and snapshots table.
    Ensures union logic works correctly.
    """
    # Room A only has messages
    msg_a = Message(room_id="room_A", sender_id="a", content="hi")
    storage_service.add_message(msg_a)

    # Room B only has a snapshot (e.g., all messages compacted/deleted in a hypothetical future feature)
    storage_service.save_snapshot("room_B", {"node": 1})

    # Room C has both
    msg_c = Message(room_id="room_C", sender_id="a", content="hi")
    storage_service.add_message(msg_c)
    storage_service.save_snapshot("room_C", {"node": 5})

    rooms = storage_service.get_all_room_ids()

    assert len(rooms) == 3
    assert "room_A" in rooms
    assert "room_B" in rooms
    assert "room_C" in rooms


def test_get_latest_clock(storage_service):
    """
    Test latest vector clock retreival
    """
    msg1 = Message(room_id="r", sender_id="s", content="1", vector_clock={"node_a": 10})
    msg2 = Message(
        room_id="r",
        sender_id="s",
        content="2",
        vector_clock={"node_a": 25, "node_b": 10},
    )

    storage_service.add_message(msg1)
    storage_service.add_message(msg2)

    assert storage_service.get_latest_clock("node_a") == 25
    assert storage_service.get_latest_clock("node_b") == 10
    assert storage_service.get_latest_clock("node_c") == 0


def test_peers_management(storage_service):
    """Test adding and retrieving peers."""
    room = "lobby"
    peer = "http://10.0.0.1:8000"

    storage_service.add_peer(room, peer)
    peers = storage_service.get_peers(room)

    assert len(peers) == 1
    assert peers[0] == peer


def test_get_all_room_messages(storage_service):
    """
    Test retrieval of messages for a specific room with pagination.
    """
    room_target = "target_room"
    room_other = "other_room"

    # Add messages to target room (ordered by time)
    for i in range(5):
        msg = Message(
            room_id=room_target,
            sender_id="tester",
            content=f"msg_{i}",
            created_at=time.time() + i,  # Ensure ordering
        )
        storage_service.add_message(msg)

    # Add message to another room (should be ignored)
    msg_other = Message(room_id=room_other, sender_id="tester", content="noise")
    storage_service.add_message(msg_other)

    # 1. Fetch all messages for target room
    messages = storage_service.get_all_room_messages(room_target, limit=10)
    assert len(messages) == 5
    assert all(m.room_id == room_target for m in messages)
    assert messages[0].content == "msg_0"

    # 2. Test Pagination (Limit)
    messages_limit = storage_service.get_all_room_messages(room_target, limit=2)
    assert len(messages_limit) == 2
    assert messages_limit[0].content == "msg_0"
    assert messages_limit[1].content == "msg_1"

    # 3. Test Pagination (Offset)
    messages_offset = storage_service.get_all_room_messages(room_target, limit=2, offset=2)
    assert len(messages_offset) == 2
    assert messages_offset[0].content == "msg_2"
    assert messages_offset[1].content == "msg_3"

    # 4. Test Empty Room
    empty = storage_service.get_all_room_messages("empty_room")
    assert len(empty) == 0
