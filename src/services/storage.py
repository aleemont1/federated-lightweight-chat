"""
Defines storage management APIs, using SQLite, for
nodes' local persistence.
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterator, List, Set, Tuple

from src.core.message import Message, VectorClock
from src.core.vector_clock import merge

# Configure module logger
logger = logging.getLogger(__name__)

# Per room, per sender: the highest sequence number held with no gaps below it.
# A message's sequence number is its sender's own entry in its vector clock.
Summary = Dict[str, Dict[str, int]]


def _to_message(row: sqlite3.Row) -> Message:
    msg_dict = dict(row)
    msg_dict["vector_clock"] = json.loads(msg_dict["vector_clock"])
    return Message(**msg_dict)


class StorageService:
    """Handles local storage in nodes for persistence."""

    def __init__(self, db_name: str):
        """
        Initialize the StorageService.

        Args:
            db_name: The file path to the SQLite database.
        """
        self.db_name = db_name
        os.makedirs(os.path.dirname(db_name) or ".", exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """
        Context manager for database connections.
        Ensures proper closure.
        """
        # Connect to the DB. This will fail if the directory doesn't exist/isn't writable.
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initializes the database schema."""
        with self._get_conn() as conn:
            conn.execute(
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room_id)")
            # If a happened-before b, then vc(a) <= vc(b) with one entry strictly smaller,
            # so sum(vc(a)) < sum(vc(b)). Ordering by (clock_sum, created_at, sender_id, message_id)
            # is therefore a causal order, and deterministic, so identical on every replica.
            conn.execute(
                """
                CREATE VIEW IF NOT EXISTS causal_messages AS
                    SELECT *, (SELECT SUM(value) FROM json_each(vector_clock)) AS clock_sum FROM messages
                """
            )
            conn.commit()

    def add_message(self, message: Message) -> bool:
        """
        Inserts a message in the local storage.
        Returns False if it was already stored (replication is idempotent).
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
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
                    json.dumps(message.vector_clock),
                    message.created_at,
                ),
            )
            conn.commit()
            return cursor.rowcount == 1

    def get_room_messages(self, room_id: str, limit: int = 50, offset: int = 0) -> List[Message]:
        """
        Returns a page of a room's history in causal order.
        offset counts back from the newest message, so offset=0 is the latest page.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM causal_messages WHERE room_id = ?
                    ORDER BY clock_sum DESC, created_at DESC, sender_id DESC, message_id DESC
                    LIMIT ? OFFSET ?
                ) ORDER BY clock_sum, created_at, sender_id, message_id
                """,
                (room_id, limit, offset),
            ).fetchall()
        return [_to_message(row) for row in rows]

    def get_known_rooms(self) -> List[str]:
        """
        Retrieves a list of all room IDs that have at least one message.
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT DISTINCT room_id FROM messages ORDER BY room_id ASC").fetchall()
        return [row["room_id"] for row in rows]

    def room_clocks(self) -> Dict[str, VectorClock]:
        """Rebuilds each room's vector clock as the merge of its messages' clocks."""
        clocks: Dict[str, VectorClock] = {}
        with self._get_conn() as conn:
            for row in conn.execute("SELECT room_id, vector_clock FROM messages"):
                clocks[row["room_id"]] = merge(
                    clocks.get(row["room_id"], {}), json.loads(row["vector_clock"])
                )
        return clocks

    # ponytail: summary() and missing_messages() scan the whole table on every gossip round.
    # Fine for a chat of a few thousand messages; past that, persist a per-(room, sender) watermark.
    def summary(self) -> Summary:
        """What this replica holds: per room and sender, the highest gap-free sequence number."""
        seqs: Dict[Tuple[str, str], Set[int]] = {}
        with self._get_conn() as conn:
            for row in conn.execute("SELECT room_id, sender_id, vector_clock FROM messages"):
                seq = json.loads(row["vector_clock"]).get(row["sender_id"], 0)
                seqs.setdefault((row["room_id"], row["sender_id"]), set()).add(seq)

        summary: Summary = {}
        for (room_id, sender_id), held in seqs.items():
            top = 0
            while top + 1 in held:
                top += 1
            summary.setdefault(room_id, {})[sender_id] = top
        return summary

    def missing_messages(self, have: Summary, limit: int = 500) -> List[Message]:
        """
        Messages that a replica with summary `have` lacks, in causal order.
        Causal order puts each sender's lowest missing sequence first, so every
        batch closes the requester's gaps even when capped by `limit`.
        """
        missing: List[Message] = []
        with self._get_conn() as conn:
            for row in conn.execute(
                "SELECT * FROM causal_messages ORDER BY clock_sum, created_at, sender_id, message_id"
            ):
                msg = _to_message(row)
                if msg.vector_clock.get(msg.sender_id, 0) > have.get(msg.room_id, {}).get(msg.sender_id, 0):
                    missing.append(msg)
                    if len(missing) >= limit:
                        break
        return missing
