"""
Define Message structure to ensure consinstency in the system
"""

import time
import uuid
from typing import Dict

from pydantic import BaseModel, Field, model_validator

# Map (writer id: sequence number of that writer's latest message)
VectorClock = Dict[str, int]


class Message(BaseModel):
    """Message structure in the app."""

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    room_id: str
    sender_id: str
    # The replica that wrote it: its key in the vector clock, whose entry is the message's
    # sequence number. Defaults to the sender, which is what it was before writer ids existed.
    writer_id: str = ""
    content: str
    vector_clock: VectorClock = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def _writer_defaults_to_sender(self) -> "Message":
        self.writer_id = self.writer_id or self.sender_id
        return self
