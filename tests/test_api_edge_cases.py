"""
Unit tests for API failure modes and edge cases.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


@pytest.fixture
def mock_node_service_broken():
    """Patches node_service to simulate uninitialized or broken states."""
    with patch("src.api.dependencies.node_service") as dep_mock:
        with patch("src.api.routes.node_service") as route_mock:
            # We explicitly link them so they behave identically
            # (though strictly they are different import references)
            yield dep_mock, route_mock


def test_auth_dependency_not_initialized(mock_node_service_broken):
    """
    Test 401 when accessing protected route without initialization.
    Target: src/api/dependencies.py:18-23
    """
    dep_mock, _ = mock_node_service_broken

    # Simulate Uninitialized Node
    dep_mock.is_initialized.return_value = False
    dep_mock.state = None

    response = client.get("/api/me")

    assert response.status_code == 401
    assert "Node not initialized" in response.json()["detail"]


def test_get_rooms_storage_error(mock_node_service_broken):
    """
    Test 500 when storage is not ready.
    Target: src/api/routes.py:78
    """
    dep_mock, route_mock = mock_node_service_broken

    # Bypass Auth
    dep_mock.is_initialized.return_value = True
    dep_mock.state.node_id = "test"

    # Simulate Missing Storage
    route_mock.storage = None

    response = client.get("/api/rooms")

    assert response.status_code == 500
    assert "Storage not ready" in response.json()["detail"]


@pytest.mark.asyncio
async def test_sync_room_node_not_ready():
    """
    Test 500 when syncing without initialization.
    Target: src/api/routes.py:87
    """
    # We need to mock the dependency to pass auth,
    # but fail the check inside the route itself.

    with patch("src.api.routes.node_service") as route_mock:
        # Simulate initialized for dependency...
        # Actually, the dependency checks initialization too.
        # So to hit line 87 (check inside sync_room), we must pass dependency check
        # but somehow fail the check in the function?
        # The code in sync_room checks `if not node_service.is_initialized():`.
        # The dependency *also* checks this.
        # To hit the inner check, the dependency must pass, but the global service must flip to False?
        # Or more likely, we test this by bypassing the dependency via override.

        route_mock.is_initialized.return_value = False

        # Override auth dependency to allow request through
        from src.api.dependencies import get_current_user

        app.dependency_overrides[get_current_user] = lambda: MagicMock(username="test")

        response = client.post("/api/rooms/general/sync")

        app.dependency_overrides = {}  # Cleanup

        assert response.status_code == 500
        assert "Node not ready" in response.json()["detail"]


def test_send_message_service_unavailable(mock_node_service_broken):
    """
    Test 500 when sending message if state/storage missing.
    Target: src/api/routes.py:98-105
    """
    dep_mock, route_mock = mock_node_service_broken

    # Pass Auth
    dep_mock.is_initialized.return_value = True
    dep_mock.state.node_id = "test"

    # Fail Route Check (state is None)
    route_mock.state = None

    response = client.post("/api/messages", json={"content": "hi", "room_id": "general"})

    assert response.status_code == 500
    assert "Node service unavailable" in response.json()["detail"]


def test_login_initialization_failure():
    """
    Test 500 if node initialization crashes during login.
    Target: src/api/routes.py:52-55
    """
    with patch("src.api.routes.current_auth_provider") as mock_auth:
        with patch("src.api.routes.node_service") as mock_node:
            # 1. Auth succeeds
            mock_user = MagicMock()
            mock_user.is_authenticated = True
            mock_auth.authenticate = AsyncMock(return_value=mock_user)

            # 2. Node needs init
            mock_node.is_initialized.return_value = False

            # 3. Init EXPLODES
            mock_node.initialize.side_effect = Exception("DB Disk Full")

            response = client.post("/api/login", json={"username": "bob", "password": ""})

            assert response.status_code == 500
            assert "Node initialization failed" in response.json()["detail"]
            assert "DB Disk Full" in response.json()["detail"]
