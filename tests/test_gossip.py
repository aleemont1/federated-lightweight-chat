"""
Anti-entropy tests on real SQLite replicas.
The peer is reached through an httpx MockTransport that serves its storage,
so the whole pull protocol runs except the network.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.message import Message
from src.core.node_state import NodeState
from src.services.gossip import GossipService
from src.services.storage import StorageService


def make_node(tmp_path, name):
    """A replica with its own DB and clock; ingest mimics NodeService.ingest."""
    storage = StorageService(str(tmp_path / f"{name}.db"))
    state = NodeState(name, {}, writer_id=storage.writer_id(name))

    async def ingest(msg: Message) -> bool:
        if not storage.add_message(msg):
            return False
        state.update_clock(msg.room_id, msg.vector_clock)
        return True

    return storage, state, ingest


def write(storage, state, room, content):
    """Authors a message the way NodeService.send does."""
    msg = Message(
        room_id=room,
        sender_id=state.node_id,
        writer_id=state.writer_id,
        content=content,
        vector_clock=state.next_clock(room),
    )
    storage.add_message(msg)
    state.update_clock(room, msg.vector_clock)
    return msg


def gossip_towards(storage, ingest, peer_storage):
    """A GossipService whose HTTP calls are answered by peer_storage."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/internal/sync"
        missing = peer_storage.missing_messages(json.loads(request.content))
        return httpx.Response(200, json=[m.model_dump(mode="json") for m in missing])

    service = GossipService(storage, "me", "http://me", on_message=ingest)
    service.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service.redis = AsyncMock()  # discovery: the peer list is set by each test
    return service


@pytest.mark.asyncio
async def test_pull_converges_beyond_old_100_message_cap(tmp_path):
    """Regression: the old push gossip never replicated more than 100 messages."""
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, _, b_ingest = make_node(tmp_path, "bob")
    for i in range(1200):
        write(a_storage, a_state, "bulk", f"m{i}")

    gossip = gossip_towards(b_storage, b_ingest, a_storage)
    rounds = 0
    while await gossip.pull_from("http://alice"):
        rounds += 1

    assert len(b_storage.get_room_messages("bulk", limit=500, offset=0)) == 500
    assert b_storage.summary() == {"bulk": {a_state.writer_id: 1200}}
    assert rounds == 3  # 500 + 500 + 200


@pytest.mark.asyncio
async def test_pull_fills_gaps_and_merges_clock(tmp_path):
    """A replica holding seq 1 and 3 (2 lost) gets 2 back and ends with the merged clock."""
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, b_state, b_ingest = make_node(tmp_path, "bob")
    msgs = [write(a_storage, a_state, "general", c) for c in ("one", "two", "three")]
    await b_ingest(msgs[0])
    await b_ingest(msgs[2])
    assert b_storage.summary() == {"general": {a_state.writer_id: 1}}

    await gossip_towards(b_storage, b_ingest, a_storage).pull_from("http://alice")

    assert [m.content for m in b_storage.get_room_messages("general")] == ["one", "two", "three"]
    assert b_state.get_clock("general")[a_state.writer_id] == 3


@pytest.mark.asyncio
async def test_bob_reply_is_causally_after_what_he_pulled(tmp_path):
    """Pulled messages update the clock, so a reply is ordered after them on every replica."""
    a_storage, a_state, a_ingest = make_node(tmp_path, "alice")
    b_storage, b_state, b_ingest = make_node(tmp_path, "bob")
    question = write(a_storage, a_state, "general", "question")

    await gossip_towards(b_storage, b_ingest, a_storage).pull_from("http://alice")
    answer = Message(
        room_id="general",
        sender_id="bob",
        content="answer",
        vector_clock=b_state.next_clock("general"),
        created_at=question.created_at - 100,  # Bob's wall clock lies: the answer looks older
    )
    b_storage.add_message(answer)
    await a_ingest(answer)

    for storage in (a_storage, b_storage):
        assert [m.content for m in storage.get_room_messages("general")] == ["question", "answer"]


@pytest.mark.asyncio
async def test_sync_once_survives_unreachable_peer(tmp_path):
    """A failing round is logged, never raised: the loop must keep running."""
    storage, _, ingest = make_node(tmp_path, "bob")
    service = GossipService(storage, "bob", "http://bob", on_message=ingest)
    service.redis = AsyncMock()
    service.get_active_peers = AsyncMock(return_value=["http://alice"])  # type: ignore[method-assign]

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    service.http = httpx.AsyncClient(transport=httpx.MockTransport(down))

    assert await service.sync_once() == 0

    service.redis.set.assert_awaited_once_with("discovery:node:bob", "http://bob", ex=10)


@pytest.mark.asyncio
async def test_drain_catches_up_in_one_call(tmp_path):
    """At startup a node drains one peer, not just one 500-message batch (or it could reuse seqs)."""
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, _, b_ingest = make_node(tmp_path, "bob")
    for i in range(1200):
        write(a_storage, a_state, "bulk", f"m{i}")
    gossip = gossip_towards(b_storage, b_ingest, a_storage)
    gossip.get_active_peers = AsyncMock(return_value=["http://alice"])  # type: ignore[method-assign]

    assert await gossip.sync_once(drain=True) == 1200
    assert b_storage.summary() == {"bulk": {a_state.writer_id: 1200}}


@pytest.mark.asyncio
async def test_drain_stops_when_peer_only_has_duplicates(tmp_path):
    """Both replicas miss alice's seq 2: the peer keeps resending seq 3, which must not loop forever."""
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, _, b_ingest = make_node(tmp_path, "bob")
    msgs = [write(a_storage, a_state, "general", c) for c in ("one", "two", "three")]
    peer_storage = StorageService(str(tmp_path / "peer.db"))
    for m in (msgs[0], msgs[2]):
        peer_storage.add_message(m)
        await b_ingest(m)
    gossip = gossip_towards(b_storage, b_ingest, peer_storage)
    gossip.get_active_peers = AsyncMock(return_value=["http://peer"])  # type: ignore[method-assign]

    assert await gossip.sync_once(drain=True) == 0


@pytest.mark.asyncio
async def test_anti_entropy_survives_redis_outage(tmp_path):
    """With discovery down, the last known peers are still used: Redis is not needed to repair."""
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, _, b_ingest = make_node(tmp_path, "bob")
    gossip = gossip_towards(b_storage, b_ingest, a_storage)
    gossip.get_active_peers = AsyncMock(return_value=["http://alice"])  # type: ignore[method-assign]
    await gossip.sync_once()  # learns the peer while Redis is up

    gossip.redis.set.side_effect = ConnectionError("Redis down")
    write(a_storage, a_state, "general", "sent while Redis was down")

    assert await gossip.sync_once() == 1
    assert b_storage.get_room_messages("general")[0].content == "sent while Redis was down"


@pytest.mark.asyncio
async def test_publish_gives_up_on_a_hanging_redis(tmp_path):
    """Regression: during a partition publish hung forever, and with it the user's write."""
    storage, state, ingest = make_node(tmp_path, "carol")
    service = GossipService(storage, "carol", "http://carol", on_message=ingest)
    service.redis = AsyncMock()

    async def hang(*_):
        await asyncio.Event().wait()

    service.redis.publish.side_effect = hang

    await asyncio.wait_for(service.publish(write(storage, state, "general", "x")), timeout=3)


@pytest.mark.asyncio
async def test_node_that_lost_its_data_is_still_heard(tmp_path):
    """
    Regression: alice's DB is lost and no peer is online when she restarts, so she counts from 1
    again. With the username as writer, her new message reused seq 1 and bob never pulled it.
    """
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, _, b_ingest = make_node(tmp_path, "bob")
    for c in ("one", "two"):
        write(a_storage, a_state, "general", c)
    await gossip_towards(b_storage, b_ingest, a_storage).pull_from("http://alice")

    reborn_storage = StorageService(str(tmp_path / "alice-reborn.db"))
    reborn_state = NodeState("alice", {}, writer_id=reborn_storage.writer_id("alice"))
    write(reborn_storage, reborn_state, "general", "after the loss")
    await gossip_towards(b_storage, b_ingest, reborn_storage).pull_from("http://alice")

    # It never saw "one" and "two", so it's concurrent with them: any position is causal.
    assert sorted(m.content for m in b_storage.get_room_messages("general")) == [
        "after the loss",
        "one",
        "two",
    ]
