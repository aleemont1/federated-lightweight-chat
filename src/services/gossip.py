"""
Replication between nodes. Every node holds a full replica of the chat log.

Two paths, so that correctness never depends on Redis:
- fast path: each new message is published on Redis pub/sub and stored by every listening node;
- repair path (anti-entropy): every GOSSIP_INTERVAL seconds a node sends its summary
  (per room and writer, the highest gap-free sequence number) to a random peer and pulls
  what it lacks. This recovers whatever the fast path lost: node offline, Redis down, partitions.

Redis is also the discovery registry: each node refreshes a key with a TTL as its heartbeat.
The last peer list is kept, so anti-entropy keeps working while Redis is down.
"""

import asyncio
import logging
import secrets
import socket
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
PUBLISH_TIMEOUT = 1.0

# A partition black-holes open connections: no reply, no error. The kernel drops a dead one after
# ~10s instead: keepalive probes catch it when idle (the pub/sub listener), the user timeout when a
# command awaits its reply. Not socket_timeout: it would also fire on a merely idle listener.
# Linux names (nodes run in Linux containers); other systems keep what they have, so tests still import.
KEEPALIVE = {
    getattr(socket, name): value
    for name, value in (
        ("TCP_KEEPIDLE", 5),
        ("TCP_KEEPINTVL", 2),
        ("TCP_KEEPCNT", 3),
        ("TCP_USER_TIMEOUT", 10_000),
    )
    if hasattr(socket, name)
}


class GossipService:
    """Keeps this node's replica converging with its peers'."""

    def __init__(
        self,
        storage: StorageService,
        node_id: str,
        node_addr: str,
        on_message: Callable[[Message], Awaitable[bool]],
    ):
        self.storage = storage
        self.node_addr = node_addr
        self.on_message = on_message
        self._key = f"discovery:node:{node_id}"
        self.redis = redis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_keepalive=True,
            socket_keepalive_options=KEEPALIVE,
        )
        self.http = httpx.AsyncClient(timeout=3.0)
        self._peers: List[str] = []  # last known, used when Redis is unreachable

    async def get_active_peers(self) -> List[str]:
        """Addresses of the other nodes whose heartbeat is still alive."""
        peers = []
        async for key in self.redis.scan_iter(match="discovery:node:*"):
            if key != self._key and (addr := await self.redis.get(key)):
                peers.append(addr)
        return peers

    async def pull_from(self, peer: str) -> int:
        """Pulls the messages this node lacks from `peer`. Returns how many were new."""
        response = await self.http.post(f"{peer.rstrip('/')}/api/internal/sync", json=self.storage.summary())
        response.raise_for_status()
        messages = [Message.model_validate(m) for m in response.json()]
        return sum([await self.on_message(msg) for msg in messages])

    async def sync_once(self, drain: bool = False) -> int:
        """
        One anti-entropy round: heartbeat, then pull from a random peer; with `drain`, keep
        pulling from it until it has nothing new for us. Returns the new messages. Never raises.
        """
        try:
            await self.redis.set(self._key, self.node_addr, ex=HEARTBEAT_TTL)
            self._peers = await self.get_active_peers()
        except Exception as e:
            logger.warning("Discovery unavailable, using %d known peers: %s", len(self._peers), e)
        if not self._peers:
            return 0

        peer, total = secrets.choice(self._peers), 0
        try:
            # Counting only *new* messages guarantees termination: a peer that keeps
            # resending duplicates (it can't fill our gap either) stops the loop.
            while new := await self.pull_from(peer):
                total += new
                if not drain:
                    break
        except Exception as e:
            logger.warning("Anti-entropy with %s failed: %s", peer, e)
        if total:
            logger.info("Anti-entropy: pulled %d messages from %s", total, peer)
        return total

    async def run(self) -> None:
        """Anti-entropy loop."""
        while True:
            await asyncio.sleep(GOSSIP_INTERVAL)
            await self.sync_once()

    async def publish(self, message: Message) -> None:
        """
        Fast path. Best effort: if Redis is down, anti-entropy delivers the message later.
        Bounded, because the user's write waits on it and must not hang during a partition.
        """
        try:
            await asyncio.wait_for(self.redis.publish(CHANNEL, message.model_dump_json()), PUBLISH_TIMEOUT)
        except (redis.RedisError, TimeoutError) as e:
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
