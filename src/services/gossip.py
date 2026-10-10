"""
Replication between nodes. Every node holds a full replica of the chat log.

Two paths, so that correctness never depends on Redis:
- fast path: each new message is published on Redis pub/sub and stored by every listening node;
- repair path (anti-entropy): every GOSSIP_INTERVAL seconds a node sends its summary
  (per room and sender, the highest gap-free sequence number) to a random peer and pulls
  what it lacks. This recovers whatever the fast path lost: node offline, Redis down, partitions.

Redis is also the discovery registry: each node refreshes a key with a TTL as its heartbeat.
"""

import asyncio
import logging
import secrets
from typing import Awaitable, Callable, List

import httpx
import redis.asyncio as redis
from pydantic import ValidationError

from src.config.settings import settings
from src.core.message import Message
from src.services.storage import StorageService

logger = logging.getLogger(__name__)

GOSSIP_INTERVAL = 2.0
HEARTBEAT_TTL = 10
CHANNEL = "chat"


class GossipService:
    """Keeps this node's replica converging with its peers'."""

    def __init__(
        self,
        storage: StorageService,
        node_id: str,
        node_addr: str,
        on_message: Callable[[Message], Awaitable[None]],
    ):
        self.storage = storage
        self.node_addr = node_addr
        self.on_message = on_message
        self._key = f"discovery:node:{node_id}"
        self.redis = redis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=True
        )
        self.http = httpx.AsyncClient(timeout=3.0)

    async def get_active_peers(self) -> List[str]:
        """Addresses of the other nodes whose heartbeat is still alive."""
        peers = []
        async for key in self.redis.scan_iter(match="discovery:node:*"):
            if key != self._key and (addr := await self.redis.get(key)):
                peers.append(addr)
        return peers

    async def pull_from(self, peer: str) -> int:
        """Pulls the messages this node lacks from `peer`. Returns how many were received."""
        response = await self.http.post(f"{peer.rstrip('/')}/api/internal/sync", json=self.storage.summary())
        response.raise_for_status()
        messages = [Message.model_validate(m) for m in response.json()]
        for msg in messages:
            await self.on_message(msg)
        return len(messages)

    async def sync_once(self) -> None:
        """One anti-entropy round: heartbeat, then pull from a random peer. Never raises."""
        try:
            await self.redis.set(self._key, self.node_addr, ex=HEARTBEAT_TTL)
            peers = await self.get_active_peers()
            if peers:
                peer = secrets.choice(peers)
                if received := await self.pull_from(peer):
                    logger.info("Anti-entropy: pulled %d messages from %s", received, peer)
        except Exception as e:
            logger.warning("Anti-entropy round failed: %s", e)

    async def run(self) -> None:
        """Anti-entropy loop."""
        while True:
            await asyncio.sleep(GOSSIP_INTERVAL)
            await self.sync_once()

    async def publish(self, message: Message) -> None:
        """Fast path. Best effort: if Redis is down, anti-entropy delivers the message later."""
        try:
            await self.redis.publish(CHANNEL, message.model_dump_json())
        except redis.RedisError as e:
            logger.warning("Publish of %s failed, leaving it to anti-entropy: %s", message.message_id, e)

    async def listen(self) -> None:
        """Stores every message published by any node. Resubscribes after Redis errors."""
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    async for item in pubsub.listen():
                        if item["type"] != "message":
                            continue
                        try:
                            msg = Message.model_validate_json(item["data"])
                        except ValidationError as e:
                            logger.warning("Dropping malformed pub/sub message: %s", e)
                            continue
                        await self.on_message(msg)
            except Exception as e:
                logger.warning("Pub/sub listener failed, resubscribing: %s", e)
                await asyncio.sleep(GOSSIP_INTERVAL)

    async def close(self) -> None:
        """Leaves the discovery registry and releases connections."""
        try:
            await self.redis.delete(self._key)
        except redis.RedisError as e:
            logger.warning("Failed to cleanup discovery key during shutdown: %s", e)
        await self.redis.aclose()
        await self.http.aclose()
