"""Authentication models to handle logins and user authentication"""

from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Defines a Login request schema."""

    username: str
    password: str = Field(default="", description="Optional for dummy login")


class User(BaseModel):
    """Represents a user"""

    id: UUID
    username: str
    is_authenticated: bool = False
