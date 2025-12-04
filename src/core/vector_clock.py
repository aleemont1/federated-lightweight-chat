"""
Module for vector clock operations

Provides utilities to increment, merge, and compare vector clocks,
which are used to track causal relationships in distributed systems.
"""

from enum import Enum

from src.core.message import VectorClock


class ClockRelation(Enum):
    """
    Enum representing the relationship between two vector clocks.

    Attributes:
        EQUAL: Both clocks are identical.
        BEFORE: First clock happened-before the second.
        AFTER: First clock happened-after the second.
        CONCURRENT: Clocks are concurrent; no causal relationship.
    """

    EQUAL = "EQUAL"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    CONCURRENT = "CONCURRENT"


class VectorClockService:
    """
    Service class providing operations on vector clocks.

    Methods:
        increment: Increments the counter of a node in a vector clock.
        merge: Merges two vector clocks by taking the max value per node.
        compare: Determines causal relationship between two vector clocks.
    """

    @staticmethod
    def increment(clock: VectorClock, node_id: str) -> VectorClock:
        """
        Increment the value of the specified node in the vector clock.

        Args:
            clock (VectorClock): The vector clock to update.
            node_id (str): The node identifier whose counter is incremented.

        Returns:
            VectorClock: A new vector clock with the updated counter.
        """
        new_clock = clock.copy()
        new_clock[node_id] = new_clock.get(node_id, 0) + 1
        return new_clock

    @staticmethod
    def merge(clock_a: VectorClock, clock_b: VectorClock) -> VectorClock:
        """
        Merge two vector clocks by taking the maximum value for each node.

        Args:
            clock_a (VectorClock): First vector clock.
            clock_b (VectorClock): Second vector clock.

        Returns:
            VectorClock: Merged vector clock containing max values per node.
        """
        all_nodes = set(clock_a.keys()) | set(clock_b.keys())
        merged_clock = {}

        for node in all_nodes:
            merged_clock[node] = max(clock_a.get(node, 0), clock_b.get(node, 0))

        return merged_clock

    @staticmethod
    def compare(vc_a: VectorClock, vc_b: VectorClock) -> ClockRelation:
        """
        Compare two vector clocks to determine causal relationship.

        Args:
            vc_a (VectorClock): First vector clock.
            vc_b (VectorClock): Second vector clock.

        Returns:
            ClockRelation: Enum indicating the relationship (EQUAL, BEFORE, AFTER, CONCURRENT).

        Logic:
            - vc_a <= vc_b if for all nodes i: vc_a[i] <= vc_b[i]
            - vc_a < vc_b if vc_a <= vc_b and there exists node j: vc_a[j] < vc_b[j]
            - Concurrent otherwise.
        """
        keys = set(vc_a.keys()) | set(vc_b.keys())

        le_a_b = True  # Is A <= B?
        le_b_a = True  # Is B <= A?

        for k in keys:
            val_a = vc_a.get(k, 0)
            val_b = vc_b.get(k, 0)

            if val_a > val_b:
                le_a_b = False
            if val_b > val_a:
                le_b_a = False

        if le_a_b and le_b_a:
            return ClockRelation.EQUAL
        if le_a_b:
            return ClockRelation.BEFORE
        if le_b_a:
            return ClockRelation.AFTER

        return ClockRelation.CONCURRENT
