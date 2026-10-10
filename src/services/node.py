"""
Node controller: centralizes the node's storage, clocks and replication
in a single object.
"""

import asyncio
import logging
from typing import List, Optional

from src.config.settings import settings
from src.core.message import Message
from src.core.node_state import NodeState
from src.services.gossip import GossipService
from src.services.storage import StorageService
from src.services.websocket import manager

logger = logging.getLogger(__name__)


class NodeService:
    """Owns the node's replica: storage, in-memory vector clocks and the replication tasks."""

    def __init__(self) -> None:
        self.state: Optional[NodeState] = None
        self.storage: Optional[StorageService] = None
        self._gossip: Optional[GossipService] = None
        self._tasks: List[asyncio.Task[None]] = []

    async def initialize(self, user_id: str) -> None:
        """
        Start node's services for the specified user.
        Opens the DB, rebuilds the clocks from it, catches up with peers and starts replication.
        """
        if self.state:
            if self.state.node_id != user_id:
                raise ValueError(f"Node is already initialized as {self.state.node_id}")
            return

        logger.info("Initializing node for user: %s", user_id)
        self.storage = StorageService(settings.db_name or f"{user_id}.db")
        self.state = NodeState(
            node_id=user_id, room_clocks=self.storage.room_clocks(), writer_id=self.storage.writer_id(user_id)
        )
        logger.info("State restored. Active rooms: %s", list(self.state.room_clocks))

        self._gossip = GossipService(
            storage=self.storage,
            node_id=user_id,
            node_addr=settings.advertised_addr,
            on_message=self.ingest,
            on_batch=self.ingest_batch,
        )
        # Catch up before serving, so the user sees the full history at once. Not needed for
        # correctness: a node whose DB was lost is a new writer and can't reuse sequence numbers.
        await self._gossip.sync_once(drain=True)
        self._tasks = [asyncio.create_task(self._gossip.run()), asyncio.create_task(self._gossip.listen())]

    async def ingest_batch(self, messages: List[Message]) -> int:
        """
        Applies a batch of messages received from a peer in a single transaction.
        Returns how many were new.
        """
        if not self.state or not self.storage or not messages:
            return 0
        new_msgs = self.storage.add_messages(messages)
        for msg in new_msgs:
            self.state.update_clock(msg.room_id, msg.vector_clock)
            await manager.broadcast(msg)
        return len(new_msgs)

    async def ingest(self, message: Message) -> bool:
        """
        Applies a message received from a peer. Idempotent, so both replication paths can
        deliver it. Returns True if the message was new.
        """
        return (await self.ingest_batch([message])) == 1

    async def send(self, room_id: str, content: str) -> Message:
        """Creates a message authored by this node, stores it and replicates it."""
        if not self.state or not self.storage or not self._gossip:
            raise RuntimeError("Node not initialized")
        message = Message(
            room_id=room_id,
            sender_id=self.state.node_id,
            writer_id=self.state.writer_id,
            content=content,
            vector_clock=self.state.next_clock(room_id),
        )
        # Stored first: if this raises, the clock hasn't moved and the next message reuses the
        # number. Advancing it anyway would leave a gap no replica could ever fill.
        self.storage.add_message(message)
        self.state.update_clock(room_id, message.vector_clock)
        await manager.broadcast(message)
        await self._gossip.publish(message)
        return message

    async def shutdown(self) -> None:
        """Stops replication and releases resources. All state is already on disk."""
        logger.info("Shutting down services...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._gossip:
            await self._gossip.close()

        self.state = None
        self.storage = None
        self._gossip = None
        self._tasks = []
        logger.info("Node shutdown complete.")


node_service = NodeService()
