"""
Defines storage management APIs, using SQLite, for
nodes' local persistence.
"""

import json
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterator, List, Tuple

from src.core.message import Message, VectorClock
from src.core.vector_clock import merge

# Configure module logger
logger = logging.getLogger(__name__)

# Per room, per writer: the ranges of sequence numbers held, e.g. [(1, 4), (6, 699)] with seq 5
# missing. Ranges, not just the gap-free prefix, so a peer never resends what sits above a gap.
# A message's sequence number is its writer's own entry in its vector clock.
Summary = Dict[str, Dict[str, List[Tuple[int, int]]]]


def _to_message(row: sqlite3.Row) -> Message:
    msg_dict = {k: row[k] for k in Message.model_fields}
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
        """Creates the schema, or migrates a database from before writer ids."""
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    room_id TEXT,
                    sender_id TEXT,
                    content TEXT,
                    vector_clock TEXT,
                    created_at REAL,
                    writer_id TEXT,
                    seq INTEGER
                    )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
            if "writer_id" not in columns:
                # Before writer ids the sender was the writer, and its clock entry the seq.
                conn.execute("ALTER TABLE messages ADD COLUMN writer_id TEXT")
                conn.execute("ALTER TABLE messages ADD COLUMN seq INTEGER")
                conn.execute(
                    """UPDATE messages SET writer_id = sender_id,
                        seq = json_extract(vector_clock, '$."' || sender_id || '"')"""
                )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_writer_seq ON messages(room_id, writer_id, seq)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
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

    def writer_id(self, user_id: str) -> str:
        """
        This replica's identity in vector clocks, created with the database. A node whose data
        is lost comes back as a new writer, so it can never reuse a sequence number.
        """
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('writer_id', ?)",
                (f"{user_id}/{secrets.token_hex(4)}",),
            )
            conn.commit()
            row = conn.execute("SELECT value FROM meta WHERE key = 'writer_id'").fetchone()
        return str(row["value"])

    def add_message(self, message: Message) -> bool:
        """
        Inserts a message in the local storage.
        Returns False if it was already stored (replication is idempotent).
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, room_id, sender_id, content, vector_clock, created_at, writer_id, seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.room_id,
                    message.sender_id,
                    message.content,
                    json.dumps(message.vector_clock),
                    message.created_at,
                    message.writer_id,
                    message.vector_clock.get(message.writer_id, 0),
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

    # ponytail: both run on the (room, writer, seq) index but still visit every message each
    # round. Fine for tens of thousands of messages; past that, keep a per-(room, writer) watermark.
    def summary(self) -> Summary:
        """What this replica holds: per room and writer, the ranges of sequence numbers."""
        summary: Summary = {}
        with self._get_conn() as conn:
            # Gaps and islands: within a run of consecutive seqs, seq minus its rank is constant.
            # DISTINCT absorbs seqs reused before writer ids existed.
            for row in conn.execute(
                """
                SELECT room_id, writer_id, MIN(seq) AS lo, MAX(seq) AS hi FROM (
                    SELECT room_id, writer_id, seq,
                        seq - ROW_NUMBER() OVER (PARTITION BY room_id, writer_id ORDER BY seq) AS island
                    FROM (SELECT DISTINCT room_id, writer_id, seq FROM messages WHERE seq IS NOT NULL)
                ) GROUP BY room_id, writer_id, island ORDER BY room_id, writer_id, lo
                """
            ):
                summary.setdefault(row["room_id"], {}).setdefault(row["writer_id"], []).append(
                    (row["lo"], row["hi"])
                )
        return summary

    def missing_messages(self, have: Summary, limit: int = 500) -> List[Message]:
        """
        Messages that a replica with summary `have` lacks, in causal order. Only what it
        lacks: messages above one of its gaps are not resent, so an unfillable gap can't
        fill every batch with duplicates and starve the rest.
        """
        spans = [
            (room_id, writer_id, lo, hi)
            for room_id, writers in have.items()
            for writer_id, ranges in writers.items()
            for lo, hi in ranges
        ]
        with self._get_conn() as conn:
            # Indexed, so each message checks only the nearest range below its seq: O(log n), not O(ranges).
            conn.execute(
                """CREATE TEMP TABLE have (room_id TEXT, writer_id TEXT, lo INTEGER, hi INTEGER,
                    PRIMARY KEY (room_id, writer_id, lo))"""
            )
            conn.executemany("INSERT OR IGNORE INTO have VALUES (?, ?, ?, ?)", spans)
            rows = conn.execute(
                """
                SELECT m.* FROM causal_messages AS m
                WHERE m.seq > COALESCE((
                    SELECT h.hi FROM have AS h
                    WHERE h.room_id = m.room_id AND h.writer_id = m.writer_id AND h.lo <= m.seq
                    ORDER BY h.lo DESC LIMIT 1
                ), 0)
                ORDER BY m.clock_sum, m.created_at, m.sender_id, m.message_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_to_message(row) for row in rows]
