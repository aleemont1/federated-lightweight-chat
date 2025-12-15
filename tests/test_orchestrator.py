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


def test_spawn_container_injects_network_config(orchestrator, mock_docker):
    """
    Test that the container is spawned with the correct ADVERTISED_ADDR.
    This fixes the bug where nodes gossiped with 'localhost' (themselves).
    """
    client = mock_docker.return_value
    client.containers.get.side_effect = NotFound("Container not found")
    client.containers.list.return_value = []

    username = "alice"
    orchestrator.spawn_user_node(username)

    # Verification
    client.containers.run.assert_called_once()
    _, kwargs = client.containers.run.call_args

    env = kwargs["environment"]

    # 1. Verify Node ID
    assert env["NODE_ID"] == "alice"

    # 2. Verify Internal Docker DNS Address (The Fix)
    # It must NOT be localhost. It must be the container name on the bridge network.
    expected_addr = "http://flc-node-alice:8000"
    assert env["ADVERTISED_ADDR"] == expected_addr


def test_spawn_container_idempotency(orchestrator, mock_docker):
    """If container exists, do not spawn a new one, just return it."""
    client = mock_docker.return_value
    mock_container = MagicMock()
    mock_container.name = "flc-node-alice"
    mock_container.status = "running"

    client.containers.get.return_value = mock_container
    client.containers.get.side_effect = None

    result = orchestrator.spawn_user_node("alice")

    client.containers.run.assert_not_called()
    assert result.name == "flc-node-alice"


def test_max_users_limit(orchestrator, mock_docker):
    """Ensure we respect the hard limit of containers."""
    client = mock_docker.return_value
    client.containers.get.side_effect = NotFound("Container not found")
    client.containers.list.return_value = [MagicMock(), MagicMock()]

    with pytest.raises(MaxUsersReachedError):
        orchestrator.spawn_user_node("charlie")
