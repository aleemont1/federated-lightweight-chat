"""Global node settings"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Centralized configuration.
    Reads configs from env variables.
    """

    app_name: str = "FLC Node"
    server_port: int = 8000

    node_id: str | None = None

    advertised_addr: str | None = None

    peers: str = ""

    db_name: str | None = None

    model_config = {"env_file": ".env"}


settings = Settings()

if not settings.advertised_addr:
    settings.advertised_addr = f"http://localhost:{settings.server_port}"
