"""
Node controllers, allows to centralize the node's services in
a structured and coherent object.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from src.config.settings import settings
from src.core.node_state import NodeState
from src.services.gossip import GossipService
from src.services.storage import StorageService

logger = logging.getLogger(__name__)


class INodeService(ABC):
    """
    Abstract Interface for node's management service.
    Defines the contract for the node's state initialization
    and management.
    """

    @property
    @abstractmethod
    def state(self) -> Optional[NodeState]:
        """Returns the current node's state (if set)"""
        pass

    @property
    @abstractmethod
    def storage(self) -> Optional[StorageService]:
        """Returns the active storage service (if set)"""
        pass

    @abstractmethod
    def is_initialized(self) -> bool:
        """Checks if node is active and ready"""
        pass

    @abstractmethod
    async def initialize(self, user_id: str) -> None:
        """
        Start node's services for the specified user.
        Configures DB, restores state and starts gossip.
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Gracefully shuts the node down, cleaning resources.
        """
        pass


class LocalNodeService(INodeService):
    """
    Local implementation with manual initialization,
    created to develop and test API routes before implementing
    the whole user management and node orchestration services.
    """

    def __init__(self) -> None:
        self._state: Optional[NodeState] = None
        self._storage: Optional[StorageService] = None
        self._gossip: Optional[GossipService] = None
        self._gossip_task: Optional[asyncio.Task[None]] = None

    @property
    def state(self) -> Optional[NodeState]:
        return self._state

    @property
    def storage(self) -> Optional[StorageService]:
        return self._storage

    def is_initialized(self) -> bool:
        return self._state is not None

    async def initialize(self, user_id: str) -> None:
        if self.is_initialized():
            if self._state and self._state.node_id != user_id:
                raise ValueError(f"Node is already initialized as {self._state.node_id}")
            return

        logger.info("Initializing node for user: %s", user_id)

        db_path = settings.db_name or f"{user_id}.db"
        self._storage = StorageService(db_path)

        self._state = NodeState(node_id=user_id, room_clocks={})

        # Restore state if there is any saved.
        if self._storage:
            room_ids = self._storage.get_all_room_ids()

            if self._state:
                for room_id in room_ids:
                    self._state.join_room(room_id)

                    snapshot_clock, last_time = self._storage.load_snapshot(room_id)
                    if snapshot_clock:
                        self._state.update_clock(room_id, snapshot_clock)
                        logger.debug("Loaded snapshot for %s (timestamp: %s)", room_id, last_time)

                    delta_messages = self._storage.get_messages_after(room_id, last_time)
                    if delta_messages:
                        logger.info("Replaying %d messages for room %s", len(delta_messages), room_id)
                        for msg in delta_messages:
                            self._state.update_clock(room_id, msg.vector_clock)

                logger.info("State restored. Active rooms: %s", list(self._state.room_clocks.keys()))

        await self._start_gossip(user_id)

    async def _start_gossip(self, user_id: str) -> None:
        peers_list = [p.strip() for p in settings.peers.split(",") if p.strip()]
        my_addr = settings.advertised_addr or f"http://localhost:{settings.server_port}"

        if self._storage:
            self._gossip = GossipService(
                storage=self._storage, node_id=user_id, node_addr=my_addr, peers=peers_list
            )
            if self._gossip:
                self._gossip_task = asyncio.create_task(self._gossip.start())

    async def shutdown(self) -> None:
        """Graceful shutdown with Snapshot saving."""
        logger.info("Shutting down services...")

        # 1. Stop Gossip
        if self._gossip_task:
            if self._gossip:
                self._gossip.stop()  # Assumi che GossipService abbia un metodo stop
            self._gossip_task.cancel()
            try:
                await self._gossip_task
            except asyncio.CancelledError:
                pass

        # 2. Save Snapshots (Critical for fast restart)
        if self._state and self._storage:
            logger.info("Saving state snapshots...")
            for room_id, clock in self._state.room_clocks.items():
                self._storage.save_snapshot(room_id, clock)

        self._state = None
        self._storage = None
        self._gossip = None
        logger.info("Node shutdown complete.")


node_service: INodeService = LocalNodeService()
