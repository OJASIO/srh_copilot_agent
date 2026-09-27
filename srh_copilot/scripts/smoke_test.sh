#!/usr/bin/env bash
# Quick end-to-end check against a running API.
set -e
API=${COPILOT_API_URL:-http://localhost:8000}
KEY=${API_KEY:-change-me}
echo "health:"; curl -s $API/health; echo
echo "agents:"; curl -s -H "X-API-Key: $KEY" $API/agents; echo
echo "chat:"; curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"message":"Which scholarships exist for international students?","agent_id":"student_service","task_id":"scholarship_info"}' \
  $API/chat; echo
