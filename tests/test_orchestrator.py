"""
Unit tests for the Orchestrator Service.
"""

from unittest.mock import MagicMock, patch

import pytest
from docker.errors import NotFound

from src.services.orchestrator import MaxUsersReachedError, OrchestratorService


@pytest.fixture
def mock_docker():
    """Mocks the docker.from_env() client."""
    with patch("src.services.orchestrator.docker.from_env") as mock:
        yield mock


@pytest.fixture
def orchestrator(mock_docker):
    """Returns an Orchestrator instance with a low user limit for testing."""
    return OrchestratorService(max_users=2)


def test_spawn_container_success(orchestrator, mock_docker):
    """Test that a container is created when it doesn't exist."""
    client = mock_docker.return_value

    # 1. Simulate that the container does NOT exist
    client.containers.get.side_effect = NotFound("Container not found")

    # 2. Simulate no active users currently
    client.containers.list.return_value = []

    username = "alice"
    orchestrator.spawn_user_node(username)

    # Verification
    client.containers.run.assert_called_once()
    args, kwargs = client.containers.run.call_args

    # Verify Traefik labels
    labels = kwargs["labels"]
    assert labels[f"traefik.http.routers.{username}.rule"] == f"Host(`{username}.localhost`)"
    assert kwargs["environment"]["NODE_ID"] == username


def test_spawn_container_idempotency(orchestrator, mock_docker):
    """If container exists, do not spawn a new one, just return it."""
    client = mock_docker.return_value
    mock_container = MagicMock()
    mock_container.name = "flc-node-alice"
    mock_container.status = "running"

    # 1. Simulate EXISTING container (no exception raised)
    client.containers.get.return_value = mock_container
    client.containers.get.side_effect = None  # Clear any previous side effects

    result = orchestrator.spawn_user_node("alice")

    client.containers.run.assert_not_called()
    assert result.name == "flc-node-alice"


def test_max_users_limit(orchestrator, mock_docker):
    """Ensure we respect the hard limit of containers."""
    client = mock_docker.return_value

    # 1. Simulate that the specific user 'charlie' does NOT exist
    # (Otherwise it would just return the existing container)
    client.containers.get.side_effect = NotFound("Container not found")

    # 2. Simulate system is full (2 active users for max_users=2)
    client.containers.list.return_value = [MagicMock(), MagicMock()]

    with pytest.raises(MaxUsersReachedError):
        orchestrator.spawn_user_node("charlie")


def test_sanitize_username(orchestrator):
    """Ensure usernames are docker-safe."""
    # We access the protected method for unit testing
    # pylint: disable=protected-access
    safe = orchestrator._sanitize_username("Alice_Bob 123")
    assert safe == "alice_bob_123"
