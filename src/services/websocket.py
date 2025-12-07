"""
WebSocket Connection Manager with Redis Pub/Sub.
Handles active connections and broadcasting messages across all the nodes.
"""

import asyncio
import json
import logging
from typing import Dict, List

import redis.asyncio as redis
from fastapi import WebSocket

from src.config.settings import settings
from src.core.message import Message

logger = logging.getLogger(__name__)

redis_url = getattr(settings, "redis_url", "redis://localhost:6379")
redis_client = redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.pubsub_tasks: Dict[str, asyncio.Task[None]] = {}

    async def connect(self, websocket: WebSocket, room_id: str) -> None:
        """
        Accepts a new WebSocket connection.
        """
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []

            await self._subscribe_to_redis(room_id)

        self.active_connections[room_id].append(websocket)
        logger.info("WS Connected to %s. Total: %d", room_id, len(self.active_connections[room_id]))

    def disconnect(self, websocket: WebSocket, room_id: str) -> None:
        """
        Removes a WebSocket connection
        """
        if room_id in self.active_connections:
            if websocket in self.active_connections[room_id]:
                self.active_connections[room_id].remove(websocket)

            # Cleanup subscription if room is empty.
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]
                self._unsubscribe_from_redis(room_id)

    async def publish(self, message: Message, room_id: str) -> None:
        """
        Publishes a message to the Redis channel.
        This allows all nodes to receive it and broadcast to their clients
        """
        channel = f"chat:{room_id}"
        payload = message.model_dump_json()
        await redis_client.publish(channel, payload)

    async def broadcast_to_local(self, message: Message, room_id: str) -> None:
        """
        Sends a message to local clients in a specific room.
        Called when a message is received from Redis.
        """
        if room_id in self.active_connections:
            payload = message.model_dump(mode="json")

            for connection in self.active_connections[room_id][:]:
                try:
                    await connection.send_json(payload)
                except Exception as e:
                    logger.warning("Error sending to WS: %s", e)

    async def _subscribe_to_redis(self, room_id: str) -> None:
        """
        Starts a background task to listen to Redis messages for a room.
        """

        # Check if we're already subscribed (saves time)
        if room_id in self.pubsub_tasks:
            return

        async def listener() -> None:
            pubsub = redis_client.pubsub()
            channel = f"chat:{room_id}"

            await pubsub.subscribe(channel)
            logger.info("Subscribed to Redis channel: %s", channel)

            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        data_dict = json.loads(message.data["data"])

                        try:
                            msg_obj = Message(**data_dict)
                            await self.broadcast_to_local(msg_obj, room_id)
                        except Exception as e:
                            logger.error("Could not parse Redis message: %s", e)

            except asyncio.CancelledError:
                await pubsub.unsubscribe(channel)
                logger.info("Unsubscribed from %s", channel)
            except Exception as e:
                logger.error("Redis listener error for %s: %s", room_id, e)

        self.pubsub_tasks[room_id] = asyncio.create_task(listener())

    def _unsubscribe_from_redis(self, room_id: str) -> None:
        """
        Cancels the Redis listener task
        """
        if room_id in self.pubsub_tasks:
            self.pubsub_tasks[room_id].cancel()
            del self.pubsub_tasks[room_id]


# Singleton instance
manager = ConnectionManager()
