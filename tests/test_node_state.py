"""Unit tests for NodeState class"""

from src.core.node_state import NodeState


def test_node_init():
    """Test node state initialization"""
    node = NodeState(node_id="alice", room_clocks={})
    assert node.node_id == "alice"
    assert not node.room_clocks


def test_join_new_room():
    """Test clock initialization on room joining."""
    node = NodeState(node_id="alice", room_clocks={})
    room = "test"

    node.join_room(room)

    assert room in node.room_clocks
    assert node.room_clocks[room] == {"alice": 0}


def test_join_existing_room_does_not_reset_clock():
    """If node attempts to join an already joined room,
    its clock does't have to be resetted."""
    initial_clock = {"alice": 5, "bob": 3}
    node = NodeState(node_id="alice", room_clocks={"general": initial_clock})

    node.join_room("general")

    assert node.room_clocks["general"] == initial_clock
    assert node.room_clocks["general"]["alice"] == 5


def test_get_clock_default():
    """When asking for an unknown room's clock, it should return a safe value."""
    node = NodeState(node_id="alice", room_clocks={})

    clock = node.get_clock("not_joined_room")
    assert clock == {"alice": 0}
    assert "not_joined_room" not in node.room_clocks


def test_increment_clock():
    """Verifies that incrementing clock updates current node's counter"""
    node = NodeState(node_id="alice", room_clocks={})
    room = "chat1"
    node.join_room(room)

    new_clock = node.increment_clock(room)

    assert new_clock["alice"] == 1
    assert node.get_clock(room)["alice"] == 1

    node.room_clocks[room]["bob"] = 5
    node.increment_clock(room)

    final_clock = node.get_clock(room)

    assert final_clock["alice"] == 2
    assert final_clock["bob"] == 5


def test_update_clock_merge():
    """Tests vector clocks' merge logic for replication."""
    node = NodeState(node_id="alice", room_clocks={"general": {"alice": 1, "bob": 1}})

    remote_clock = {"alice": 1, "bob": 2, "charlie": 5}

    merged_clock = node.update_clock("general", remote_clock)

    expected = {"alice": 1, "bob": 2, "charlie": 5}

    assert merged_clock == expected
    assert node.room_clocks["general"] == expected


def test_update_clock_concurrent():
    """
    Tests concurrency clocks updating.
    Local: Alice=2, Bob=0
    Remote: Alice=0, Bob=2

    Expected: Alice=2, Bob=2
    """

    node = NodeState(node_id="alice", room_clocks={"general": {"alice": 2}})
    remote_clock = {"bob": 2}

    node.update_clock("general", remote_clock)

    assert node.room_clocks["general"]["alice"] == 2
    assert node.room_clocks["general"]["bob"] == 2
