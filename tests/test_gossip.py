"""
Anti-entropy tests on real SQLite replicas.
The peer is reached through an httpx MockTransport that serves its storage,
so the whole pull protocol runs except the network.
"""

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
    state = NodeState(name, {})

    async def ingest(msg: Message) -> None:
        if storage.add_message(msg):
            state.update_clock(msg.room_id, msg.vector_clock)

    return storage, state, ingest


def write(storage, state, room, content):
    """Authors a message the way NodeService.send does."""
    msg = Message(
        room_id=room, sender_id=state.node_id, content=content, vector_clock=state.increment_clock(room)
    )
    storage.add_message(msg)
    return msg


def gossip_towards(storage, ingest, peer_storage):
    """A GossipService whose HTTP calls are answered by peer_storage."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/internal/sync"
        missing = peer_storage.missing_messages(json.loads(request.content))
        return httpx.Response(200, json=[m.model_dump(mode="json") for m in missing])

    service = GossipService(storage, "me", "http://me", on_message=ingest)
    service.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
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
    assert b_storage.summary() == {"bulk": {"alice": 1200}}
    assert rounds == 3  # 500 + 500 + 200


@pytest.mark.asyncio
async def test_pull_fills_gaps_and_merges_clock(tmp_path):
    """A replica holding seq 1 and 3 (2 lost) gets 2 back and ends with the merged clock."""
    a_storage, a_state, _ = make_node(tmp_path, "alice")
    b_storage, b_state, b_ingest = make_node(tmp_path, "bob")
    msgs = [write(a_storage, a_state, "general", c) for c in ("one", "two", "three")]
    await b_ingest(msgs[0])
    await b_ingest(msgs[2])
    assert b_storage.summary() == {"general": {"alice": 1}}

    await gossip_towards(b_storage, b_ingest, a_storage).pull_from("http://alice")

    assert [m.content for m in b_storage.get_room_messages("general")] == ["one", "two", "three"]
    assert b_state.get_clock("general")["alice"] == 3


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
        vector_clock=b_state.increment_clock("general"),
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

    await service.sync_once()

    service.redis.set.assert_awaited_once_with("discovery:node:bob", "http://bob", ex=10)
