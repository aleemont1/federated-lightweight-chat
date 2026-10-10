"""
Vector clock operations, used to track causal relationships between messages.
"""

from src.core.message import VectorClock


def increment(clock: VectorClock, node_id: str) -> VectorClock:
    """Returns a copy of `clock` with `node_id`'s counter incremented."""
    return {**clock, node_id: clock.get(node_id, 0) + 1}


def merge(clock_a: VectorClock, clock_b: VectorClock) -> VectorClock:
    """Returns the entry-wise maximum of two clocks."""
    return {node: max(clock_a.get(node, 0), clock_b.get(node, 0)) for node in clock_a.keys() | clock_b.keys()}
