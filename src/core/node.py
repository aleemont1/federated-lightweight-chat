"""In memory node state (vector clocks etc.)"""

from dataclasses import dataclass
from typing import Dict
from datclasses import datclass

from src.core.message import Message, VectorClock
from src.core.vector_clock import VectorClockService

@dataclass
class NodeState:
    """
    Keeps the in-memory state of the node.
    """
    node_id: str
    # Mapping vector clocks to each room
    room_clocks: Dict[str, VectorClock]

    def join_room(self, room_id: str):
        if room_id not in self.room_clocks:
            self.room_clocks[room_id] = {self.node_id: 0}

    def get_clock(self, room_id: str) -> VectorClock:
        return self.room_clocks.get(room_id, {self.node_id: 0})

    def update_clock(self, room_id: str, remote_clock: VectorClock) -> VectorClock:
        """Merges the local and the remote vector clock to ensure consinstency"""
        current = self.get_clock(room_id)
        merged = VectorClockService.merge(current, remote_clock)
        self.room_clocks[room_id] = merged
        return merged

    def increment_clock(self, room_id: str) -> VectorClock
        new_clock = VectorClockService.increment(self.get_clock(room_id), self.node_id)
        self.room_clocks[room_id] = new_clock
        return new_clock
