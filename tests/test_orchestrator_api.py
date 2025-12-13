"""
Unit tests for the Orchestrator API endpoints.
Tests the landing page rendering and login orchestration logic.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Import the app object to wrap in TestClient
from src.orchestrator_api import app
from src.services.orchestrator import MaxUsersReachedError

client = TestClient(app)


@pytest.fixture
def mock_orch_service():
    """Patches the singleton orchestrator service used by the API."""
    with patch("src.orchestrator_api.orchestrator_service") as mock:
        yield mock


def test_landing_page(mock_orch_service):
    """Test that GET / returns the HTML landing page with active users."""
    mock_orch_service.get_active_users.return_value = ["alice", "bob"]

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "alice" in response.text
    assert "bob" in response.text
    assert "FLC Network" in response.text


def test_login_success(mock_orch_service):
    """Test successful login redirects to the user's subdomain."""
    # Setup mock to succeed
    mock_orch_service._sanitize_username.return_value = "alice_safe"
    mock_orch_service.spawn_user_node.return_value = None

    response = client.post("/login", data={"username": "Alice"})

    # 303 See Other is standard for POST -> GET redirect
    # TestClient follows redirects by default? No, usually returns the redirect response.
    # Note: Starlette TestClient follows redirects by default, let's disable it to check the header
    response = client.post("/login", data={"username": "Alice"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "http://alice_safe.localhost"
    mock_orch_service.spawn_user_node.assert_called_with("Alice")


def test_login_max_users_error(mock_orch_service):
    """Test that 503 is returned when the system is full."""
    mock_orch_service.spawn_user_node.side_effect = MaxUsersReachedError("Full")

    response = client.post("/login", data={"username": "Charlie"})

    assert response.status_code == 503
    assert "Full" in response.json()["detail"]


def test_login_generic_error(mock_orch_service):
    """Test that 500 is returned for unexpected errors."""
    mock_orch_service.spawn_user_node.side_effect = RuntimeError("Docker exploded")

    response = client.post("/login", data={"username": "Dave"})

    assert response.status_code == 500
    assert "Docker exploded" in response.json()["detail"]
