# 🚀 SeekReap Tier-4 Orchestrator
**Core Role:** The "Brain" of the SeekReap ecosystem. 
Coordinates between Tier-3 Workers and Tier-5 Analytics.

## 🛠 Setup & Migration
1. **Install Dependencies:** `npm install`
2. **Configuration:** Create a `.env` file (see below).
3. **Execution:** `node server.js` or `nohup node server.js > orchestrator.log 2>&1 &`

## 📡 Environment Variables
| Variable | Description |
| :--- | :--- |
| PORT | Port for the Express server (default: 10000) |
| TIER3_URL | Private URL for Tier-3 Workers |
| TIER5_URL | URL for Tier-5 Orchestrator |

## 🧪 Health Check
Verify status via: `curl http://localhost:10000/health`
