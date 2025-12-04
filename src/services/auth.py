"""Authentication Provider, handles the login and auth of users."""

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Optional

from src.core.auth_models import LoginRequest, User

logger = logging.getLogger(__name__)


class IAuthProvider(ABC):
    """
    Abstract interface for the authentication provider.
    """

    @abstractmethod
    async def authenticate(self, credentials: LoginRequest) -> Optional[User]:
        """
        Verifies credentials and returns a User if valid,
        otherwise None or raises an Excepion
        """
        pass


class DummyAuthProvider(IAuthProvider):
    """
    Dummy implementation of the Auth Provider, used
    only for API endpoints development without
    verifying password, and using only a username
    for node's authentication
    """

    async def authenticate(self, credentials: LoginRequest) -> Optional[User]:
        if not credentials.username.strip():
            logger.warning("Login attempt with empty username")
            return None

        user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, credentials.username)

        return User(id=user_uuid, username=credentials.username.strip(), is_authenticated=True)


# Create a singleton for Auth provider
current_auth_provider: IAuthProvider = DummyAuthProvider()
