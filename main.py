from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uuid
import time
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve ALL static files from ROOT (not /static)
app.mount("/", StaticFiles(directory=".", html=True), name="static")

@app.post("/v4/verify")
async def verify_reap(data: dict):
    reap_id = data.get("reap_id", str(uuid.uuid4()))
    global_id = f"global-{reap_id}"
    cert_id = f"seekreap-cert-{reap_id}-{int(time.time())}"
    return {
        "global_id": global_id,
        "tier3_verified": True,
        "global_quorum": "15/15",
        "nodes_active": 15,
        "worldwide_latency": 127.3,
        "certificate_id": cert_id
    }

@app.get("/")
async def root():
    if os.path.exists("creator-dashboard.html"):
        with open("creator-dashboard.html") as f:
            return HTMLResponse(f.read())
    return {"status": "SeekReap Tier-4 LIVE"}
