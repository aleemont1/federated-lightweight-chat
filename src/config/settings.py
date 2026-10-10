"""Global node settings"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Centralized configuration.
    Reads configs from env variables.
    """

    model_config = {"env_file": ".env"}

    node_id: str | None = None

    # Address peers use to reach this node.
    advertised_addr: str = "http://localhost:8000"

    db_name: str | None = None

    redis_url: str = "redis://localhost:6379"

    # Secret proving a browser logged in through the orchestrator. Empty means nobody can.
    node_token: str = ""

    # Orchestrator: user nodes are served at http://<username>.<public_domain>
    max_users: int = 10
    public_domain: str = "localhost"
    accounts_db: str = "accounts.db"


settings = Settings()
