import os
import uuid
import httpx
from datetime import datetime
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn

# Environment variables
PORT = int(os.getenv("PORT", 10000))
TIER3_URL = os.getenv("TIER3_URL", "https://seekreap-tier-3-private.onrender.com")

# FastAPI app
app = FastAPI(title="SeekReap Tier-4 Orchestrator", version="2.0.0")

# Models
class Envelope(BaseModel):
    id: str
    timestamp: float
    payload: Dict[str, Any]
    schema_version: str
    orchestration_policy: str
    signature: str
    metadata: Optional[Dict[str, Any]] = None

class BatchEnvelope(BaseModel):
    envelopes: List[Envelope]

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    # Check connection to Tier-3
    tier3_status = "unknown"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{TIER3_URL}/health", timeout=5.0)
            if response.status_code == 200:
                tier3_status = "healthy"
    except:
        tier3_status = "unreachable"
    
    return {
        "status": "healthy",
        "tier": 4,
        "timestamp": datetime.now().isoformat(),
        "tier3_url": TIER3_URL,
        "tier3_status": tier3_status,
        "version": "2.0.0"
    }

# Debug endpoints
@app.get("/debug/tier3-url")
async def debug_tier3_url():
    """Debug endpoint to check TIER3_URL value"""
    return {
        "TIER3_URL_env": os.getenv("TIER3_URL", "NOT SET"),
        "TIER3_URL_var": TIER3_URL
    }

@app.get("/debug/test-tier3-connection")
async def debug_tier3_connection():
    """Test connection to Tier-3"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{TIER3_URL}/health", timeout=5.0)
            return {
                "status": "connected",
                "tier3_status_code": response.status_code,
                "tier3_response": response.text
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__
        }

@app.get("/debug/routes")
async def list_routes():
    """List all registered routes"""
    routes = []
    for route in app.routes:
        routes.append({
            "path": route.path,
            "name": route.name,
            "methods": list(route.methods) if hasattr(route, 'methods') else []
        })
    return {"routes": routes}

# Job endpoints - SINGLE CORRECT DEFINITION
@app.get("/api/submissions/{job_id}")
async def get_submission(job_id: int):
    """Get specific job from Tier-3 using their /api/job/{job_id} endpoint"""
    try:
        print(f"Fetching job {job_id} from {TIER3_URL}/api/job/{job_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{TIER3_URL}/api/job/{job_id}", timeout=10.0)
            
            print(f"Response status: {response.status_code}")
            print(f"Response body: {response.text}")
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text
                }, response.status_code
                
    except httpx.ConnectError as e:
        print(f"Connection error to Tier-3: {e}")
        return {"error": f"Cannot connect to Tier-3: {str(e)}"}, 503
    except httpx.TimeoutException as e:
        print(f"Timeout connecting to Tier-3: {e}")
        return {"error": "Timeout connecting to Tier-3"}, 504
    except Exception as e:
        print(f"Unexpected error: {e}")
        return {"error": f"Internal error: {str(e)}"}, 500

@app.post("/api/submit")
async def submit_job(request: Request):
    """Submit job to Tier-3"""
    try:
        data = await request.json()
        
        async with httpx.AsyncClient() as client:
            # Use the process-envelope endpoint
            response = await client.post(f"{TIER3_URL}/process-envelope", json={
                "id": f"job-{data.get('job_id', int(datetime.now().timestamp()))}",
                "timestamp": datetime.now().timestamp(),
                "payload": data,
                "schema_version": "1.0",
                "orchestration_policy": "standard",
                "signature": "tier4-signature"
            }, timeout=10.0)
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text
                }, response.status_code
                
    except Exception as e:
        print(f"Error submitting job: {e}")
        return {"error": str(e)}, 500

# Process envelope endpoints (original functionality)
@app.post("/process-envelope")
async def process_envelope(envelope: Envelope):
    """Process a single envelope by forwarding to Tier-3"""
    print(f"Tier-4 received envelope: {envelope.id}")
    print(f"Processing envelope: {envelope.id}")
    print(f"Policy: {envelope.orchestration_policy}")
    
    try:
        # Forward to Tier-3
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{TIER3_URL}/process-envelope", json=envelope.dict(), timeout=10.0)
            return response.json()
    except Exception as e:
        print(f"Error forwarding to Tier-3: {str(e)}")
        return {
            "decision": "ERROR",
            "confidence": 0,
            "risk_factors": [f"Error: {str(e)}"],
            "appeal_text": None,
            "job_id": envelope.payload.get("job_id")
        }

@app.post("/process-batch")
async def process_batch(batch: BatchEnvelope):
    """Process multiple envelopes in batch"""
    print(f"Tier-4 received batch of {len(batch.envelopes)} envelopes")
    
    results = []
    for envelope in batch.envelopes:
        result = await process_envelope(envelope)
        results.append(result)
    
    return {"results": results}

@app.post("/test-envelope")
async def test_envelope(request: Request):
    """Create a test envelope for development"""
    data = await request.json()
    
    # Create a test envelope
    test_envelope = {
        "id": f"test-{datetime.now().timestamp()}",
        "timestamp": datetime.now().timestamp(),
        "payload": {
            "job_id": data.get("job_id", 999),
            "metadata": {
                "source": "tier4_test",
                "test": True
            }
        },
        "schema_version": "1.0",
        "orchestration_policy": "standard",
        "signature": "tier4-test-signature",
        "metadata": {"test": True}
    }
    
    # Process it
    return await process_envelope(Envelope(**test_envelope))

# Root endpoint
@app.get("/")
async def root():
    return {
        "status": "Tier-4 is running",
        "version": "2.0.0",
        "endpoints": [
            "/health",
            "/process-envelope",
            "/process-batch",
            "/test-envelope",
            "/api/submissions/{job_id}",
            "/api/submit",
            "/debug/tier3-url",
            "/debug/test-tier3-connection",
            "/debug/routes",
            "/docs"
        ]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)

# Improved submit endpoint with correct envelope format
@app.post("/api/submit-v2")
async def submit_job_v2(request: Request):
    """Submit job to Tier-3 with correct envelope format"""
    try:
        data = await request.json()
        
        # Create a proper envelope based on Tier-3's schema
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": {
                "creator_id": data.get("creator_id", 1),
                "content": {
                    "url": data.get("url", ""),
                    "type": data.get("job_type", "url")
                },
                "metadata": {
                    "source": "tier4-frontend",
                    "timestamp": datetime.now().isoformat()
                }
            },
            "schema_version": "1.0",
            "orchestration_policy": data.get("policy", "standard"),
            "signature": "tier4-signature",
            "metadata": {
                "client": "web",
                "request_id": str(uuid.uuid4())
            }
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TIER3_URL}/process-envelope", 
                json=envelope, 
                timeout=10.0
            )
            
            print(f"Submit response: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text,
                    "envelope_sent": envelope  # For debugging
                }, response.status_code
                
    except Exception as e:
        print(f"Error in submit: {e}")
        return {"error": str(e)}, 500

# Minimal working envelope based on OpenAPI spec
@app.post("/api/submit-v3")
async def submit_job_v3(request: Request):
    """Submit job with minimal correct envelope format"""
    try:
        data = await request.json()
        
        # Create a SIMPLE envelope - just what's required
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": {
                "url": data.get("url", ""),
                "creator_id": data.get("creator_id", 1),
                "job_type": data.get("job_type", "url")
            },
            "schema_version": "1.0",
            "orchestration_policy": "standard",
            "signature": "tier4-signature",
            "metadata": {}  # Empty object, not null
        }
        
        print(f"Sending envelope: {envelope}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TIER3_URL}/process-envelope",
                json=envelope,
                timeout=10.0
            )
            
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            
            if response.status_code == 200:
                return response.json()
            else:
                # Try alternative endpoint
                alt_response = await client.post(
                    f"{TIER3_URL}/api/process-submission",
                    json=payload,
                    timeout=10.0
                )
                return {
                    "primary_error": response.text,
                    "alt_status": alt_response.status_code,
                    "alt_response": alt_response.text
                }, 500
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500
