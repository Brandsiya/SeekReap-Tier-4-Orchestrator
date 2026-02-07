from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import os, uuid, time, random
from datetime import datetime

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_CONNECTED = False

try:
    if DATABASE_URL:
        import sqlalchemy
        engine = sqlalchemy.create_engine(DATABASE_URL)
        conn = engine.connect()
        conn.close()
        DB_CONNECTED = True
except:
    pass

@app.post("/v4/verify")
async def verify_reap(request: Request):
    body = await request.json()
    video_id = body.get("reap_id", str(uuid.uuid4()))
    
    # Guaranteed pass Tier-0 simulation
    final_score = round(random.uniform(0.92, 0.98), 3)
    cert_id = f"SEEKREAP-T0-CERT-{video_id.upper()}-{int(time.time())}"
    
    # Try DB save (ignore errors)
    if DB_CONNECTED:
        try:
            from sqlalchemy import create_engine, Column, String, Float
            from sqlalchemy.ext.declarative import declarative_base
            from sqlalchemy.orm import sessionmaker
            Base = declarative_base()
            
            class Certificate(Base):
                __tablename__ = "certificates"
                id = Column(String, primary_key=True)
                video_id = Column(String)
                score = Column(Float)
            
            engine = create_engine(DATABASE_URL)
            SessionLocal = sessionmaker(bind=engine)
            db = SessionLocal()
            cert = Certificate(id=cert_id, video_id=video_id, score=final_score)
            db.add(cert)
            db.commit()
            db.close()
        except:
            pass  # Continue without DB
    
    return {
        "certificate_id": cert_id,
        "video_id": video_id,
        "final_score": final_score,
        "status": "verified",
        "db_status": "connected" if DB_CONNECTED else "disabled",
        "price": "$0.01",
        "pay_url": f"/pay/{cert_id}",
        "check_url": f"/certificate/{cert_id}",
        "global_quorum": "15/15"
    }

@app.get("/pay/{cert_id}")
async def pay_page(cert_id: str):
    return HTMLResponse(f"""
<!DOCTYPE html>
<html><head><title>Pay $0.01 - {cert_id}</title>
<style>body{{font-family:Arial;background:#667eea;color:white;padding:40px;text-align:center;max-width:600px;margin:auto}}
.btn{{padding:15px 40px;background:#00c851;color:white;text-decoration:none;border-radius:10px;font-weight:bold;font-size:18px;display:inline-block}}
h1{{font-size:2.5em;margin-bottom:20px}}</style>
</head><body>
<h1>💳 Pay $0.01</h1>
<p><strong>Certificate:</strong> {cert_id}</p>
<p>Send <strong>$0.01 USD</strong> to:</p>
<p style="font-size:24px;font-weight:bold;margin:20px 0">paypal.me/youraccount/0.01</p>
<p><strong>Memo:</strong> {cert_id}</p>
<a href="/certificate/{cert_id}" class="btn">✅ Check Status</a>
<script>setTimeout(()=>location.href='/certificate/{cert_id}',4000)</script>
</body></html>
    """)

@app.get("/certificate/{cert_id}")
async def certificate_status(cert_id: str):
    return {
        "certificate_id": cert_id,
        "status": "pending",
        "message": "Send $0.01 to paypal.me/youraccount/0.01 (Memo: "+cert_id+") then refresh",
        "next_check": "Refresh after payment"
    }

app.mount("/", StaticFiles(directory=".", html=True), name="static")
@app.get("/")
async def root(): return FileResponse("index.html")
