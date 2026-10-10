"""In memory node state (vector clocks etc.)"""

from dataclasses import dataclass
from typing import Dict

from src.core.message import VectorClock
from src.core.vector_clock import increment, merge


@dataclass
class NodeState:
    """
    Keeps the in-memory state of the node.
    """

    node_id: str
    # Mapping vector clocks to each room
    room_clocks: Dict[str, VectorClock]
    # This replica's key in the clocks (see StorageService.writer_id); the username if unset.
    writer_id: str = ""

    def __post_init__(self) -> None:
        self.writer_id = self.writer_id or self.node_id

    def get_clock(self, room_id: str) -> VectorClock:
        """Returns the vector clock of the node corresponding to the specified room."""
        return self.room_clocks.get(room_id, {self.writer_id: 0})

    def update_clock(self, room_id: str, remote_clock: VectorClock) -> VectorClock:
        """Merges the local and the remote vector clock to ensure consinstency"""
        current = self.get_clock(room_id)
        merged = merge(current, remote_clock)
        self.room_clocks[room_id] = merged
        return merged

    def next_clock(self, room_id: str) -> VectorClock:
        """
        The clock of this replica's next message in the room. Not stored: the caller commits
        it with update_clock once the message is saved, so a failed write burns no sequence number.
        """
        return increment(self.get_clock(room_id), self.writer_id)
