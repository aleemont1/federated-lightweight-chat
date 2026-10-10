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
        client.images.get.return_value.id = "sha256:current"
        yield client


@pytest.fixture
def orchestrator(docker_client):
    """An orchestrator with a low user limit for testing."""
    return OrchestratorService(max_users=2)


def node(name, status="running", env=("NODE_TOKEN=tok",), image="sha256:current"):
    """A mocked node container, by default running the current image and carrying a token."""
    container = MagicMock(status=status, attrs={"Image": image, "Config": {"Env": [f"NODE_ID={name}", *env]}})
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


@pytest.mark.parametrize("legacy", [{"env": ()}, {"image": "sha256:old"}])
def test_outdated_container_is_recreated(orchestrator, docker_client, legacy):
    """
    No token: the node would accept nobody. Older image: it would keep running old code
    (and an old DB schema) forever, since rebuilding the stack doesn't touch user nodes.
    The data volume survives the recreation.
    """
    legacy = node("alice", **legacy)
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


def gateway(status=200, error=None):
    """Patches the HTTP client is_ready uses; returns (Client class mock, its get mock)."""
    client_cls = MagicMock()
    get = client_cls.return_value.__enter__.return_value.get
    get.return_value, get.side_effect = MagicMock(status_code=status), error
    return patch("src.services.orchestrator.httpx.Client", client_cls), client_cls, get


def test_is_ready_probes_the_gateway(orchestrator):
    """Ready means routable: the user's health endpoint answers through Traefik."""
    patched, _, _ = gateway(404)  # Traefik has no route yet
    with patched:
        assert orchestrator.is_ready("alice") is False

    patched, client_cls, get = gateway(200)
    with patched:
        assert orchestrator.is_ready("alice") is True
    client_cls.assert_called_once_with(verify=True, timeout=2)
    get.assert_called_once_with(
        "http://flc-gateway/api/health", headers={"Host": "alice.localhost"}, extensions={}
    )

    patched, _, _ = gateway(error=httpx.ConnectError("gateway down"))
    with patched:
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


@pytest.mark.parametrize("domain,verify", [("203.0.113.7.nip.io", True), ("localhost", False)])
def test_is_ready_with_tls_waits_for_a_valid_certificate(orchestrator, domain, verify):
    """
    Through HTTPS, presenting the user's name as SNI like a browser, and verifying the certificate:
    until Let's Encrypt issues it, Traefik's self-signed default would fail in the browser.
    *.localhost can't get a public certificate, so there only routing is checked.
    """
    host = f"alice.{domain}"
    patched, client_cls, get = gateway(200)
    with patched, patch.multiple("src.services.orchestrator.settings", tls=True, public_domain=domain):
        assert orchestrator.is_ready("alice") is True
    client_cls.assert_called_once_with(verify=verify, timeout=2)
    get.assert_called_once_with(
        "https://flc-gateway/api/health", headers={"Host": host}, extensions={"sni_hostname": host}
    )


def test_spawn_with_tls_asks_for_a_certificate(orchestrator, docker_client):
    """Node routers use the HTTPS entrypoint and request a certificate; the node knows (Secure cookie)."""
    with patch("src.services.orchestrator.settings.tls", True):
        orchestrator.spawn_user_node("alice")

    kwargs = docker_client.containers.run.call_args.kwargs
    assert kwargs["labels"]["traefik.http.routers.flc-alice.entrypoints"] == "websecure"
    assert kwargs["labels"]["traefik.http.routers.flc-alice.tls.certresolver"] == "le"
    assert kwargs["environment"]["TLS"] == "True"
