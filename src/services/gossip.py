"""
Handles background syncing between nodes
"""

import asyncio
import logging
import secrets

import httpx

from src.core.message import Message
from src.services.storage import StorageService

logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class GossipService:
    """Background sync service"""

    def __init__(self, storage: StorageService, node_id: str, node_addr: str, peers: list[str]):
        self.storage = storage
        self.node_id = node_id
        self.node_addr = node_addr
        self.peers = peers
        self._running = False

    async def start(self) -> None:
        """Start node sync"""

        self._running = True
        logger.info("[%s] Started syncing. Initial peers: %s", self.node_id, self.peers)

        try:
            # Initial warmup delay
            await asyncio.sleep(3)

            while self._running:
                try:
                    if self.peers:
                        # Choose a random peer to sync with
                        target = secrets.choice(self.peers)
                        if target != self.node_addr:
                            await self._push_data(target)
                # pylint: disable=broad-exception-caught
                except Exception as e:
                    logger.error("Gossip error: %s", e)

                await asyncio.sleep(2)

        finally:
            # Ensure the flag is reset to False when the task stops, crashes, or is cancelled.
            logger.info("[%s] Gossip loop terminated.", self.node_id)
            self._running = False

    def stop(self) -> None:
        """
        Signals the gossip loop to stop running.
        The loop will check this flag and exit gracefully.
        """
        logger.info("[%s] Stopping gossip service...", self.node_id)
        self._running = False

    async def sync_room_messages(self, room_id: str) -> None:
        """
        Actively pulls messages for a specific room from random peers.
        This is an 'Anti-Entropy' pull mechanism triggered by user actions (joining a room).
        """
        if not self.peers:
            return

        # Try up to 3 random peers to spread load and increase chance of finding data
        # We use SystemRandom to ensure cryptographic randomness is not needed but good practice
        targets = secrets.SystemRandom().sample(self.peers, min(len(self.peers), 3))

        async with httpx.AsyncClient(timeout=3.0) as client:
            for target in targets:
                if target == self.node_addr:
                    continue

                try:
                    # Construct URL. Assuming standard API structure based on peer base URL.
                    base_url = target.rstrip("/")
                    url = f"{base_url}/api/messages"

                    # Fetch messages for the room
                    response = await client.get(url, params={"room_id": room_id, "limit": 100})

                    if response.status_code == 200:
                        messages_data = response.json()
                        new_count = 0

                        for msg_data in messages_data:
                            # Parse and validate
                            try:
                                msg = Message(**msg_data)

                                # Idempotency Check: Don't overwrite if exists
                                if not self.storage.message_exists(msg.message_id):
                                    self.storage.add_message(msg)
                                    # Note: We rely on standard add_message logic.
                                    # In a full vector clock implementation, we might merge clocks here too.
                                    new_count += 1
                            except Exception as e:
                                logger.warning("Failed to parse synced message: %s", e)

                        if new_count > 0:
                            logger.info(
                                "Synced %d new messages for room '%s' from %s", new_count, room_id, target
                            )
                            # Optimization: If we found data, maybe we can stop?
                            # Or keep going to ensure convergence. Let's keep going for robustness.

                except Exception as e:
                    logger.warning("Failed to sync room '%s' from peer %s: %s", room_id, target, e)

    async def _push_data(self, target: str) -> None:
        """Push data to a target node"""
        msgs = self.storage.get_all_messages()
        if not msgs:
            return

        # Ensure we don't double-slash if target has trailing slash
        base_url = target.rstrip("/")
        # FIX: The API router is mounted at /api, so the endpoint is /api/replication
        replication_url = f"{base_url}/api/replication"

        async with httpx.AsyncClient(timeout=1.0) as client:
            for msg in msgs:
                try:
                    payload = msg.model_dump(mode="json")
                    await client.post(
                        replication_url,
                        json=payload,
                        headers={"X-Origin-Node": self.node_addr},
                    )
                except httpx.HTTPError as e:
                    logger.debug("Failed to push gossip to %s: %s", target, e)
