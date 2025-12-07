"""
Unit tests for API Routes.
Uses FastAPI TestClient to simulate HTTP requests and WebSockets.
Mocks core services (NodeService, AuthProvider, ConnectionManager) to test endpoints in isolation.
"""

# Disable this warning as it is a false positive caused by pytest syntax
# pylint: disable=redefined-outer-scope

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
def mock_websocket_manager():
    """
    Patches the global websocket manager in the routes.
    This prevents the code from trying to connect to a real Redis instance
    AND ensures the WebSocket handshake is completed correctly in tests.
    """
    with patch("src.api.routes.manager") as mock_mgr:
        # Setup async methods

        # CRITICAL FIX: The connect mock must accept the websocket to complete the handshake
        async def mock_connect_side_effect(websocket, room_id):
            await websocket.accept()

        mock_mgr.connect = AsyncMock(side_effect=mock_connect_side_effect)
        mock_mgr.publish = AsyncMock()
        mock_mgr.broadcast = AsyncMock()
        # Setup sync methods
        mock_mgr.disconnect = MagicMock()
        yield mock_mgr


@pytest.fixture
def override_auth_dependency():
    """Overrides the get_current_user dependency to bypass auth checks."""
    user_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
    user = User(id=user_id, username="test_user", is_authenticated=True)
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides = {}


# --- Tests ---
# Disable this warning as it is a false positive caused by pytest syntax
# pylint: disable=unused-argument


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


def test_send_message(mock_node_service, override_auth_dependency, mock_websocket_manager):
    """Test sending a message (and verifying Redis publish)."""
    payload = {"content": "Hello World", "room_id": "general"}

    response = client.post("/api/messages", json=payload)

    assert response.status_code == 201
    assert response.json()["content"] == "Hello World"

    mock_node_service.state.increment_clock.assert_called_with("general")
    mock_node_service.storage.add_message.assert_called_once()

    # VERIFY: Message published to Redis
    mock_websocket_manager.publish.assert_called_once()
    # Optional: Verify args
    args, _ = mock_websocket_manager.publish.call_args
    assert args[0].content == "Hello World"
    assert args[1] == "general"


@pytest.mark.asyncio
async def test_replication_receive(mock_node_service, mock_websocket_manager):
    """Test receiving gossip (and verifying Redis publish)."""
    msg_payload = Message(room_id="general", sender_id="remote", content="sync").model_dump(mode="json")

    response = client.post("/api/replication", json=msg_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "replicated"

    mock_node_service.state.update_clock.assert_called()
    mock_node_service.storage.add_message.assert_called()

    # VERIFY: Replicated message published to Redis
    mock_websocket_manager.publish.assert_called_once()


def test_replication_idempotency(mock_node_service, mock_websocket_manager):
    """Test ignoring duplicates."""
    mock_node_service.storage.message_exists.return_value = True
    msg_payload = Message(room_id="general", sender_id="remote", content="sync").model_dump(mode="json")

    response = client.post("/api/replication", json=msg_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    mock_node_service.storage.add_message.assert_not_called()
    # Should NOT publish if ignored
    mock_websocket_manager.publish.assert_not_called()


def test_websocket_endpoint(mock_websocket_manager):
    """
    Test the WebSocket connection lifecycle.
    """
    # Use the context manager to simulate a connection
    # Note: connect() mock MUST accept the socket or this will fail
    with client.websocket_connect("/api/ws/general") as websocket:
        # Verify connect called on manager
        mock_websocket_manager.connect.assert_called_once()
        args, _ = mock_websocket_manager.connect.call_args
        # args[0] is the websocket object, args[1] is room_id
        assert args[1] == "general"

        # Send some text (optional, to verify loop processing)
        websocket.send_text("ping")

    # After context exit (disconnect), verify disconnect called
    mock_websocket_manager.disconnect.assert_called_once()
