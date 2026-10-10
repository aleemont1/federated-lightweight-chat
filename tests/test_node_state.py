"""Unit tests for NodeState class"""

from src.core.node_state import NodeState


def test_node_init():
    """Test node state initialization"""
    node = NodeState(node_id="alice", room_clocks={})
    assert node.node_id == "alice"
    assert not node.room_clocks


def test_get_clock_default():
    """When asking for an unknown room's clock, it should return a safe value."""
    node = NodeState(node_id="alice", room_clocks={})

    clock = node.get_clock("not_joined_room")
    assert clock == {"alice": 0}
    assert "not_joined_room" not in node.room_clocks


def test_next_clock_is_only_committed_by_update():
    """next_clock proposes this replica's next entry; nothing moves until update_clock."""
    node = NodeState(node_id="alice", room_clocks={"chat1": {"bob": 5}})

    proposed = node.next_clock("chat1")

    assert proposed == {"alice": 1, "bob": 5}
    assert node.next_clock("chat1") == proposed  # still uncommitted
    node.update_clock("chat1", proposed)
    assert node.next_clock("chat1") == {"alice": 2, "bob": 5}


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
