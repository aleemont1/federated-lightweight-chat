"""
Handles background syncing between nodes
"""

import asyncio
import logging
import os
import secrets
from typing import List

import httpx
import redis.asyncio as redis

from src.core.message import Message
from src.services.storage import StorageService

logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class GossipService:
    """Background sync service"""

    def __init__(self, storage: StorageService, node_id: str, node_addr: str, peers: list[str]):
        # 'peers' arg is kept for backward compat, but we mostly ignore it now in favor of Redis
        self.storage = storage
        self.node_id = node_id
        self.node_addr = node_addr
        self._running = False

        # Redis connection for Discovery
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis = redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]

    async def register_self(self) -> None:
        """Registers this node in the central Redis registry."""
        key = f"discovery:node:{self.node_id}"
        # Set with expiration (TTL) acting as a heartbeat
        await self.redis.set(key, self.node_addr, ex=10)

    async def get_active_peers(self) -> List[str]:
        """Fetches active peers from Redis."""
        keys = await self.redis.keys("discovery:node:*")
        peers = []
        for key in keys:
            # Don't gossip with self
            if key == f"discovery:node:{self.node_id}":
                continue
            addr = await self.redis.get(key)
            if addr:
                peers.append(addr)
        return peers

    async def start(self) -> None:
        """Start node sync and heartbeat loop"""
        self._running = True
        logger.info("[%s] Gossip/Discovery service started.", self.node_id)

        try:
            while self._running:
                try:
                    # 1. Heartbeat: I am alive
                    await self.register_self()

                    # 2. Discover Peers
                    dynamic_peers = await self.get_active_peers()

                    if dynamic_peers:
                        # 3. Gossip: Push data to a random peer
                        target = secrets.choice(dynamic_peers)
                        await self._push_data(target)

                except Exception as e:
                    logger.error("Gossip loop error: %s", e)

                await asyncio.sleep(2)  # Beat every 2 seconds

        finally:
            self._running = False
            # Cleanup self from discovery
            try:
                await self.redis.delete(f"discovery:node:{self.node_id}")
            except Exception as e:
                logger.warning("Failed to cleanup discovery key during shutdown: %s", e)
            logger.info("Gossip service stopped.")

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
        # FIX: Await the async call to get the actual list
        active_peers = await self.get_active_peers()

        if not active_peers:
            return

        # Try up to 3 random peers to spread load and increase chance of finding data
        # We use SystemRandom to ensure cryptographic randomness is not needed but good practice
        targets = secrets.SystemRandom().sample(active_peers, min(len(active_peers), 3))

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

        # Ensure valid URL format
        base_url = target.rstrip("/")
        # Traefik internal routing or direct IP routing
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
