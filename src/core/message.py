"""
Define Message structure to ensure consinstency in the system
"""

import uuid
import time
from typing import Dict
from pydantic import BaseModel, Field

# Map (NodeID: Counter)
VectorClock = Dict[str, int]


class Message(BaseModel):
    """Message structure in the app."""

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    room_id: str
    sender_id: str
    content: str
    vector_clock: VectorClock = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
