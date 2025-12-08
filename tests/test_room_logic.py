"""
Unit tests for Room Management Logic.
Tests implicit room discovery and message routing.
"""

from src.core.node_state import NodeState


def test_implicit_room_discovery():
    """
    Test that receiving a message for a new room adds it to the node state.
    """
    node = NodeState("test_node", {})
    new_room = "topic_x"

    # Initially room is unknown (get_clock returns default)
    assert new_room not in node.room_clocks

    # Simulating receiving a message (which calls update_clock)
    # This implicitly joins/tracks the room
    remote_clock = {"test_node": 1}
    node.update_clock(new_room, remote_clock)

    # Assert room is now tracked
    assert new_room in node.room_clocks


def test_room_clock_isolation():
    """Ensure clocks for different rooms don't bleed into each other."""
    node = NodeState("alice", {})

    node.increment_clock("room_a")
    node.increment_clock("room_b")
    node.increment_clock("room_b")

    clock_a = node.get_clock("room_a")
    clock_b = node.get_clock("room_b")

    assert clock_a["alice"] == 1
    assert clock_b["alice"] == 2
