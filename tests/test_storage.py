"""Unit tests for storage service and CRUD ops on DB."""

import json
import sqlite3

import pytest

from src.core.message import Message
from src.services.storage import StorageService


@pytest.fixture
def storage_service(tmp_path):
    """
    Creates a temporary file-based DB.
    tmp_path is a built-in pytest fixture that provides a temporary directory
    """
    return StorageService(str(tmp_path / "test_node.db"))


def msg(seq, sender="alice", room="general", **clock):
    """A message whose sender entry in the clock is `seq`."""
    return Message(
        room_id=room, sender_id=sender, content=f"{sender}{seq}", vector_clock={sender: seq, **clock}
    )


def test_add_and_retrieve_message(storage_service):
    """Tests message addition, idempotency and retrieval."""
    message = Message(
        room_id="test_room",
        sender_id="test_sender",
        content="Unit testing",
        vector_clock={"node_a": 1, "node_b": 2},
    )

    assert storage_service.add_message(message) is True
    assert storage_service.add_message(message) is False  # duplicates are ignored

    (loaded,) = storage_service.get_room_messages("test_room")
    assert loaded == message


def test_add_messages_batch(storage_service):
    """Batch insertion stores new messages in a single transaction and skips duplicates."""
    m1 = msg(1, room="batch_room")
    m2 = msg(2, room="batch_room")
    assert storage_service.add_messages([]) == []
    stored = storage_service.add_messages([m1, m2])
    assert len(stored) == 2
    # Second insertion with overlapping message skips the duplicate
    m3 = msg(3, room="batch_room")
    stored2 = storage_service.add_messages([m2, m3])
    assert len(stored2) == 1
    assert stored2[0].message_id == m3.message_id
    assert len(storage_service.get_room_messages("batch_room")) == 3


def test_creates_missing_db_directory(tmp_path):
    """The DB directory (e.g. a fresh /data volume) is created on demand."""
    StorageService(str(tmp_path / "nested" / "dir" / "node.db"))
    assert (tmp_path / "nested" / "dir" / "node.db").exists()


def test_room_messages_are_newest_page_in_causal_order(storage_service):
    """offset counts back from the newest message; each page is returned oldest first."""
    for seq in range(1, 6):
        storage_service.add_message(msg(seq))
    storage_service.add_message(msg(1, room="other"))

    assert [m.content for m in storage_service.get_room_messages("general", limit=2)] == ["alice4", "alice5"]
    assert [m.content for m in storage_service.get_room_messages("general", limit=2, offset=2)] == [
        "alice2",
        "alice3",
    ]
    assert len(storage_service.get_room_messages("general", limit=10)) == 5
    assert storage_service.get_room_messages("empty_room") == []


def test_causal_order_ignores_wall_clock(storage_service):
    """A reply that saw the original sorts after it even if its timestamp is older."""
    original = msg(1)
    original.created_at = 1000.0
    reply = Message(
        room_id="general",
        sender_id="bob",
        content="reply",
        vector_clock={"alice": 1, "bob": 1},
        created_at=1.0,
    )
    storage_service.add_message(reply)
    storage_service.add_message(original)

    assert [m.content for m in storage_service.get_room_messages("general")] == ["alice1", "reply"]


def test_known_rooms(storage_service):
    """Rooms are listed once each, sorted."""
    for room in ("b", "a", "b"):
        storage_service.add_message(Message(room_id=room, sender_id="x", content="hi"))
    assert storage_service.get_known_rooms() == ["a", "b"]


def test_room_clocks_merge_all_messages(storage_service):
    """Clocks are rebuilt from history, so restarts need no snapshot."""
    storage_service.add_message(msg(3, bob=1))
    storage_service.add_message(msg(2, sender="bob", alice=4))
    storage_service.add_message(msg(7, room="other"))

    assert storage_service.room_clocks() == {"general": {"alice": 4, "bob": 2}, "other": {"alice": 7}}


def test_summary_lists_held_ranges_around_gaps(storage_service):
    """The gap shows as a hole between ranges: the missing message is asked for, the rest isn't."""
    for seq in (1, 2, 4, 5, 7):
        storage_service.add_message(msg(seq))
    storage_service.add_message(msg(1, sender="bob"))

    assert storage_service.summary() == {"general": {"alice": [(1, 2), (4, 5), (7, 7)], "bob": [(1, 1)]}}
    assert [
        m.content for m in storage_service.missing_messages({"general": {"alice": [(1, 1), (5, 7)]}})
    ] == [
        "bob1",
        "alice2",
        "alice4",
    ]


def test_missing_messages(storage_service):
    """Returns exactly what the requester lacks, in causal order, capped by limit."""
    for seq in range(1, 4):
        storage_service.add_message(msg(seq))
    storage_service.add_message(msg(1, sender="bob", room="other"))

    missing = storage_service.missing_messages({"general": {"alice": [(1, 1)]}})
    assert [m.content for m in missing] == ["bob1", "alice2", "alice3"]

    assert [m.content for m in storage_service.missing_messages({}, limit=2)] == ["alice1", "bob1"]
    assert (
        storage_service.missing_messages({"general": {"alice": [(1, 3)]}, "other": {"bob": [(1, 1)]}}) == []
    )


def test_legacy_database_is_migrated(tmp_path):
    """Volumes written before writer ids: the sender was the writer and its clock entry the seq."""
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE messages (message_id TEXT PRIMARY KEY, room_id TEXT, sender_id TEXT,"
            " content TEXT, vector_clock TEXT, created_at REAL)"
        )
        for seq in (1, 2):
            conn.execute(
                "INSERT INTO messages VALUES (?, 'general', 'alice', 'hi', ?, 1.0)",
                (f"m{seq}", json.dumps({"alice": seq, "bob": 1})),
            )

    storage = StorageService(path)

    assert storage.summary() == {"general": {"alice": [(1, 2)]}}
    assert [m.writer_id for m in storage.get_room_messages("general")] == ["alice", "alice"]


def test_writer_id_belongs_to_the_database(tmp_path):
    """Same DB, same writer across restarts; a lost DB comes back as a new writer."""
    first = StorageService(str(tmp_path / "a.db")).writer_id("alice")
    assert StorageService(str(tmp_path / "a.db")).writer_id("alice") == first
    reborn = StorageService(str(tmp_path / "b.db")).writer_id("alice")
    assert reborn != first and reborn.startswith("alice/")


def test_summary_and_missing_are_per_writer(storage_service):
    """Two incarnations of alice number independently: neither hides the other."""
    for seq, writer in ((1, "alice/old"), (2, "alice/old"), (1, "alice/new")):
        storage_service.add_message(
            Message(
                room_id="general",
                sender_id="alice",
                writer_id=writer,
                content=f"{writer}{seq}",
                vector_clock={writer: seq},
            )
        )

    assert storage_service.summary() == {"general": {"alice/new": [(1, 1)], "alice/old": [(1, 2)]}}
    missing = storage_service.missing_messages({"general": {"alice/old": [(1, 2)]}})
    assert [m.content for m in missing] == ["alice/new1"]
