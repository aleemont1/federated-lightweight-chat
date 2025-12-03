"""In memory node state (vector clocks etc.)"""

from dataclasses import dataclass
from typing import Dict

from src.core.message import VectorClock
from src.core.vector_clock import VectorClockService


@dataclass
class NodeState:
    """
    Keeps the in-memory state of the node.
    """

    node_id: str
    # Mapping vector clocks to each room
    room_clocks: Dict[str, VectorClock]

    def join_room(self, room_id: str) -> None:
        """Joins a node to the specified room"""
        if room_id not in self.room_clocks:
            self.room_clocks[room_id] = {self.node_id: 0}

    def get_clock(self, room_id: str) -> VectorClock:
        """Returns the vector clock of the node corresponding to the specified room."""
        return self.room_clocks.get(room_id, {self.node_id: 0})

    def update_clock(self, room_id: str, remote_clock: VectorClock) -> VectorClock:
        """Merges the local and the remote vector clock to ensure consinstency"""
        current = self.get_clock(room_id)
        merged = VectorClockService.merge(current, remote_clock)
        self.room_clocks[room_id] = merged
        return merged

    def increment_clock(self, room_id: str) -> VectorClock:
        """Increments the vector clock counter of the node in the specified room"""
        new_clock = VectorClockService.increment(self.get_clock(room_id), self.node_id)
        self.room_clocks[room_id] = new_clock
        return new_clock
