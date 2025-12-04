"""Unit tests for dummy auth provider"""

import uuid

import pytest

from src.core.auth_models import LoginRequest
from src.services.auth import DummyAuthProvider


@pytest.fixture
def auth_provider() -> DummyAuthProvider:
    """Auth provider fixture"""
    return DummyAuthProvider()


@pytest.mark.asyncio
async def test_auth_success(auth_provider: DummyAuthProvider) -> None:
    """
    Test authentication
    """
    credentials: LoginRequest = LoginRequest(username="alice", password="password")
    user = await auth_provider.authenticate(credentials)

    assert user is not None
    assert user.username == "alice"
    assert user.is_authenticated is True
    assert user.id == uuid.uuid5(uuid.NAMESPACE_DNS, "alice")


@pytest.mark.asyncio
async def test_auth_empty_username_failure(auth_provider: DummyAuthProvider) -> None:
    """
    Test authentication result with empty username
    """
    credentials: LoginRequest = LoginRequest(username="", password="password")
    user = await auth_provider.authenticate(credentials)

    assert user is None


@pytest.mark.asyncio
async def test_auth_username_with_trailing_spaces(auth_provider: DummyAuthProvider) -> None:
    """
    Test authentication with trailing spaces in username
    """

    credentials: LoginRequest = LoginRequest(username="alice", password="password")
    user = await auth_provider.authenticate(credentials)

    assert user is not None
    assert user.username == "alice"
    assert user.is_authenticated is True
    assert user.id == uuid.uuid5(uuid.NAMESPACE_DNS, "alice")
