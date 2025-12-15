"""
Containers orchestrator service.
Manages the lifecycle of dynamic users through the Docker Socket.
"""

import logging
import re
from typing import List

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

logger = logging.getLogger(__name__)


class MaxUsersReachedError(Exception):
    """Raised when the maximum number of concurrent user containers is reached."""

    pass


class OrchestratorService:
    """
    Control plane for FLC.
    Spawns, monitors and kills user node containers.
    """

    def __init__(self, max_users: int = 10, network_name: str = "flc-net"):
        self.max_users = max_users
        self.network_name = network_name
        self._client = docker.from_env()

    def _sanitize_username(self, username: str) -> str:
        """Converts username in a Docker-safe and DNS-safe string"""
        # lowercase and non-alphanumerics converted to _
        return re.sub(r"[^a-z0-9]", "_", username.lower()).strip("_")

    def get_active_users(self) -> List[str]:
        """Returns a list of currently running user nodes"""
        containers = self._client.containers.list(filters={"label": "app=flc_node"})
        return [c.name.replace("flc-node-", "") for c in containers]

    def spawn_user_node(self, username: str) -> Container:
        """
        Spawns a Container for the given user.
        """
        safe_name = self._sanitize_username(username)
        container_name = f"flc-node-{safe_name}"

        try:
            container = self._client.containers.get(container_name)
            if container.status != "running":
                logger.info("Restarting existing container for %s", safe_name)
                container.start()
            return container
        except NotFound:
            pass  # proceed to creation

        active_count = len(self.get_active_users())
        if active_count >= self.max_users:
            raise MaxUsersReachedError(f"System full ({active_count}/{self.max_users} users).")

        logger.info("Spawning new node for %s...", safe_name)

        image_name = "flc-node:latest"

        # Traefik configuration labels
        labels = {
            "app": "flc-node",
            "traefik.enable": "true",
            f"traefik.http.routers.{safe_name}.rule": f"Host(`{safe_name}.localhost`)",
            f"traefik.http.routers.{safe_name}.entrypoints": "web",
            f"traefik.http.services.{safe_name}.loadbalancer.server.port": "8000",
        }

        # Inject ADVERTISED_ADDR so other nodes can reach this one.
        # Inside the docker network, the container is reachable by its container name.
        internal_address = f"http://{container_name}:8000"

        env = {
            "NODE_ID": safe_name,
            "REDIS_URL": "redis://flc-redis:6379",
            "DB_NAME": f"/data/{safe_name}.db",
            "ADVERTISED_ADDR": internal_address,
        }

        try:
            container = self._client.containers.run(
                image=image_name,
                name=container_name,
                detach=True,
                network=self.network_name,
                labels=labels,
                environment=env,
                volumes={f"flc_data_{safe_name}": {"bind": "/data", "mode": "rw"}},
                restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
            )
            return container
        except APIError as e:
            logger.error("Docker API Error: %s", str(e))
            raise RuntimeError(f"Failed to spawn node: {e}")


# OrchestratorService singleton
orchestrator_service = OrchestratorService()
