"""
Unit tests for the Orchestrator API endpoints.
OrchestratorService is mocked; accounts use a real SQLite file.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.orchestrator_api import app, get_accounts, get_orchestrator
from src.services.accounts import Accounts
from src.services.orchestrator import MaxUsersReachedError

client = TestClient(app)
PASSWORD = "correct horse"


@pytest.fixture
def orch():
    """Replaces the orchestrator dependency, so no Docker daemon is needed."""
    mock = MagicMock()
    mock.get_active_users.return_value = []
    mock.node_token.return_value = "tok123"
    app.dependency_overrides[get_orchestrator] = lambda: mock
    yield mock
    app.dependency_overrides.clear()


@pytest.fixture
def accounts(tmp_path):
    """A real account store where alice already has a password."""
    store = Accounts(str(tmp_path / "accounts.db"))
    store.login("alice", PASSWORD)
    app.dependency_overrides[get_accounts] = lambda: store
    yield store
    app.dependency_overrides.pop(get_accounts, None)


def test_landing_lists_and_links_active_users(orch):
    """Each running node is listed with a link to its subdomain."""
    orch.get_active_users.return_value = ["alice", "bob"]

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="http://alice.localhost"' in response.text
    assert "2 Online" in response.text


def test_login_answers_with_loading_page_and_login_link(orch, accounts):
    """After the password check the node starts and the browser gets the token link, directly in the POST."""
    response = client.post("/login", data={"username": " Alice ", "password": PASSWORD})

    assert response.status_code == 200
    orch.spawn_user_node.assert_called_once_with("alice")
    assert "/status/alice" in response.text
    assert "http://alice.localhost/api/auth?token=tok123" in response.text


def test_first_login_claims_the_name(orch, accounts):
    """A new username is registered with the password used."""
    assert client.post("/login", data={"username": "bob", "password": "bobs password"}).status_code == 200
    assert accounts.verify("bob", "bobs password")


def test_login_wrong_password(orch, accounts):
    """A taken name with the wrong password gets no node and no link."""
    response = client.post("/login", data={"username": "alice", "password": "guess guess"})

    assert response.status_code == 401
    assert "Wrong username or password" in response.text
    assert "tok123" not in response.text
    orch.spawn_user_node.assert_not_called()


@pytest.mark.parametrize(
    "username,password,message",
    [("alice smith", PASSWORD, "Usernames are"), ("dave", "short", "at least 8")],
)
def test_login_rejects_invalid_input(orch, accounts, username, password, message):
    """Invalid names and too-short new passwords are refused with a message."""
    response = client.post("/login", data={"username": username, "password": password})

    assert response.status_code == 400
    assert message in response.text
    orch.spawn_user_node.assert_not_called()


def test_login_when_full(orch, accounts):
    """A full network is reported on the landing page."""
    orch.spawn_user_node.side_effect = MaxUsersReachedError("System full (10/10 users).")

    response = client.post("/login", data={"username": "alice", "password": PASSWORD})

    assert response.status_code == 503
    assert "System full" in response.text


def test_loading_page_is_not_reachable_by_get(orch):
    """The login link must only be handed out after a password check."""
    assert client.get("/loading?user=alice").status_code == 404


def test_status_reports_readiness(orch):
    """Ready only when the orchestrator says the node is routable; invalid names are never ready."""
    orch.is_ready.return_value = True
    assert client.get("/status/alice").json() == {"ready": True}
    orch.is_ready.assert_called_once_with("alice")

    assert client.get("/status/no_such").json() == {"ready": False}


def test_stop_needs_the_password(orch, accounts):
    response = client.post("/stop", data={"username": "Alice", "password": PASSWORD}, follow_redirects=False)

    assert response.status_code == 303
    orch.stop_user_node.assert_called_once_with("alice")


@pytest.mark.parametrize("password", ["", "guess guess"])
def test_stop_refuses_without_the_password(orch, accounts, password):
    """Previously anyone could stop anyone's node by name."""
    response = client.post("/stop", data={"username": "alice", "password": password})

    assert response.status_code in (401, 422)
    orch.stop_user_node.assert_not_called()
