"""
Handles background syncing between nodes
"""

import asyncio
import logging
import random
import httpx

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

    async def start(self):
        """Start node sync"""

        self._running = True
        logger.info("[%s] Started syncing. Initial peers: %s", self.node_id, self.peers)

        await asyncio.sleep(3)

        while self._running:
            try:
                if self.peers:
                    # Choose a random peer to sync with
                    target = random.choice(self.peers)
                    if target != self.node_addr:
                        await self._push_data(target)
            # pylint: disable=broad-exception-caught
            except Exception as e:
                logger.error("Gossip error: %s", e)
            await asyncio.sleep(2)

    async def _push_data(self, target: str):
        """Push data to a target node"""
        msgs = self.storage.get_all_messages()
        if not msgs:
            return

        async with httpx.AsyncClient(timeout=1.0) as client:
            for msg in msgs:
                try:
                    payload = msg.model_dump(mode="json")
                    await client.post(
                        f"{target}/replication", json=payload, headers={"X-Origin-Node": self.node_addr}
                    )
                # pylint: disable=broad-exception-caught
                except Exception:
                    pass
