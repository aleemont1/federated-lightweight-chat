"""
Unit tests for the Orchestrator Service (Docker client mocked).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from docker.errors import NotFound

from src.services.orchestrator import MaxUsersReachedError, OrchestratorService, normalize_username


@pytest.fixture
def docker_client():
    """Mocks docker.from_env(); by default no container exists and none is running."""
    with patch("src.services.orchestrator.docker.from_env") as from_env:
        client = from_env.return_value
        client.containers.get.side_effect = NotFound("missing")
        client.containers.list.return_value = []
        yield client


@pytest.fixture
def orchestrator(docker_client):
    """An orchestrator with a low user limit for testing."""
    return OrchestratorService(max_users=2)


def node(name, status="running", env=("NODE_TOKEN=tok",)):
    """A mocked node container, by default running and carrying a token."""
    container = MagicMock(status=status, attrs={"Config": {"Env": [f"NODE_ID={name}", *env]}})
    container.name = f"flc-node-{name}"  # MagicMock(name=...) would only set the repr
    return container


@pytest.mark.parametrize("raw,expected", [("alice", "alice"), (" Bob-2 ", "bob-2"), ("x", "x")])
def test_normalize_accepts_dns_labels(raw, expected):
    """Usernames become subdomains, so they must be DNS labels; case is folded."""
    assert normalize_username(raw) == expected


@pytest.mark.parametrize("raw", ["", "!!!", "alice smith", "alice_smith", "-alice", "alice-", "a" * 33])
def test_normalize_rejects_invalid(raw):
    """Previously these were mangled into broken names (e.g. 'flc-node-' for '!!!')."""
    with pytest.raises(ValueError):
        normalize_username(raw)


def test_spawn_configures_routing_and_peer_address(orchestrator, docker_client):
    """The node gets its subdomain route and the address peers use to reach it."""
    orchestrator.spawn_user_node("alice")

    kwargs = docker_client.containers.run.call_args.kwargs
    assert kwargs["name"] == "flc-node-alice"
    assert kwargs["environment"]["NODE_ID"] == "alice"
    assert kwargs["environment"]["ADVERTISED_ADDR"] == "http://flc-node-alice:8000"
    assert kwargs["labels"]["app"] == "flc-node"
    assert kwargs["environment"]["NODE_TOKEN"]  # a fresh secret per node
    rule = kwargs["labels"]["traefik.http.routers.flc-alice.rule"]
    assert rule == "Host(`alice.localhost`) && !PathPrefix(`/api/internal`)"  # sync stays internal


def test_each_node_gets_its_own_token(orchestrator, docker_client):
    """Tokens are random per node, so one user's link can't open another's node."""
    orchestrator.spawn_user_node("alice")
    orchestrator.spawn_user_node("bob")
    first, second = (
        c.kwargs["environment"]["NODE_TOKEN"] for c in docker_client.containers.run.call_args_list
    )
    assert first != second and len(first) >= 32


def test_node_token_is_read_back_from_the_container(orchestrator, docker_client):
    """Login links and logout checks use the token the node was started with."""
    assert orchestrator.node_token("ghost") is None
    docker_client.containers.get.side_effect = None
    docker_client.containers.get.return_value = node("alice", env=("NODE_TOKEN=a=b",))
    assert orchestrator.node_token("alice") == "a=b"


def test_container_without_token_is_recreated(orchestrator, docker_client):
    """Nodes created before tokens existed would accept nobody: replace them."""
    legacy = node("alice", env=())
    docker_client.containers.get.side_effect = None
    docker_client.containers.get.return_value = legacy

    orchestrator.spawn_user_node("alice")

    legacy.remove.assert_called_once_with(force=True)
    docker_client.containers.run.assert_called_once()


def test_active_users_uses_the_label_nodes_carry(orchestrator, docker_client):
    """Regression: the filter used 'flc_node' while nodes are labelled 'flc-node', so 0 were listed."""
    docker_client.containers.list.return_value = [node("bob"), node("alice")]

    assert orchestrator.get_active_users() == ["alice", "bob"]
    docker_client.containers.list.assert_called_once_with(filters={"label": "app=flc-node"})


def test_spawn_is_idempotent(orchestrator, docker_client):
    """A running node is left alone."""
    docker_client.containers.get.side_effect = None
    docker_client.containers.get.return_value = node("alice")

    orchestrator.spawn_user_node("alice")

    docker_client.containers.run.assert_not_called()


def test_spawn_restarts_stopped_container(orchestrator, docker_client):
    """A stopped node is started again, keeping its container."""
    stopped = node("alice", status="exited")
    docker_client.containers.get.side_effect = None
    docker_client.containers.get.return_value = stopped

    orchestrator.spawn_user_node("alice")

    stopped.start.assert_called_once()
    docker_client.containers.run.assert_not_called()


def test_max_users_limit_enforcement(orchestrator, docker_client):
    """The limit counts running nodes (it was never enforced while the label filter was wrong)."""
    docker_client.containers.list.return_value = [node("a"), node("b")]

    with pytest.raises(MaxUsersReachedError):
        orchestrator.spawn_user_node("charlie")
    docker_client.containers.run.assert_not_called()


def test_is_ready_probes_the_gateway(orchestrator):
    """Ready means routable: the user's health endpoint answers through Traefik."""
    with patch("src.services.orchestrator.httpx.get") as get:
        get.return_value = MagicMock(status_code=404)  # Traefik has no route yet
        assert orchestrator.is_ready("alice") is False

        get.return_value = MagicMock(status_code=200)
        assert orchestrator.is_ready("alice") is True
        get.assert_called_with(
            "http://flc-gateway/api/health", headers={"Host": "alice.localhost"}, timeout=2
        )

        get.side_effect = httpx.ConnectError("gateway down")
        assert orchestrator.is_ready("alice") is False


def test_stop_user_node(orchestrator, docker_client):
    """Stopping removes the container; a missing node is not an error."""
    orchestrator.stop_user_node("ghost")

    container = MagicMock()
    docker_client.containers.get.side_effect = None
    docker_client.containers.get.return_value = container
    orchestrator.stop_user_node("alice")

    docker_client.containers.get.assert_called_with("flc-node-alice")
    container.stop.assert_called_once()
    container.remove.assert_called_once()
