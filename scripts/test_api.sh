#!/bin/bash
# A simple E2E test script using curl.
# Requires the server to be running: poetry run uvicorn src.main:app --reload

BASE_URL="http://localhost:8000/api"
USERNAME="alice"

echo "=========================================="
echo "   FLC API Test Script"
echo "=========================================="

# 1. Check Health (Should be uninitialized initially)
echo -e "\n1. Checking Health..."
curl -s "$BASE_URL/health" | python3 -m json.tool

# 2. Login
echo -e "\n2. Logging in as '$USERNAME'..."
# Note: In a real app, you'd save the token. Here, login initializes the node stateful-ly.
curl -X POST "$BASE_URL/login" \
     -H "Content-Type: application/json" \
     -d "{\"username\": \"$USERNAME\", \"password\": \"dummy\"}" | python3 -m json.tool

# 3. Check Health (Should be initialized now)
echo -e "\n3. Checking Health (Post-Login)..."
curl -s "$BASE_URL/health" | python3 -m json.tool

# 4. Send a Message
echo -e "\n4. Sending a message..."
curl -X POST "$BASE_URL/messages" \
     -H "Content-Type: application/json" \
     -d '{"content": "Hello from curl!", "room_id": "general"}' | python3 -m json.tool

# 5. Get Messages
echo -e "\n5. Retrieving messages..."
curl -s "$BASE_URL/messages?room_id=general&limit=5" | python3 -m json.tool

echo -e "\n=========================================="
echo "   Test Complete"
echo "=========================================="
