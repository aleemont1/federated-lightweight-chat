"""
Unit tests for API Routes.
Uses FastAPI TestClient to simulate HTTP requests and WebSockets.
Mocks core services (NodeService, ConnectionManager) to test endpoints in isolation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Import the module we are patching to use patch.object (safer than string strings)
import src.api.routes
from src.core.message import Message
from src.main import app

# Create TestClient
client = TestClient(app)
TOKEN = "node-secret"


@pytest.fixture(autouse=True)
def session():
    """Configures the node token and logs the test client in with it."""
    with patch.object(src.api.routes.settings, "node_token", TOKEN):
        client.cookies.set(src.api.routes.SESSION_COOKIE, TOKEN)
        yield
        client.cookies.clear()


# --- Fixtures ---


@pytest.fixture
def mock_node_service():
    """Patches the global node_service singleton in the routes module."""
    # Use patch.object for robust patching (avoids string path typos)
    with patch.object(src.api.routes, "node_service") as mock_service:
        # 2. Setup 'initialize' (async method) -> Must be AsyncMock
        mock_service.initialize = AsyncMock(return_value=None)

        # 3. Setup 'state' property (mock object with attributes)
        mock_state = MagicMock()
        mock_state.node_id = "test_user"
        mock_state.get_clock.return_value = {"test_user": 1}
        mock_service.state = mock_state

        # 4. Setup 'storage' property
        mock_storage = MagicMock()
        mock_storage.get_room_messages.return_value = []
        mock_storage.get_known_rooms.return_value = ["general", "topic_x"]
        mock_service.storage = mock_storage

        yield mock_service


@pytest.fixture
def mock_websocket_manager():
    """
    Patches the global websocket manager in the routes.
    This prevents the code from trying to connect to a real Redis instance
    AND ensures the WebSocket handshake is completed correctly in tests.
    """
    with patch.object(src.api.routes, "manager") as mock_mgr:
        # Setup async methods

        # CRITICAL FIX: The connect mock must accept the websocket to complete the handshake
        async def mock_connect_side_effect(websocket, room_id):
            await websocket.accept()

        mock_mgr.connect = AsyncMock(side_effect=mock_connect_side_effect)
        # Setup sync methods
        mock_mgr.disconnect = MagicMock()
        yield mock_mgr


# --- Tests ---


def test_health_check(mock_node_service):
    """Test /health endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    # Note: Mock returns initialized=True by default in fixture
    assert data["initialized"] is True


@pytest.mark.parametrize(
    "method,path",
    [("get", "/api/rooms"), ("get", "/api/messages"), ("post", "/api/internal/sync")],
)
def test_routes_wait_for_initialization(mock_node_service, method, path):
    """Until the node has started, data routes answer 503 instead of crashing."""
    mock_node_service.storage = None
    response = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
    assert response.status_code == 503


def test_send_message_waits_for_initialization(mock_node_service):
    """Sending before startup is refused, not attempted."""
    mock_node_service.storage = None
    mock_node_service.send = AsyncMock()
    response = client.post("/api/messages", json={"content": "hi", "room_id": "general"})
    assert response.status_code == 503
    mock_node_service.send.assert_not_awaited()


def test_get_messages(mock_node_service):
    """Test retrieving messages."""
    mock_msg = Message(room_id="general", sender_id="bob", content="hello")
    mock_node_service.storage.get_room_messages.return_value = [mock_msg]

    response = client.get("/api/messages?room_id=general&limit=10")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["content"] == "hello"
    mock_node_service.storage.get_room_messages.assert_called_once_with(room_id="general", limit=10, offset=0)


def test_get_messages_rejects_unbounded_page(mock_node_service):
    """Page size is capped so a client can't dump the whole DB in one request."""
    assert client.get("/api/messages?limit=100000").status_code == 422
    assert client.get("/api/messages?offset=-1").status_code == 422


def test_get_rooms(mock_node_service):
    """Test retrieving known rooms."""
    response = client.get("/api/rooms")
    assert response.status_code == 200
    data = response.json()
    assert "general" in data
    assert "topic_x" in data


def test_send_message(mock_node_service):
    """Sending delegates to the node service, which stores and replicates."""
    sent = Message(room_id="general", sender_id="test_user", content="Hello World")
    mock_node_service.send = AsyncMock(return_value=sent)

    response = client.post("/api/messages", json={"content": "Hello World", "room_id": "general"})

    assert response.status_code == 201
    assert response.json()["content"] == "Hello World"
    mock_node_service.send.assert_awaited_once_with("general", "Hello World")


def test_internal_sync_returns_missing_messages(mock_node_service):
    """Anti-entropy endpoint answers a peer's summary with what the peer lacks."""
    missing = Message(room_id="general", sender_id="test_user", content="you missed this")
    mock_node_service.storage.missing_messages.return_value = [missing]

    response = client.post("/api/internal/sync", json={"general": {"test_user": [[1, 2]]}})

    assert response.status_code == 200
    assert response.json()[0]["content"] == "you missed this"
    mock_node_service.storage.missing_messages.assert_called_once_with({"general": {"test_user": [(1, 2)]}})


@pytest.mark.parametrize("ranges", ["lots", [[1]], [["a", "b"]], 7])
def test_internal_sync_rejects_malformed_summary(mock_node_service, ranges):
    """Each writer maps to a list of [low, high] integer pairs."""
    response = client.post("/api/internal/sync", json={"general": {"bob": ranges}})
    assert response.status_code == 422


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


# --- Authentication ---


def test_auth_link_sets_session_cookie():
    """The orchestrator's login link is traded for the session cookie, then the chat loads."""
    client.cookies.clear()
    response = client.get(f"/api/auth?token={TOKEN}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert f"{src.api.routes.SESSION_COOKIE}={TOKEN}" in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "secure" not in response.headers["set-cookie"].lower()  # plain HTTP locally


def test_session_cookie_is_secure_with_tls():
    """Behind HTTPS the cookie must never travel over plain HTTP."""
    client.cookies.clear()
    with patch.object(src.api.routes.settings, "tls", True):
        response = client.get(f"/api/auth?token={TOKEN}", follow_redirects=False)
    assert "secure" in response.headers["set-cookie"].lower()


def test_logout_clears_only_this_browsers_cookie(mock_node_service):
    """Logout ends the session here; the node and the token stay valid for other browsers."""
    response = client.post("/api/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert f'{src.api.routes.SESSION_COOKIE}=""' in response.headers["set-cookie"]
    mock_node_service.shutdown.assert_not_called()


@pytest.mark.parametrize("token", ["wrong", "", "\u00e9"])
def test_auth_link_rejects_bad_token(token):
    """Wrong, empty and non-ASCII tokens are refused (non-ASCII must not crash the comparison)."""
    assert client.get("/api/auth", params={"token": token}).status_code == 401


def test_auth_fails_closed_without_configured_token():
    """A node started without NODE_TOKEN accepts nobody, not everybody."""
    with patch.object(src.api.routes.settings, "node_token", ""):
        assert client.get("/api/auth", params={"token": ""}).status_code == 401
        assert client.get("/api/rooms").status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [("get", "/api/rooms"), ("get", "/api/messages"), ("post", "/api/messages")],
)
def test_browser_routes_require_session(mock_node_service, method, path):
    """Without the cookie, someone who knows the subdomain can't read or post as the user."""
    client.cookies.clear()
    kwargs = {"json": {"content": "hi", "room_id": "general"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_health_and_internal_sync_need_no_session(mock_node_service):
    """Health is public; internal sync is guarded by the gateway, not by the cookie."""
    client.cookies.clear()
    mock_node_service.storage.missing_messages.return_value = []
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/internal/sync", json={}).status_code == 200


def test_websocket_requires_session(mock_websocket_manager):
    """The socket is closed before subscribing anyone without the cookie."""
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/general"):
            pass
    mock_websocket_manager.connect.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "", "room_id": "general"},
        {"content": "x" * 4001, "room_id": "general"},
        {"content": "hi", "room_id": "<img src=x onerror=alert(1)>"},
        {"content": "hi", "room_id": "a" * 33},
    ],
)
def test_send_message_validates_input(mock_node_service, payload):
    """Empty or huge messages and room names outside [a-z0-9-]{1,32} are refused."""
    mock_node_service.send = AsyncMock()
    assert client.post("/api/messages", json=payload).status_code == 422
    mock_node_service.send.assert_not_awaited()
