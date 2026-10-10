<div align="center">

# Federated Lightweight Chat (FLC)

[![CI Pipeline](https://img.shields.io/github/actions/workflow/status/aleemont1/federated-lightweight-chat/ci.yml?branch=develop&style=for-the-badge)](https://github.com/aleemont1/federated-lightweight-chat/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![Black](https://img.shields.io/badge/black-%23000000.svg?style=for-the-badge&logo=black&logoColor=white)
![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)
![DigitalOcean](https://img.shields.io/badge/DigitalOcean-%230167ff.svg?style=for-the-badge&logo=digitalOcean&logoColor=white)
[![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge)](./LICENSE)

</div>

Federated Lightweight Chat (FLC) is a group chat built to study replication under failures.
Every user gets their own node (a container with its own SQLite database), every node keeps a
full copy of every room, and the copies converge on their own. A node keeps accepting messages
while it is cut off from the others or while Redis is down, and every node shows each room in
the same order, with replies always after what they answer.

## How it works

```
                    http://localhost                 http://<user>.localhost
browser ─────────────► Traefik (flc-gateway) ──────────────┬──────────────┐
                            │                              ▼              ▼
                            ▼                        flc-node-alice  flc-node-bob   ...
                     flc-orchestrator                 SQLite replica  SQLite replica
                     accounts, spawns nodes               │   ▲  pull anti-entropy ▲
                     via the Docker socket                │   └────────────────────┘
                                                          ▼
                                              flc-redis: discovery + pub/sub
```

- **Orchestrator**: checks the password (the first login claims a username), starts the user's
  node through the Docker socket, and hands the browser a login link carrying the
  node's secret token. The node trades it for a session cookie.
- **Gateway** (Traefik): routes `localhost` to the orchestrator and `<user>.localhost` to that
  user's node. It never routes `/api/internal`, so node-to-node sync stays on the Docker network.
- **Nodes**: each one writes locally and never waits for the others. Two replication paths:
  - *fast path*: a new message is published on Redis pub/sub and stored by every online node;
  - *anti-entropy*: every 2 s a node sends a random peer its summary (per room and writer, the
    ranges of sequence numbers it holds) and pulls exactly what it lacks. This path alone
    guarantees convergence, after crashes, restarts, partitions or a Redis outage.
- **Ordering**: each message carries a vector clock. Rooms are sorted by the sum of the clock,
  then timestamp, sender and id. That order respects causality (if A happened before B, A's sum
  is smaller) and is identical on every node, whatever each machine's clock says.
- **Writers**: clocks are keyed by a writer id created with each database (`alice/7f3a9c1e`), not
  by the username, so a node that lost its data can never reuse a sequence number.

Trust model: users and outsiders are not trusted (passwords hashed with scrypt, a random token
per node, input limits); nodes trust each other, so crash faults are tolerated, Byzantine ones are not.

## Running it

Requirements: Docker with Compose, and port 80 free. The Traefik dashboard is at
<http://localhost:8080>, reachable from this machine only.
`*.localhost` resolves to your machine in browsers and curl, so no `/etc/hosts` changes are needed.

```bash
./scripts/local_deploy.sh        # same as: docker compose up -d --build
```

Open <http://localhost>, choose a username and a password (at least 8 characters), and you land
on `http://<username>.localhost` once the node is up. Cookies are per subdomain, so several users
can be logged in from tabs of the same browser.

- **Logout** (in the chat) ends the session in that browser only; the node keeps running.
- **Stop node** (on the landing page, with the password) removes the user's container. The data
  volume `flc_data_<user>` is kept, and the next login picks up where it left off.

Settings: `MAX_USERS` (default 10) caps the running nodes: `MAX_USERS=3 docker compose up -d`.
Logs: `docker compose logs -f` for the stack, `docker logs -f flc-node-<user>` for a node.

### Two users from the terminal

```bash
login() {  # login <user> <password>: claims the account, starts the node, stores the session cookie
  link=$(curl -s -d "username=$1" -d "password=$2" http://localhost/login \
         | grep -o "http://$1.localhost/api/auth?token=[A-Za-z0-9_-]*" | head -1)
  until curl -s "http://localhost/status/$1" | grep -q true; do sleep 1; done
  curl -s -o /dev/null -c "$1.jar" "$link"
}
login alice alice-password
login bob bob-password

curl -s -b alice.jar -H 'Content-Type: application/json' \
     -d '{"room_id": "general", "content": "hi from alice"}' http://alice.localhost/api/messages
curl -s -b bob.jar 'http://bob.localhost/api/messages?room_id=general'
```

### Stopping and cleaning up

```bash
docker compose down                                                  # gateway, Redis, orchestrator
docker ps -aq --filter label=app=flc-node | xargs -r docker rm -f    # user nodes
docker volume ls -q --filter name=flc_data_ | xargs -r docker volume rm   # all chat data (irreversible)
```

## Deploying

FLC needs a machine where it can run Docker itself: the orchestrator starts a container per
user, so app platforms without a Docker socket won't do. Any Linux VM works, e.g. a free
[Oracle Cloud](https://www.oracle.com/cloud/free/) ARM instance. No domain is needed:
[nip.io](https://nip.io) resolves `alice.203.0.113.7.nip.io` to `203.0.113.7`.

1. Open ports 80 and 443 to the internet, both in the provider's firewall (on Oracle, the
   subnet's security list) and on the VM itself if its image ships with a restrictive one.
2. Install Docker with Compose, clone the repository, and start the stack with HTTPS:

   ```bash
   export PUBLIC_DOMAIN=203.0.113.7.nip.io ACME_EMAIL=you@example.org   # your VM's public IP
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   ```

3. Share `https://203.0.113.7.nip.io`. Each user's node gets its own Let's Encrypt certificate
   the first time it starts, and the loading page waits until that certificate is valid.

`docker-compose.prod.yml` redirects HTTP to HTTPS, requests certificates with the HTTP-01
challenge (so port 80 must stay open), marks the session cookie `Secure`, and drops the
Traefik dashboard. To rehearse without touching Let's Encrypt's rate limits, add
`ACME_CA_SERVER=https://acme-staging-v02.api.letsencrypt.org/directory` (browsers won't trust
those certificates), then `docker volume rm flc_letsencrypt` before the real run.

## Development

```bash
poetry install --with dev
./scripts/check.sh      # black, isort, mypy, flake8, bandit, pytest with coverage >= 80%
./scripts/e2e.sh        # end-to-end suite against the real stack
```

The end-to-end suite starts the stack and checks one guarantee per test: replicas converge past
100 messages; every replica shows the same causal order; a restarted node has caught up before
it serves; both sides of a network partition keep accepting messages and agree once it heals;
messages still replicate while Redis is stopped; nodes refuse requests without the right
session. It is skipped unless `FLC_E2E=1` is set, which `e2e.sh` does.

CI runs the same gates and the end-to-end suite on every pull request; pushes to `main` publish
the image to Docker Hub after the same checks.

## Project structure

```
src/
├── main.py               # node app: chat UI, REST and WebSocket API
├── orchestrator_api.py   # orchestrator app: landing page, login, stop
├── api/routes.py         # node endpoints, session checks, internal sync endpoint
├── core/                 # Message model, vector clocks, in-memory node state
├── services/
│   ├── node.py           # a node's lifecycle: storage, clocks, replication tasks
│   ├── gossip.py         # discovery, pub/sub fast path, pull anti-entropy
│   ├── storage.py        # SQLite replica, causal order, summaries
│   ├── orchestrator.py   # Docker: spawn, stop, readiness through the gateway
│   ├── accounts.py       # scrypt password store
│   └── websocket.py      # live updates to open chat tabs
├── static/               # chat front end (Tailwind, marked, DOMPurify, highlight.js)
└── templates/            # orchestrator pages
tests/                    # unit tests, plus test_e2e.py for the real stack
Dockerfile                # one image for nodes and orchestrator
docker-compose.yml        # gateway, Redis, orchestrator
```

## Known limits

- HTTPS only with `docker-compose.prod.yml`: the local setup is plain HTTP.
- Login has no rate limit: scrypt slows guessing down, but nothing locks an account.
- Every node stores every room, and each anti-entropy round visits the whole log: right for a
  handful of users and tens of thousands of messages, not beyond.
- Rooms are open to every user.
