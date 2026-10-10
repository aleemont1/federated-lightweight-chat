"""
Unit tests for the NodeService.
Real SQLite storage; GossipService (Redis + network) and the WebSocket manager are mocked.
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from src.core.message import Message
from src.services.node import NodeService
from src.services.storage import StorageService


@pytest.fixture
def db_path(tmp_path):
    """Path of the node DB, pre-populated by some tests."""
    return str(tmp_path / "node.db")


@pytest.fixture
def mocks(db_path):
    """Patches the node's collaborators; yields (gossip_cls, manager)."""
    with (
        patch("src.services.node.GossipService") as gossip_cls,
        patch("src.services.node.manager") as manager,
        patch("src.services.node.settings.db_name", db_path),
    ):
        gossip = gossip_cls.return_value
        gossip.sync_once = AsyncMock()
        gossip.run = AsyncMock()
        gossip.listen = AsyncMock()
        gossip.publish = AsyncMock()
        gossip.close = AsyncMock()
        manager.broadcast = AsyncMock()
        yield gossip_cls, manager


@pytest.mark.asyncio
async def test_initialize_catches_up_before_starting_loops(mocks):
    """Initial anti-entropy round runs before the background loops."""
    gossip_cls, _ = mocks
    node = NodeService()

    await node.initialize("alice")

    gossip = gossip_cls.return_value
    gossip.sync_once.assert_awaited_once_with(drain=True)
    gossip.run.assert_called_once()
    gossip.listen.assert_called_once()
    assert gossip_cls.call_args.kwargs["on_message"] == node.ingest
    await node.shutdown()


@pytest.mark.asyncio
async def test_initialize_idempotency_and_conflict(mocks):
    """Same user is a no-op, a different user is refused."""
    node = NodeService()
    await node.initialize("user_a")
    await node.initialize("user_a")
    with pytest.raises(ValueError, match="Node is already initialized"):
        await node.initialize("user_b")
    await node.shutdown()


@pytest.mark.asyncio
async def test_clocks_are_rebuilt_from_history(mocks, db_path):
    """After a restart the next message continues this replica's sequence."""
    storage = StorageService(db_path)
    me = storage.writer_id("alice")
    storage.add_message(
        Message(
            room_id="lobby", sender_id="alice", writer_id=me, content="old", vector_clock={me: 10, "bob": 5}
        )
    )
    node = NodeService()
    await node.initialize("alice")

    sent = await node.send("lobby", "new")

    assert (sent.writer_id, sent.vector_clock) == (me, {me: 11, "bob": 5})
    await node.shutdown()


@pytest.mark.asyncio
async def test_send_stores_shows_and_publishes(mocks):
    """Local messages reach local sockets once and are published."""
    gossip_cls, manager = mocks
    node = NodeService()
    await node.initialize("alice")

    sent = await node.send("general", "hi")

    assert node.storage.get_room_messages("general") == [sent]
    manager.broadcast.assert_awaited_once_with(sent)
    gossip_cls.return_value.publish.assert_awaited_once_with(sent)
    await node.shutdown()


@pytest.mark.asyncio
async def test_ingest_is_idempotent(mocks):
    """The same message via pub/sub and anti-entropy is stored and shown once, and merges the clock."""
    _, manager = mocks
    node = NodeService()
    await node.initialize("alice")
    remote = Message(room_id="general", sender_id="bob", content="yo", vector_clock={"bob": 3})

    assert await node.ingest(remote) is True
    assert await node.ingest(remote) is False

    manager.broadcast.assert_awaited_once_with(remote)
    assert node.state.get_clock("general") == {node.state.writer_id: 0, "bob": 3}
    await node.shutdown()


@pytest.mark.asyncio
async def test_shutdown_lifecycle(mocks):
    """Shutdown cancels the loops and leaves discovery."""
    gossip_cls, _ = mocks
    node = NodeService()
    await node.initialize("user1")
    tasks = list(node._tasks)

    await node.shutdown()

    assert node.state is None
    assert all(t.done() for t in tasks)
    gossip_cls.return_value.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_write_burns_no_sequence_number(mocks):
    """Regression: the clock moved before the insert, so a disk error left a permanent gap."""
    node = NodeService()
    await node.initialize("alice")
    me = node.state.writer_id
    with patch.object(node.storage, "add_message", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError):
            await node.send("general", "lost")

    sent = await node.send("general", "kept")

    assert sent.vector_clock[me] == 1
    await node.shutdown()
