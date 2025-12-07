#!/bin/bash
set -e

echo "🚀 Starting Local Deployment..."

# 1. Create data directories for persistence
echo "📂 Setting up data volumes..."
mkdir -p docker_data/alice
mkdir -p docker_data/bob
chmod 777 docker_data/alice
chmod 777 docker_data/bob

# 2. Build Docker Image
echo "🔨 Building Docker image..."
docker-compose build

# 3. Start Cluster
echo "🔥 Spawning Cluster (Alice & Bob)..."
docker-compose up -d

echo "✅ Deployment Complete!"
echo "   Alice: http://localhost:8000/docs"
echo "   Bob:   http://localhost:8001/docs"
echo ""
echo "📝 Logs: docker-compose logs -f"
