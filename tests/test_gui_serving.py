"""
Integration tests for Static File Serving (GUI).
Ensures the backend correctly serves the Single Page Application assets.
"""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_root_serves_index_html():
    """GET / serves the chat client."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="chat-view"' in response.text


def test_static_assets_routing():
    """Static assets are mounted under /static."""
    response = client.get("/static/js/app.js")
    assert response.status_code == 200
    assert "ChatApp" in response.text


def test_api_is_separate_from_static():
    """
    Ensure API routes are not shadowed by static files.
    """
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
