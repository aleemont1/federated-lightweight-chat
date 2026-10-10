"""
Unit tests for vector clock operations
"""

from src.core.vector_clock import increment, merge


def test_increment_clock():
    """Test that incrementing updates the correct node counter."""
    initial_clock = {"node_a": 1, "node_b": 2}

    # Action: Increment node_a
    new_clock = increment(initial_clock, "node_a")

    # Assertions
    assert new_clock["node_a"] == 2
    assert new_clock["node_b"] == 2
    # Ensure immutability (original shouldn't change)
    assert initial_clock["node_a"] == 1


def test_merge_clocks():
    """Test merging two divergent clocks (taking the max)."""
    clock_a = {"node_a": 3, "node_b": 1, "node_c": 0}
    clock_b = {"node_a": 1, "node_b": 5, "node_d": 2}

    merged = merge(clock_a, clock_b)

    expected = {
        "node_a": 3,  # max(3, 1)
        "node_b": 5,  # max(1, 5)
        "node_c": 0,  # from A
        "node_d": 2,  # from B
    }
    assert merged == expected
