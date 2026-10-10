<div align="center">

# Federated Lightweight Chat (FLC)

[![CI Pipeline](https://img.shields.io/github/actions/workflow/status/aleemont1/federated-lightweight-chat/ci.yml?branch=develop&style=for-the-badge)](https://github.com/aleemont1/federated-lightweight-chat/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![Black](https://img.shields.io/badge/black-%23000000.svg?style=for-the-badge&logo=black&logoColor=white)
![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)
![DigitalOcean](https://img.shields.io/badge/DigitalOcean-%230167ff.svg?style=for-the-badge&logo=digitalOcean&logoColor=white)
[![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge)](./LICENSE)

</div>

Federated Lightweight Chat (FLC) is a distributed, container-native messaging system designed to demonstrate Eventual Consistency, Vector Clocks, and Dynamic Orchestration.

Unlike traditional chat applications, FLC assigns a dedicated, isolated Docker container to each user. These nodes communicate peer-to-peer using a Gossip Protocol, while a central Orchestrator handles lifecycle management and an Edge Router (Traefik) handles dynamic domain routing.

## 🚀 Key Features

Dynamic User Provisioning: The Orchestrator service listens on the Docker Socket to automatically spawn new containers when users log in.

Dynamic Edge Routing: Traefik acts as a reverse proxy, automatically discovering new user containers and routing traffic to subdomains (e.g., alice.localhost, bob.localhost) without restarts.

Decentralized Messaging: Messages are stored locally in each node's SQLite database. There is no central message database.

Eventual Consistency: Implements a randomized Gossip Protocol and Anti-Entropy mechanisms to synchronize messages between nodes.

Causal Ordering: Utilizes Vector Clocks to track message causality, ensuring consistent history ordering even across network partitions.

Self-Healing: Nodes automatically recover missing messages from peers upon restart using the discovery service.

## 🛠️ Tech Stack

<div align="center">

<table>
<tr>
<td><strong>Framework</strong></td>
<td><img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi"></td>
</tr>
<tr>
<td><strong>Orchestration</strong></td>
<td><img src="https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white"></td>
</tr>
<tr>
<td><strong>Routing</strong></td>
<td><img src="https://img.shields.io/badge/Traefik-%252324a1c1.svg?style=for-the-badge&logo=traefik&logoColor=white"></td>
</tr>
<tr>
<td><strong>Discovery</strong></td>
<td><img src="https://img.shields.io/badge/Redis-%23DD0031.svg?style=for-the-badge&logo=redis&logoColor=white"></td>
</tr>
<tr>
<td><strong>Storage</strong></td>
<td><img src="https://img.shields.io/badge/sqlite-%2307405e.svg?style=for-the-badge&logo=sqlite&logoColor=white"></td>
</tr>
<tr>
<td><strong>Validation</strong></td>
<td><img src="https://img.shields.io/badge/pydantic-%23E92063.svg?style=for-the-badge&logo=pydantic&logoColor=white"></td>
</tr>
</table>

</div>

## 📦 Architecture Overview

The system consists of three main infrastructure layers:

Traefik (Edge Router): The entry point. It routes localhost to the Orchestrator and *.localhost to specific user containers.

Orchestrator: A control plane that manages the Docker lifecycle. It spawns user nodes and provides a "Loading" UI while containers initialize.

Redis: Acts as a discovery directory for nodes to find each other's internal IPs within the bridge network.

## 📂 Project Structure

```
federated-lightweight-chat/
├── src/
│   ├── api/                    # FastAPI Routes for User Nodes
│   ├── config/                 # Pydantic Settings
│   ├── core/                   # Domain logic (Vector Clocks, Messages)
│   ├── services/               # Logic (Orchestrator, Gossip, Storage)
│   ├── static/                 # Frontend assets (JS, CSS)
│   ├── main.py                 # Entry point for User Nodes
│   └── orchestrator_api.py     # Entry point for the Orchestrator
├── scripts/                    # Utility scripts (Deployment & Testing)
├── tests/                      # Unit and Integration tests
├── docker-compose.yml          # Infrastructure definition
└── Dockerfile                  # One image: user nodes and the orchestrator
```

## ⚡ Getting Started

Prerequisites

Docker and Docker Compose installed.

Ports 80 (Gateway), 8080 (Traefik Dashboard), and 6379 (Redis) must be available.

### 🚀 Automated Local Deployment (for Development)

To streamline the startup process, use the provided helper script scripts/local_deploy.sh. It handles image building and container orchestration in one step:
```bash
./scripts/local_deploy.sh
```

### Manual Deployment

1. Build the images:
```bash
docker-compose build
```

2. Start the infrastructure:
```bash
docker-compose up -d
```

3. Access the application:

Open http://localhost to access the Orchestrator.

Login with a username (e.g., alice). The system will spawn a container and redirect you to http://<username>.localhost.

## 🧪 Development & Quality Assurance

This project adheres to strict code quality standards. We provide utility scripts to run tests and linters easily.

### Local Quality Check

To run the full quality assurance suite (formatting, static analysis, and unit tests), use the check.sh script:

```bash
./scripts/check.sh
```

This script performs:

- **Formatting**: black and isort.
- **Static Analysis**: mypy (types) and flake8 (lint).
- **Testing**: Runs pytest with coverage reports.



### End-to-end Tests

The distributed guarantees are tested against the real stack (containers, Redis, network cuts):

```bash
./scripts/e2e.sh
```

It builds and starts the stack, then checks that replicas converge past 100 messages, that every
replica shows the same causal order, that a restarted node catches up before serving, that a
network partition heals, that messages still replicate while Redis is down, and that nodes refuse
requests without the right session. Without `FLC_E2E=1` these tests are skipped.
