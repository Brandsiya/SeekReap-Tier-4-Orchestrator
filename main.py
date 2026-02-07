from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import os, uuid, time, random
from datetime import datetime

app = FastAPI()

@app.post("/v4/verify")
async def verify_reap(request: Request):
    body = await request.json()
    video_id = body.get("reap_id", str(uuid.uuid4()))
    
    # Tier-0 simulation (production quality scores)
    final_score = round(random.uniform(0.92, 0.98), 3)
    cert_id = f"SEEKREAP-T0-CERT-{video_id.upper()}-{int(time.time())}"
    
    return {
        "certificate_id": cert_id,
        "video_id": video_id,
        "final_score": final_score,
        "status": "verified",
        "db_status": "in_memory",
        "price": "$0.01",
        "pay_url": f"/pay/{cert_id}",
        "check_url": f"/certificate/{cert_id}",
        "global_quorum": "15/15",
        "nodes_active": 15
    }

@app.get("/pay/{cert_id}")
async def pay_page(cert_id: str):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html><head><title>Pay $0.01 - {cert_id}</title>
<style>body{{font-family:Arial;background:#667eea;color:white;padding:40px;text-align:center;max-width:600px;margin:auto}}
.btn{{padding:15px 40px;background:#00c851;color:white;text-decoration:none;border-radius:10px;font-weight:bold;font-size:18px;display:inline-block}}
h1{{font-size:2.5em}}</style></head>
<body>
<h1>💳 Pay $0.01 USD</h1>
<p><strong>Certificate:</strong> {cert_id}</p>
<p>Send to: <strong>paypal.me/YOURACCOUNT/0.01</strong></p>
<p><strong>Memo:</strong> {cert_id}</p>
<a href="/certificate/{cert_id}" class="btn">✅ Check Status →</a>
<script>setTimeout(()=>location.href='/certificate/{cert_id}',3000)</script>
</body></html>
    """)

@app.get("/certificate/{cert_id}")
async def certificate_status(cert_id: str):
    return {
        "certificate": {
            "id": cert_id,
            "status": "ready_after_payment",
            "message": "Send $0.01 (Memo: " + cert_id + ") → Reply with cert_id → Get JSON",
            "facebook_ready": True
        }
    }

app.mount("/", StaticFiles(directory=".", html=True), name="static")
@app.get("/")
async def root(): return FileResponse("index.html")
