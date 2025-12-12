#!/bin/bash
echo "🔍 Checking Node 0 Database State..."

# 1. Check if DB file exists and has size
echo "📂 File System Check:"
docker exec flc-node-0 ls -lh /data/

echo "Installing sqlite3..."
docker exec flc-node-0 apt-get update && apt-get install sqlite3 -y
# 2. Check row count in Messages table
echo "📊 Row Count in 'messages' table:"
docker exec flc-node-0 sqlite3 /data/user_0.db "SELECT count(*) FROM messages;"

# 3. Dump the last 5 messages (ID and Content)
echo "📝 Last 5 Messages:"
docker exec flc-node-0 sqlite3 /data/user_0.db "SELECT message_id, room_id, content FROM messages ORDER BY created_at DESC LIMIT 5;"
