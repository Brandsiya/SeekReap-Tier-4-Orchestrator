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

# Fixed minimal envelope endpoint
@app.post("/api/submit-v4")
async def submit_job_v4(request: Request):
    """Submit job with minimal correct envelope format - FIXED"""
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
                # Try alternative endpoint with CORRECT variable name
                alt_response = await client.post(
                    f"{TIER3_URL}/api/process-submission", 
                    json=envelope,  # Fixed: was 'payload', now 'envelope'
                    timeout=10.0
                )
                return {
                    "primary_error": response.text,
                    "alt_status": alt_response.status_code,
                    "alt_response": alt_response.text,
                    "envelope_sent": envelope  # Debug info
                }, 500
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500

# Exact envelope format from OpenAPI spec
@app.post("/api/submit-v5")
async def submit_job_v5(request: Request):
    """Submit job with exact envelope format from OpenAPI spec"""
    try:
        data = await request.json()
        
        # Create envelope matching the spec EXACTLY
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": {
                "job_data": data  # Wrap the data in a job_data field
            },
            "schema_version": "1.0",
            "orchestration_policy": "standard",
            "signature": "tier4-signature",
            "metadata": {
                "source": "tier4",
                "timestamp": datetime.now().isoformat()
            }
        }
        
        print(f"Sending envelope: {envelope}")
        
        async with httpx.AsyncClient() as client:
            # Try both endpoints
            endpoints = [
                f"{TIER3_URL}/process-envelope",
                f"{TIER3_URL}/api/process-submission"
            ]
            
            results = {}
            for endpoint in endpoints:
                try:
                    response = await client.post(
                        endpoint,
                        json=envelope,
                        timeout=10.0
                    )
                    results[endpoint] = {
                        "status": response.status_code,
                        "body": response.text
                    }
                    
                    if response.status_code == 200:
                        return {
                            "success": True,
                            "endpoint": endpoint,
                            "data": response.json()
                        }
                except Exception as e:
                    results[endpoint] = {"error": str(e)}
            
            return {
                "success": False,
                "envelope_sent": envelope,
                "results": results
            }, 500
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500

# Try different HTTP methods for submission
@app.post("/api/submit-v6")
async def submit_job_v6(request: Request):
    """Try different approaches for submission"""
    try:
        data = await request.json()
        
        # Create a simple envelope
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": data,
            "schema_version": "1.0",
            "orchestration_policy": "standard",
            "signature": "tier4-signature"
        }
        
        results = {}
        
        async with httpx.AsyncClient() as client:
            # Try POST first
            try:
                resp = await client.post(
                    f"{TIER3_URL}/process-envelope",
                    json=envelope,
                    timeout=10.0
                )
                results['POST'] = {
                    'status': resp.status_code,
                    'body': resp.text
                }
            except Exception as e:
                results['POST'] = {'error': str(e)}
            
            # Try PUT
            try:
                resp = await client.put(
                    f"{TIER3_URL}/process-envelope",
                    json=envelope,
                    timeout=10.0
                )
                results['PUT'] = {
                    'status': resp.status_code,
                    'body': resp.text
                }
            except Exception as e:
                results['PUT'] = {'error': str(e)}
            
            # Try with query parameters
            try:
                resp = await client.get(
                    f"{TIER3_URL}/process-envelope",
                    params={'data': str(envelope)},
                    timeout=10.0
                )
                results['GET'] = {
                    'status': resp.status_code,
                    'body': resp.text
                }
            except Exception as e:
                results['GET'] = {'error': str(e)}
            
            return {
                'envelope_sent': envelope,
                'results': results
            }, 200
            
    except Exception as e:
        return {'error': str(e)}, 500

# Final working submit endpoint
@app.post("/api/submit-v7")
async def submit_job_v7(request: Request):
    """Submit job with the exact structure Tier-3 expects"""
    try:
        data = await request.json()
        
        # Create a job that matches the Tier-3 job structure
        job_payload = {
            "job_id": int(datetime.now().timestamp()),  # Generate a unique ID
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "failure_reason": None,
            "params": {
                "url": data.get("url", ""),
                "creator_id": data.get("creator_id", 1)
            },
            "job_type": data.get("job_type", "url")
        }
        
        print(f"Sending job: {job_payload}")
        
        async with httpx.AsyncClient() as client:
            # Try posting the job directly
            response = await client.post(
                f"{TIER3_URL}/process-envelope",
                json=job_payload,
                timeout=10.0
            )
            
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            
            if response.status_code == 200:
                return response.json()
            else:
                # Try wrapping it in an envelope
                envelope = {
                    "id": f"job-{job_payload['job_id']}",
                    "timestamp": datetime.now().timestamp(),
                    "payload": job_payload,
                    "schema_version": "1.0",
                    "orchestration_policy": "standard",
                    "signature": "tier4-signature",
                    "metadata": {}
                }
                
                env_response = await client.post(
                    f"{TIER3_URL}/process-envelope",
                    json=envelope,
                    timeout=10.0
                )
                
                return {
                    "direct_attempt": {
                        "status": response.status_code,
                        "body": response.text
                    },
                    "envelope_attempt": {
                        "status": env_response.status_code,
                        "body": env_response.text
                    },
                    "job_sent": job_payload,
                    "envelope_sent": envelope
                }, 200
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500

# The working endpoint - using /api/process-submission
@app.post("/api/submit-final")
async def submit_job_final(request: Request):
    """Submit job using Tier-3's dedicated submission endpoint"""
    try:
        data = await request.json()
        
        # Simple submission - no envelope needed!
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TIER3_URL}/api/process-submission",
                json=data,
                timeout=10.0
            )
            
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text,
                    "data_sent": data
                }, response.status_code
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500

# THE WORKING ENDPOINT - Uses correct envelope format
@app.post("/api/submit-working")
async def submit_job_working(request: Request):
    """Submit job using the correct envelope format that Tier-3 validates"""
    try:
        data = await request.json()
        
        # Create envelope with ALL required fields
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": {
                "job_data": data  # Put the actual data inside payload
            },
            "schema_version": "1.0",
            "orchestration_policy": "standard",
            "signature": "tier4-signature",
            "metadata": {
                "source": "tier4-frontend",
                "timestamp": datetime.now().isoformat()
            }
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
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text,
                    "envelope_sent": envelope,
                    "validation_hint": "Make sure all required fields are present: id, timestamp, payload, schema_version, orchestration_policy, signature"
                }, response.status_code
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500

# THE FINAL WORKING ENDPOINT - Matches Tier-3's expected format
@app.post("/api/submit-final-v2")
async def submit_job_final_v2(request: Request):
    """Submit job with the exact format Tier-3 expects"""
    try:
        data = await request.json()
        
        # Create envelope with job data at TOP level, not wrapped
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": {
                "job_id": int(datetime.now().timestamp()),  # Generate a unique ID
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "completed_at": None,
                "failure_reason": None,
                "params": {
                    "url": data.get("url", ""),
                    "creator_id": data.get("creator_id", 1)
                },
                "job_type": data.get("job_type", "url")
            },
            "schema_version": "1.0",
            "orchestration_policy": "standard",
            "signature": "tier4-signature",
            "metadata": {
                "source": "tier4-frontend",
                "timestamp": datetime.now().isoformat()
            }
        }
        
        print(f"Sending final envelope: {envelope}")
        
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
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text,
                    "envelope_sent": envelope
                }, response.status_code
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500

# FINAL FIXED ENDPOINT - Returns the REAL database job_id
@app.post("/api/submit-production")
async def submit_job_production(request: Request):
    """Submit job and return the REAL database job_id"""
    try:
        data = await request.json()
        
        # Create envelope with job data
        envelope = {
            "id": f"job-{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().timestamp(),
            "payload": {
                "job_id": int(datetime.now().timestamp()),  # Temporary ID for this request
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "completed_at": None,
                "failure_reason": None,
                "params": {
                    "url": data.get("url", ""),
                    "creator_id": data.get("creator_id", 1)
                },
                "job_type": data.get("job_type", "url")
            },
            "schema_version": "1.0",
            "orchestration_policy": "standard",
            "signature": "tier4-signature",
            "metadata": {
                "source": "tier4-frontend",
                "timestamp": datetime.now().isoformat()
            }
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
                tier3_response = response.json()
                
                # Check if Tier-3 returned a job_id (it should be the database ID)
                real_job_id = tier3_response.get("job_id")
                
                if real_job_id:
                    # Return the REAL database job_id to the frontend
                    return {
                        "job_id": real_job_id,  # This is the correct database ID!
                        "decision": tier3_response.get("decision"),
                        "risk_score": tier3_response.get("risk_score"),
                        "details": tier3_response.get("details")
                    }
                else:
                    # Fallback to the original response
                    return tier3_response
            else:
                return {
                    "error": f"Tier-3 returned {response.status_code}",
                    "details": response.text,
                    "envelope_sent": envelope
                }, response.status_code
                
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}, 500
