# 🚍 Entebus Server

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-0db7ed?logo=docker)](https://www.docker.com/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-ready-326ce5?logo=kubernetes)](https://kubernetes.io/)

The **Entebus Server** is a high-performance API server built with [FastAPI](https://fastapi.tiangolo.com/). It provides the backbone for managing transport-related data and services. Designed for **containerized environments** (Docker + Kubernetes), it ensures scalability, resilience, and modern developer experience.

## ✨ Features

- ⚡ **High-performance** API with FastAPI + Uvicorn  
- 🐘 **PostGIS support** for geospatial data  
- 📦 **MinIO integration** for object storage  
- 📊 **OpenObserve** for logs, metrics & traces  
- ⚡ **Redis support** for caching & queues  
- 🐳 Ready-to-use **Docker image** with CI-friendly tags  
- ☸️ Deployment ready for **Kubernetes**  

## 🛠️ Getting Started

### Prerequisites

- Ubuntu 22.04+ (WSL2 supported)  
- Python 3.12+  
- Docker
- Kubernetes (optional, for cluster deployment)  

### Local Development Setup

```bash
sudo apt-get update
sudo apt-get upgrade
sudo apt autoremove

# Install the libraries and tools
sudo apt-get install build-essential
sudo apt-get install libpq-dev
sudo apt install python3-pip
sudo apt-get install python3-venv

# Create a virtual environment and install required libraries
python3 -m venv .venv
source .venv/bin/activate
pip install pip-tools
pip install -r requirements.txt

# Generating a locked requirements.txt with pinned versions
pip-compile requirements.in --output-file requirements.txt

# Upgrading packages
pip-compile --upgrade requirements.in --no-strip-extras
```

### VS Code (plugins)

* Code Spell Checker (streetsidesoftware.code-spell-checker)
* GitLens (eamodio.gitlens)
* Python (ms-python.python)
* Black Formatter (ms-python.black-formatter)
* autoDocstring (njpwerner.autodocstring)

### Dependencies via Docker

**PostgreSQL + PostGIS**

```bash
docker run --name postgis \
    -e POSTGRES_PASSWORD=password \
    -p 5432:5432 \
    -d postgis/postgis:18-3.6-alpine
```

**MinIO (object storage)**

```bash
docker run --name minio \
    -e MINIO_ROOT_USER=minio \
    -e MINIO_ROOT_PASSWORD=password \
    -p 9000:9000 \
    -p 9001:9001 \
    -d minio/minio:latest server /data --console-address ":9001"
```

**OpenObserve (logs, traces, metrics)**

```bash
docker run -d \
    --name openobserve \
    -p 5080:5080 \
    -e ZO_ROOT_USER_EMAIL="admin@entebus.com" \
    -e ZO_ROOT_USER_PASSWORD="password" \
    openobserve/openobserve:v0.80.3
```

**Redis DB**

```bash
docker run --name redis \
    -p 6379:6379 \
    -d redis:8.6.3-alpine \
    redis-server --requirepass "password" --save "" --appendonly no
```


## 🗄️ Database Setup & Management

All database migrations and schema management are handled via **Alembic** and the helper script `setup.py`. This script provides commands for revising, migrating, resetting, and managing DB tables and MinIO buckets.

### Usage

Run the following commands from the project root:

```bash
python -m app.setup tables revise "added new table" # Create a new migration (revision) from model changes
python -m app.setup tables migrate                  # Apply migrations (bring DB schema to latest head)

# Reset the database (drop + recreate schema)
# Note: Reinstall PostGIS extension when you do so
python -m app.setup tables reset

# Downgrade the database by one step (use -N for multiple steps)
python -m app.setup tables downgrade
python -m app.setup tables downgrade -2

# Note: Use with caution (not for production/regular use)
python -m app.setup tables create                   # Create all tables directly (without migrations)
python -m app.setup tables delete                   # Delete all tables (without migrations)
python -m app.setup tables init                     # Initialize the database with default data

python -m app.setup buckets create                  # Create all MinIO buckets (defined in app/src/buckets.py)
python -m app.setup buckets delete                  # Delete all MinIO buckets

python -m tests.setup test api                      # To run the tests (Make sure the server is running)
```


## 🚀 Running the Server

**Starting server**

The preferred server to run the FastAPI application is Uvicorn.
```bash
# Run with Uvicorn (hot reload enabled)
uvicorn app.main:app --port 8080 --reload
```
You can access the API from http://127.0.0.1:8080/docs.


## 🐳 Docker Image

**Docker Image**

Build, run, and push the image:
The image is tagged using the format: `<branch-name>-<commit-id>` (for latest image you may add optional tag `<branch-name>-latest`).
```bash
# Building the docker image
docker build -t <registry>/<namespace>/entebus-server:<branch>-<commit-id> \
             -t <registry>/<namespace>/entebus-server:<branch>-latest .

# Running the docker image
docker run -d --name <container-name> -p <host-port>:<container-port> \
    <registry>/<namespace>/entebus-server:<branch>-latest

# Login to remote docker repository (only needed once)
docker login <registry>

# Push the docker image to nexus repository
docker push <registry>/<namespace>/entebus-server:<branch>-latest
docker push <registry>/<namespace>/entebus-server:<branch>-<commit-id>

# Pull the docker image from nexus repository
docker pull <registry>/<namespace>/entebus-server:<branch>-<commit-id>
```

## 🧹 Code Formatting & CI

This project enforces Python code formatting using [Black](https://black.readthedocs.io/).

- All code must be formatted with Black before merging.
- A GitHub Actions workflow automatically checks formatting on every pull request.
- If any file is not properly formatted, the PR will fail the check and cannot be merged until fixed.

**How to check and fix formatting locally (without Black Formatter extension in VS Code):**

```bash
# Install black (once)
pip install black==25.1.0

# Check formatting (shows files that need changes)
black --check .

# Auto-format all files in place
black .
```

**CI Workflow:**
- The workflow runs on every PR and uses `black==25.1.0` (the same version shown in the local install example above).
- To pass the check, ensure you run Black 25.1.0 locally (via CLI or your editor/VS Code `ms-python.black-formatter` extension configured to use this version) before pushing your changes.

See `.github/workflows/black-format.yaml` for details.

## 🤝 Contributing

Contributions are welcome! 🚀
Please check out our [Contributing Guide](CONTRIBUTING.md) for guidelines on setting up your dev environment, coding standards, and submitting PRs. This project follows a [Code of Conduct](CODE_OF_CONDUCT.md) to ensure a welcoming community for everyone.

## 📜 License

This project is licensed under the [MIT License](LICENSE).
Feel free to use, modify, and distribute under the terms of the license.

## 📧 Contact

Developed with ❤️ by Nixbug Softwares OPC Pvt Ltd (contact@nixbug.com).
For issues or feature requests, please open a GitHub issue.
