#!/bin/bash

LOGFILE="pipeline_results.log"

# Submit a dummy YouTube video
SUB_ID=$(curl -s -X POST http://localhost:8080/api/submit \
  -H "Content-Type: application/json" \
  -d '{
    "firebase_uid": "test-user",
    "email": "unique_test_'$(date +%s)'@seekreap.io",
    "content_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "content_type": "youtube_video",
    "content_hash": "dummyhash_'$(date +%s)'",
    "creator_id": "test-user-uuid"
  }' | jq -r '.submission_id')

echo "Tracking submission: $SUB_ID"

# Finalize (simulate worker completion + Tier-3 scoring)
FINALIZE=$(curl -s -X POST http://localhost:8080/api/finalize \
  -H "Content-Type: application/json" \
  -d '{"submission_id":"'"$SUB_ID"'"}' | jq .)

echo "Finalize response: $FINALIZE"

# Poll status until COMPLETED
while true; do
  STATUS=$(curl -s http://localhost:8080/api/status/$SUB_ID | jq -r '.status')
  RISK_SCORE=$(curl -s http://localhost:8080/api/status/$SUB_ID | jq -r '.risk_score')
  RISK_LEVEL=$(curl -s http://localhost:8080/api/status/$SUB_ID | jq -r '.risk_level')
  echo "Status: $STATUS | Risk: $RISK_SCORE $RISK_LEVEL"

  if [ "$STATUS" = "COMPLETED" ]; then
    TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
    echo "$TIMESTAMP | Submission: $SUB_ID | Risk: $RISK_SCORE $RISK_LEVEL" >> $LOGFILE
    break
  fi
  sleep 30
done
