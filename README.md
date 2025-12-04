<div align="center">

# Federated Lightweight Chat (FLC)

[![CI Pipeline](https://img.shields.io/github/actions/workflow/status/aleemont1/federated-lightweight-chat/ci.yml?branch=develop&style=for-the-badge)](https://github.com/aleemont1/federated-lightweight-chat/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![Black](https://img.shields.io/badge/black-%23000000.svg?style=for-the-badge&logo=black&logoColor=white)
![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)
![DigitalOcean](https://img.shields.io/badge/DigitalOcean-%230167ff.svg?style=for-the-badge&logo=digitalOcean&logoColor=white)
[![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge)](./LICENSE)

</div>

**Federated Lightweight Chat** is a simple distributed chat system designed to demonstrate eventual consistency and causal ordering in a decentralized environment. Built with **FastAPI** and **Python 3.11**, it leverages modern asynchronous programming patterns to handle node-to-node communication without a central authority.

## 🚀 Key Features

  * **Decentralized Architecture:** No central server; nodes communicate directly via HTTP.
  * **Eventual Consistency:** Implements a randomized **Gossip Protocol** to propagate messages across the cluster in the background, ensuring all nodes eventually synchronize.
  * **Causal Ordering:** Utilizes **Vector Clocks** to track message causality and partial ordering, ensuring chat history consistency even with network latency or partitions.
  * **Local Persistence:** Uses **SQLite** for lightweight, reliable message and peer persistence, managing database connections and schema initialization locally.
  * **Modern Python Stack:** Fully typed codebase using **Pydantic V2** for data validation and settings management.

## 🛠️ Tech Stack

<div align="center">

<table>
  <tr>
    <td><strong>Framework</strong></td>
    <td><img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi"></td>
  </tr>
  <tr>
    <td><strong>Validation</strong></td>
    <td><img src="https://img.shields.io/badge/pydantic-%23E92063.svg?style=for-the-badge&logo=pydantic&logoColor=white"></td>
  </tr>
  <tr>
    <td><strong>Storage</strong></td>
    <td><img src="https://img.shields.io/badge/sqlite-%2307405e.svg?style=for-the-badge&logo=sqlite&logoColor=white"></td>
  </tr>
  <tr>
    <td><strong>Package Manager</strong></td>
    <td><img src="https://img.shields.io/badge/Poetry-%233B82F6.svg?style=for-the-badge&logo=poetry&logoColor=0B3D8D"></td>
  </tr>
</table>

</div>


## 📂 Project Structure

```text
federated-lightweight-chat/
├── .github/workflows/   # CI/CD Pipelines (GitHub Actions)
├── scripts/             # Utility scripts (e.g., check.sh)
├── src/
│   ├── config/          # Environment configuration (Pydantic Settings)
│   ├── core/            # Domain logic (Vector Clocks, Node State, Messages)
│   └── services/        # Business logic (Storage, Gossip Protocol)
├── tests/               # Unit and Integration tests (Pytest)
├── pyproject.toml       # Dependencies and Tool Config
└── poetry.lock          # Locked dependencies
```

## ⚡ Getting Started

### Prerequisites

  * Python 3.11 or higher
  * [Poetry](https://www.google.com/search?q=https://python-poetry.org/docs/%23installation) installed

### Installation

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/aleemont1/federated-lightweight-chat.git
    cd federated-lightweight-chat
    ```

2.  **Install dependencies:**

    ```bash
    poetry install --with dev
    ```

    This command installs both runtime dependencies (FastAPI, Uvicorn) and development tools (Black, MyPy, Pytest).

### Configuration

Configuration is handled via environment variables, loaded by Pydantic Settings. You can create a `.env` file to override defaults like `SERVER_PORT` or `PEERS`.

## 🧪 Development & Quality Assurance

This project adheres to strict code quality standards, enforced by a CI pipeline.

### Local Quality Check

A convenience script `scripts/check.sh` is provided to run the full suite of checks locally. This script performs the following:

1.  **Formatting:** Runs `black` and `isort` to ensure code style consistency.
2.  **Static Analysis:** Runs `mypy` for type checking, `flake8` for linting, and `bandit` for security analysis.
3.  **Testing:** Runs `pytest` with verbose output to verify unit tests.

<!-- end list -->

```bash
# Make the script executable
chmod +x scripts/check.sh

# Run the full QA suite
./scripts/check.sh
```

### Testing

Unit tests focus on ensuring the correctness of core distributed system components like **Vector Clocks** and **Node State** logic, as well as persistence layers.

```bash
# Run tests with coverage report
poetry run pytest -vv --cov=src
```

<!---
## 🐳 Docker Support

The project includes a **Continuous Delivery (CD)** pipeline that automatically builds and pushes Docker images upon pushing to the main branch.

```bash
docker pull aleemont/flc-node:latest
```
-->
