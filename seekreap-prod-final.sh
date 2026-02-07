#!/bin/bash
echo "🚀 SeekReap Global Production → Starting..."
pkill -f main.py 2>/dev/null || true
sleep 3

cd ~/SeekReap-Tier-2-Semantic && nohup python3 main.py > tier2.log 2>&1 &
sleep 1
cd ~/SeekReap-Tier-3-Private && nohup python3 main.py > tier3.log 2>&1 &
sleep 1
cd ~/SeekReap-Tier-4-Orchestrator && nohup python3 main.py > tier4.log 2>&1 &

sleep 5
echo "✅ PRODUCTION ENDPOINTS:"
echo "  Tier-2: http://localhost:8000"
echo "  Tier-3: http://localhost:9000" 
echo "  Tier-4: http://localhost:10000"
curl -s http://localhost:10000/v4/health || echo "  Tier-4: starting..."
echo "✅ GLOBAL HUMAN VERIFICATION = LIVE!"
