"""
End-to-end tests of the system's guarantees against the real Docker setup:
real containers, real Redis, real network cuts. Each test is one invariant.

Run: docker compose up -d --build && FLC_E2E=1 pytest tests/test_e2e.py -v
(or ./scripts/e2e.sh). Skipped otherwise, so the unit gates stay fast.

Requests go to 127.0.0.1 with an explicit Host header, so `*.localhost`
doesn't need to resolve on the machine running the tests.
"""

import json
import os
import re
import secrets
import time
from typing import Any, Callable, Dict, Iterator, List

import docker
import httpx
import pytest
from docker.errors import NotFound

pytestmark = pytest.mark.skipif(not os.getenv("FLC_E2E"), reason="needs the stack: set FLC_E2E=1")

GATEWAY = "http://127.0.0.1"
PASSWORD = "e2e-password"
RUN = secrets.token_hex(3)  # fresh usernames each run: accounts persist in the orchestrator


def eventually(check: Callable[[], Any], timeout: float = 30, what: str = "condition") -> Any:
    """Polls `check` until it returns something truthy. Every wait in this file goes through here."""
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            if last := check():
                return last
        except httpx.HTTPError as e:
            last = e
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout}s waiting for {what} (last: {last!r})")


def orchestrator() -> httpx.Client:
    return httpx.Client(base_url=GATEWAY, headers={"Host": "localhost"}, timeout=10)


class Node:
    """A logged-in browser session on one user's node."""

    def __init__(self, user: str, token: str):
        self.user, self.token = user, token
        self.http = httpx.Client(
            base_url=GATEWAY,
            headers={"Host": f"{user}.localhost", "Cookie": f"flc_session={token}"},
            timeout=10,
        )

    def send(self, room: str, content: str) -> Dict[str, Any]:
        response = self.http.post("/api/messages", json={"room_id": room, "content": content})
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    def history(self, room: str) -> List[Dict[str, Any]]:
        """The whole room in display order (pages come newest first, each oldest to newest)."""
        pages = []
        while page := self.http.get(
            "/api/messages", params={"room_id": room, "limit": 500, "offset": 500 * len(pages)}
        ).json():
            pages.append(page)
        return [m for page in reversed(pages) for m in page]

    def ids(self, room: str) -> List[str]:
        return [m["message_id"] for m in self.history(room)]

    @property
    def container(self) -> Any:
        return docker.from_env().containers.get(f"flc-node-{self.user}")


def login(user: str) -> Node:
    """The browser flow: password -> loading page -> node routable -> login link -> cookie."""
    with orchestrator() as orch:
        page = orch.post("/login", data={"username": user, "password": PASSWORD})
        assert page.status_code == 200, page.text
        token = re.search(rf"{user}\.localhost/api/auth\?token=([\w-]+)", page.text)
        assert token, "the loading page carries the node's login link"
        eventually(lambda: orch.get(f"/status/{user}").json()["ready"], 60, f"{user}'s node")
    auth = httpx.get(f"{GATEWAY}/api/auth", params={"token": token[1]}, headers={"Host": f"{user}.localhost"})
    assert auth.status_code == 303 and "flc_session" in auth.headers["set-cookie"]
    return Node(user, token[1])


def stop_node(node: Node) -> None:
    with orchestrator() as orch:
        orch.post("/stop", data={"username": node.user, "password": PASSWORD})


@pytest.fixture(scope="module")
def cluster() -> Iterator[List[Node]]:
    """Three fresh users with running nodes; removed with their data afterwards."""
    with orchestrator() as orch:
        eventually(lambda: orch.get("/").status_code == 200, what="the orchestrator (is the stack up?)")
    nodes = [login(f"e2e-{RUN}-{name}") for name in ("a", "b", "c")]
    yield nodes
    client = docker.from_env()
    for node in nodes:
        stop_node(node)
        try:
            client.volumes.get(f"flc_data_{node.user}").remove()
        except NotFound:
            pass


def room() -> str:
    return f"r-{secrets.token_hex(4)}"


def test_replicas_converge_beyond_100_messages(cluster: List[Node]) -> None:
    """Every replica ends with the same messages (the old gossip stopped at the 100 oldest)."""
    a, b, c = cluster
    r = room()
    for i in range(150):
        a.send(r, f"m{i}")

    expected = a.ids(r)
    assert len(expected) == 150
    for node in (b, c):
        eventually(lambda node=node: node.ids(r) == expected, what=f"{node.user} to converge")


def test_all_replicas_show_the_same_causal_order(cluster: List[Node]) -> None:
    """Concurrent writers, one total order everywhere, and every reply after what it answers."""
    a, b, c = cluster
    r = room()
    question = a.send(r, "question")
    eventually(lambda: question["message_id"] in b.ids(r), what="b to see the question")
    answer = b.send(r, "answer")  # b saw the question: causally after it
    for i in range(10):  # concurrent traffic from all sides
        for node in (a, b, c):
            node.send(r, f"{node.user[-1]}{i}")

    order = eventually(lambda: len(ids := a.ids(r)) == 32 and ids, what="all 32 messages on a")
    for node in (b, c):
        eventually(lambda node=node: node.ids(r) == order, what=f"{node.user} to show a's order")
    assert order.index(question["message_id"]) < order.index(answer["message_id"])


def test_restarted_node_catches_up_before_serving(cluster: List[Node]) -> None:
    """A node offline while others write has everything as soon as it's back."""
    a, _, c = cluster
    r = room()
    stop_node(c)  # container removed, data volume kept
    sent = [a.send(r, f"while c was down {i}")["message_id"] for i in range(20)]

    c2 = login(c.user)
    cluster[2] = c2
    assert c2.ids(r) == sent  # no `eventually`: the startup catch-up already ran


def test_partition_heals(cluster: List[Node]) -> None:
    """Both sides of a partition keep accepting writes (AP) and agree once it heals."""
    a, b, c = cluster
    r = room()
    net = docker.from_env().networks.get("flc-net")
    net.disconnect(c.container)
    try:
        a.send(r, "majority side")
        # c is cut off from the gateway too: write from inside its container.
        body = json.dumps({"room_id": r, "content": "minority side"})
        script = (
            "import urllib.request as u; u.urlopen(u.Request('http://localhost:8000/api/messages', "
            f"data={body!r}.encode(), headers={{'Content-Type': 'application/json', "
            f"'Cookie': 'flc_session={c.token}'}}))"
        )
        assert c.container.exec_run(["python", "-c", script]).exit_code == 0
        time.sleep(3)
        assert [m["content"] for m in a.history(r)] == ["majority side"]
    finally:
        net.connect(c.container)

    for node in (a, b, c):
        eventually(lambda node=node: len(node.ids(r)) == 2, what=f"{node.user} to merge both sides")
    assert a.ids(r) == b.ids(r) == c.ids(r)


def test_messages_still_replicate_while_redis_is_down(cluster: List[Node]) -> None:
    """Redis only speeds things up: with it stopped, anti-entropy with known peers still delivers."""
    a, b, _ = cluster
    r = room()
    redis = docker.from_env().containers.get("flc-redis")
    redis.stop()
    try:
        sent = a.send(r, "Redis is down")
        eventually(lambda: b.ids(r) == [sent["message_id"]], what="b to get it without Redis")
    finally:
        redis.start()


def test_nodes_refuse_everything_without_the_right_session(cluster: List[Node]) -> None:
    """The audit's attacks: anonymous access, another user's cookie, public sync, stop by name."""
    a, b, _ = cluster
    anon = httpx.Client(base_url=GATEWAY, headers={"Host": f"{a.user}.localhost"})
    assert anon.get("/api/rooms").status_code == 401
    assert anon.post("/api/messages", json={"room_id": "general", "content": "x"}).status_code == 401
    assert anon.post("/api/internal/sync", json={}).status_code == 404  # not routed by the gateway

    borrowed = anon.get("/api/rooms", headers={"Cookie": f"flc_session={b.token}"})
    assert borrowed.status_code == 401

    with orchestrator() as orch:
        assert orch.post("/stop", data={"username": a.user, "password": "wrong-password"}).status_code == 401
        assert orch.post("/login", data={"username": a.user, "password": "wrong-password"}).status_code == 401
