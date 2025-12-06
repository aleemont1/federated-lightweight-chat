"""
FastAPI dependencies for request auth and state validation.
"""

import uuid

from fastapi import HTTPException, status

from src.core.auth_models import User
from src.services.node import node_service


async def get_current_user() -> User:
    """
    Dependency that checks if the node is initialized (user is logged in).
    Returns the current user content.
    """
    if not node_service.is_initialized() or not node_service.state:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Node not initialized. Please login first."
        )
    user_id = uuid.uuid5(uuid.NAMESPACE_DNS, node_service.state.node_id)
    return User(id=user_id, username=node_service.state.node_id, is_authenticated=True)
