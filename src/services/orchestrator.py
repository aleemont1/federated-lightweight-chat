"""
Containers orchestrator service.
Manages the lifecycle of user nodes through the Docker socket.
"""

import logging
import re
import secrets
import threading
from typing import Dict, List, Optional

import docker
import httpx
from docker.errors import NotFound
from docker.models.containers import Container

from src.config.settings import settings

logger = logging.getLogger(__name__)

IMAGE = "flc-node:latest"
NETWORK = "flc-net"
GATEWAY = "flc-gateway"
NODE_LABEL = "app=flc-node"
NAME_PREFIX = "flc-node-"
# A DNS label, so that the username can be the node's subdomain.
USERNAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?")


class MaxUsersReachedError(Exception):
    """Raised when the maximum number of concurrent user containers is reached."""


def normalize_username(username: str) -> str:
    """Lowercases a username and checks it is a valid subdomain. Raises ValueError otherwise."""
    name = username.strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("Usernames are 1-32 letters, digits or '-', and can't start or end with '-'.")
    return name


class OrchestratorService:
    """
    Control plane for FLC.
    Spawns, monitors and removes user node containers. Usernames must be normalized.
    """

    def __init__(self, max_users: int):
        self.max_users = max_users
        self._client = docker.from_env()
        # Serializes spawns, so concurrent logins can't race on the same name or on the user limit.
        # ponytail: one global lock; per-user locks if spawning ever becomes a bottleneck.
        self._spawn_lock = threading.Lock()

    def _find(self, username: str) -> Optional[Container]:
        try:
            return self._client.containers.get(NAME_PREFIX + username)
        except NotFound:
            return None

    def get_active_users(self) -> List[str]:
        """Usernames of the running user nodes."""
        containers = self._client.containers.list(filters={"label": NODE_LABEL})
        return sorted(c.name.removeprefix(NAME_PREFIX) for c in containers)

    def is_ready(self, username: str) -> bool:
        """
        True once the gateway routes to the user's node. Traefik only routes healthy containers,
        and picks up health changes with a short delay, so "healthy" alone isn't "reachable".
        With TLS it also waits for the node's certificate: until Let's Encrypt issues it, Traefik
        serves a self-signed default, and the browser would show a certificate error.
        """
        host = f"{username}.{settings.public_domain}"
        if not settings.tls:
            url, extensions, verify = f"http://{GATEWAY}/api/health", {}, True
        else:
            # SNI picks the certificate, so present the user's name, as a browser would.
            # *.localhost can never get a public certificate: there, check routing only.
            url, extensions = f"https://{GATEWAY}/api/health", {"sni_hostname": host}
            verify = not host.endswith("localhost")
        try:
            with httpx.Client(verify=verify, timeout=2) as client:
                response = client.get(url, headers={"Host": host}, extensions=extensions)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def spawn_user_node(self, username: str) -> None:
        """Starts the user's node, creating it on first login. Idempotent."""
        with self._spawn_lock:
            container = self._find(username)
            if container and (
                self._env(container).get("NODE_TOKEN") is None
                or container.attrs["Image"] != self._client.images.get(IMAGE).id
            ):
                # Created before nodes had tokens, or from an older image. The volume is kept.
                logger.info("Recreating outdated container for %s", username)
                container.remove(force=True)
                container = None
            if container and container.status == "running":
                return

            active = len(self.get_active_users())
            if active >= self.max_users:
                raise MaxUsersReachedError(f"System full ({active}/{self.max_users} users).")

            if container:
                logger.info("Restarting existing container for %s", username)
                container.start()
                return

            logger.info("Spawning new node for %s...", username)
            router = f"flc-{username}"  # prefixed so a user can't shadow the orchestrator's router
            tls_labels = {f"traefik.http.routers.{router}.tls.certresolver": "le"} if settings.tls else {}
            self._client.containers.run(
                image=IMAGE,
                name=NAME_PREFIX + username,
                detach=True,
                init=True,
                network=NETWORK,
                labels={
                    "app": "flc-node",
                    "traefik.enable": "true",
                    # Node-to-node sync stays inside the Docker network.
                    f"traefik.http.routers.{router}.rule": (
                        f"Host(`{username}.{settings.public_domain}`) && !PathPrefix(`/api/internal`)"
                    ),
                    f"traefik.http.routers.{router}.entrypoints": "websecure" if settings.tls else "web",
                    f"traefik.http.services.{router}.loadbalancer.server.port": "8000",
                    **tls_labels,
                },
                environment={
                    "NODE_ID": username,
                    "NODE_TOKEN": secrets.token_urlsafe(32),
                    "REDIS_URL": "redis://flc-redis:6379",
                    "DB_NAME": f"/data/{username}.db",
                    # Peers reach each other by container name on the Docker network.
                    "ADVERTISED_ADDR": f"http://{NAME_PREFIX}{username}:8000",
                    "TLS": str(settings.tls),
                },
                volumes={f"flc_data_{username}": {"bind": "/data", "mode": "rw"}},
                restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
            )

    @staticmethod
    def _env(container: Container) -> Dict[str, str]:
        return dict(var.split("=", 1) for var in container.attrs["Config"]["Env"])

    def node_token(self, username: str) -> Optional[str]:
        """The secret the user's node accepts as proof of login, None if the node doesn't exist."""
        container = self._find(username)
        return self._env(container).get("NODE_TOKEN") if container else None

    def stop_user_node(self, username: str) -> None:
        """Stops and removes the user's container. Its data volume is kept for the next login."""
        container = self._find(username)
        if not container:
            logger.warning("Attempted to stop non-existent node: %s", username)
            return
        logger.info("Stopping container for %s...", username)
        container.stop(timeout=5)  # SIGTERM, then SIGKILL after 5s
        container.remove()
