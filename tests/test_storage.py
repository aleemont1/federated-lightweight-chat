"""Unit tests for storage service and CRUD ops on DB."""

import pytest
from src.services.storage import StorageService
from src.core.message import Message


@pytest.fixture
def test_storage(tmp_path):
    """
    Creates a temporary file-based DB.
    tmp_path is a built-in pytest fixture that provides a temporary directory
    """
    db_file = tmp_path / "test_node.db"
    return StorageService(str(db_file))


def test_add_and_retreive_message(test_storage):
    """
    Tests message addition and retrieval
    """
    msg = Message(
        room_id="test_room",
        sender_id="test_sender",
        content="Unit testing",
        vector_clock={"node_a": 1, "node_b": 2},
    )

    test_storage.add_message(msg)

    assert test_storage.message_exists(msg.message_id)
    retrieved_msg = test_storage.get_all_messages()

    assert len(retrieved_msg) == 1
    loaded_msg = retrieved_msg[0]

    assert loaded_msg.message_id == msg.message_id
    assert loaded_msg.room_id == "test_room"
    assert loaded_msg.sender_id == "test_sender"
    assert loaded_msg.content == "Unit testing"

    assert isinstance(loaded_msg.vector_clock, dict)
    assert loaded_msg.vector_clock["node_a"] == 1


def test_get_latest_clock(test_storage):
    """
    Test latest vector clock retreival
    """
    msg1 = Message(room_id="r", sender_id="s", content="1", vector_clock={"node_a": 10})
    msg2 = Message(room_id="r", sender_id="s", content="2", vector_clock={"node_a": 25, "node_b": 10})

    test_storage.add_message(msg1)
    test_storage.add_message(msg2)

    assert test_storage.get_latest_clock("node_a") == 25
    assert test_storage.get_latest_clock("node_b") == 10
    assert test_storage.get_latest_clock("node_c") == 0


def test_add_and_retreive_peers(test_storage):
    """
    Test peers retreival
    """

    room_a = "room_a"
    room_b = "room_b"
    peer_1 = "http://peer_1:8000"
    peer_2 = "http://peer_2:8000"

    test_storage.add_peer(room_a, peer_1)
    test_storage.add_peer(room_a, peer_2)
    test_storage.add_peer(room_b, peer_1)

    peers_room_a = test_storage.get_peers(room_a)
    assert len(peers_room_a) == 2
    assert peer_1 in peers_room_a
    assert peer_2 in peers_room_a

    peers_room_b = test_storage.get_peers(room_b)
    assert len(peers_room_b) == 1
    assert peer_1 in peers_room_b
    assert peer_2 not in peers_room_b
