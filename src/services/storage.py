"""
Defines storage management APIs, using SQLite, for
nodes' local persistence.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional

from src.core.message import Message, VectorClock


class StorageService:
    """Handles local storage in nodes for persistence."""

    def __init__(self, db_name: str):
        self.db_name = db_name
        self._init_db()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_name)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS room_peers (
                    room_id TEXT,
                    peer_url TEXT,
                    last_seen REAL,
                    PRIMARY KEY (room_id, peer_url)
                    )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    room_id TEXT,
                    sender_id TEXT,
                    content TEXT,
                    vector_clock TEXT,
                    created_at REAL
                    )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    room_id TEXT PRIMARY KEY,
                    vector_clock TEXT,
                    last_processed_time REAL
                    )
                """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_created_at
                    ON messages(created_at)
                """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_room_id
                    ON messages(room_id)
                """
            )

            conn.commit()

    def message_exists(self, message_id: str) -> bool:
        """
        Checks if a message exists.
        Used for idempotency during replication
        """

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM messages WHERE message_id = ?
                """,
                (message_id,),
            )
            return cursor.fetchone() is not None

    def add_message(self, message: Message) -> None:
        """
        Inserts a new message in the local storage.
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            vc_json = json.dumps(message.vector_clock)
            cursor.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, room_id, sender_id, content, vector_clock, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.room_id,
                    message.sender_id,
                    message.content,
                    vc_json,
                    message.created_at,
                ),
            )
            conn.commit()

    def save_snapshot(self, room_id: str, vector_clock: VectorClock) -> None:
        """
        Saves the current vector clock state for a room.
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            vc_json = json.dumps(vector_clock)
            current_time = time.time()
            cursor.execute(
                """
                INSERT OR REPLACE INTO snapshots
                    (room_id, vector_clock, last_processed_time)
                    VALUES (?, ?, ?)
                """,
                (room_id, vc_json, current_time),
            )
            conn.commit()

    def load_snapshot(self, room_id: str) -> tuple[Optional[VectorClock], float]:
        """
        Loads the latest snapshot for a room.
        Returns: (VectorClock, last_processed_time) or (None, 0.0)
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT vector_clock, last_processed_time 
                    FROM snapshots 
                    WHERE room_id = ?
                """,
                (room_id,),
            )

            row = cursor.fetchone()
            if row:
                return json.loads(row["vector_clock"]), row["last_processed_time"]
            return None, 0.0

    def get_all_messages(self, limit: int = 100, offset: int = 0) -> List[Message]:
        """Retreives all messages and converts them to Pydantic objects"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM messages
                    ORDER BY created_at ASC
                    LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            rows = cursor.fetchall()

            messages = []

            for row in rows:
                msg_dict = dict(row)
                msg_dict["vector_clock"] = json.loads(msg_dict["vector_clock"])
                messages.append(Message(**msg_dict))

        return messages

    def get_latest_clock(self, node_id: str) -> int:
        """
        Retreives the highest counter known for this node.
        Useful to restore the clock at 0 at each restart.
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT vector_clock FROM messages
                """
            )
            rows = cursor.fetchall()

            max_counter = 0
            for row in rows:
                vc = json.loads(row["vector_clock"])
                max_counter = max(vc.get(node_id, 0), max_counter)
        return max_counter

    def get_peers(self, room_id: str) -> List[str]:
        """Get all known peers in a room."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT peer_url FROM room_peers 
                    WHERE room_id = ?
                """,
                (room_id,),
            )

            peers = [row["peer_url"] for row in cursor.fetchall()]
            return peers

    def add_peer(self, room_id: str, peer_url: str) -> None:
        """Add a new peer inside the specified room"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO room_peers
                    (room_id, peer_url, last_seen)
                    VALUES (?, ?, ?)
                """,
                (room_id, peer_url, time.time()),
            )
            conn.close()
