#!/bin/bash
set -e

echo "🚀 Starting Local Deployment..."

# 1. Stop any currently running containers to avoid conflicts
echo "🛑 Stopping existing containers..."
docker-compose down

# 2. Build Docker Image
echo "🔨 Building Docker image..."
docker-compose build

# 3. Start Cluster
echo "🔥 Spawning Cluster..."
docker-compose up -d

echo "✅ Deployment Complete!"

echo ""
echo "🌐 Access URLs:"
echo "---------------------------------------------------"

# Iterate over all running services in the current project
# We use 'docker-compose ps' to get service names, then 'docker-compose port' to find the public port
for service in $(docker-compose ps --services); do
    # Get the public port mapped to container port 8000
    # The output format of 'docker-compose port' is 0.0.0.0:8001
    full_port_info=$(docker-compose port "$service" 8000 2>/dev/null)
    
    if [ ! -z "$full_port_info" ]; then
        # Extract just the port number (e.g., 8001)
        # cut -d: -f2 gets the port part after the colon
        port=$(echo "$full_port_info" | cut -d: -f2)
        
        # Print clickable URL
        echo "$service: http://localhost:$port"
    fi
done

echo "---------------------------------------------------"
echo "📝 Logs: docker-compose logs -f"
