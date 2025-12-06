"""
Unit tests for API Routes.
Uses FastAPI TestClient to simulate HTTP requests.
Mocks core services (NodeService, AuthProvider) to test endpoints in isolation.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_current_user
from src.core.auth_models import User
from src.core.message import Message

# Import app inside tests or fixtures to ensure patches apply if needed,
# though patching modules should work fine with global app.
from src.main import app

# Create TestClient
client = TestClient(app)

# --- Fixtures ---


@pytest.fixture
def mock_node_service():
    """Patches the global node_service singleton in the routes module."""
    with patch("src.api.routes.node_service") as mock_service:
        # 1. Setup 'is_initialized' (sync method)
        mock_service.is_initialized.return_value = True

        # 2. Setup 'initialize' (async method) -> Must be AsyncMock
        mock_service.initialize = AsyncMock(return_value=None)

        # 3. Setup 'state' property (mock object with attributes)
        mock_state = MagicMock()
        mock_state.node_id = "test_user"
        mock_state.get_clock.return_value = {"test_user": 1}
        mock_service.state = mock_state

        # 4. Setup 'storage' property
        mock_storage = MagicMock()
        mock_storage.get_all_room_messages.return_value = []
        mock_storage.message_exists.return_value = False
        mock_service.storage = mock_storage

        yield mock_service


@pytest.fixture
def mock_auth_provider():
    """Patches the global auth provider in the routes module."""
    with patch("src.api.routes.current_auth_provider") as mock_auth:
        # Crucial: authenticate MUST be an AsyncMock because it's awaited in the route.
        # We replace the method on the mock object with a fresh AsyncMock.
        mock_auth.authenticate = AsyncMock()
        yield mock_auth


@pytest.fixture
def override_auth_dependency():
    """Overrides the get_current_user dependency to bypass auth checks."""
    user_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
    user = User(id=user_id, username="test_user", is_authenticated=True)
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides = {}


# --- Tests ---


def test_health_check(mock_node_service):
    """Test /health endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    # Note: Mock returns initialized=True by default in fixture
    assert data["initialized"] is True


@pytest.mark.asyncio
async def test_login_success(mock_auth_provider, mock_node_service):
    """Test successful login."""
    # 1. Prepare valid User object
    user_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
    fake_user = User(id=user_id, username="alice", is_authenticated=True)

    # 2. Configure Auth Mock to return this user when awaited
    mock_auth_provider.authenticate.return_value = fake_user

    # 3. Simulate Node NOT initialized yet (so it triggers initialization)
    mock_node_service.is_initialized.return_value = False

    # 4. Perform Request
    response = client.post("/api/login", json={"username": "alice", "password": ""})

    # 5. Verify
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "alice"
    assert data["id"] == str(user_id)

    # Verify initialize called
    mock_node_service.initialize.assert_awaited_once_with("alice")


@pytest.mark.asyncio
async def test_login_failure(mock_auth_provider):
    """Test login failure."""
    # Configure Auth Mock to return None
    mock_auth_provider.authenticate.return_value = None

    response = client.post("/api/login", json={"username": "bad", "password": ""})

    assert response.status_code == 401


def test_get_messages(mock_node_service, override_auth_dependency):
    """Test retrieving messages."""
    mock_msg = Message(room_id="general", sender_id="bob", content="hello")
    mock_node_service.storage.get_all_room_messages.return_value = [mock_msg]

    response = client.get("/api/messages?room_id=general&limit=10")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["content"] == "hello"


def test_send_message(mock_node_service, override_auth_dependency):
    """Test sending a message."""
    payload = {"content": "Hello World", "room_id": "general"}

    response = client.post("/api/messages", json=payload)

    assert response.status_code == 201
    assert response.json()["content"] == "Hello World"

    mock_node_service.state.increment_clock.assert_called_with("general")
    mock_node_service.storage.add_message.assert_called_once()


@pytest.mark.asyncio
async def test_replication_receive(mock_node_service):
    """Test receiving gossip."""
    msg_payload = Message(room_id="general", sender_id="remote", content="sync").model_dump(mode="json")

    response = client.post("/api/replication", json=msg_payload)

    assert response.status_code == 200
    # Update expectation to match actual API behavior ("accepted")
    assert response.json()["status"] == "replicated"

    mock_node_service.state.update_clock.assert_called()
    mock_node_service.storage.add_message.assert_called()


def test_replication_idempotency(mock_node_service):
    """Test ignoring duplicates."""
    mock_node_service.storage.message_exists.return_value = True
    msg_payload = Message(room_id="general", sender_id="remote", content="sync").model_dump(mode="json")

    response = client.post("/api/replication", json=msg_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    mock_node_service.storage.add_message.assert_not_called()
