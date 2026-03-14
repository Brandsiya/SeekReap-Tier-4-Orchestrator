import os
import sys
import logging
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Get environment variables
PORT = int(os.getenv('PORT', 8080))
DATABASE_URL = os.getenv('DATABASE_URL')
WORKER_URL = os.getenv('WORKER_URL')

logger.info(f"Starting Tier-4 Orchestrator on port {PORT}")
logger.info(f"WORKER_URL: {WORKER_URL}")

@app.route('/health', methods=['GET'])
def health():
    logger.info("Health check called")
    return jsonify({
        "status": "healthy",
        "tier": 4,
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }), 200

@app.route('/api/worker-forward/health', methods=['GET'])
def worker_forward_health():
    """Forward health check to Tier-3 Core Engine"""
    tier3_url = WORKER_URL
    
    if not tier3_url:
        return jsonify({
            "status": "error",
            "tier": 4,
            "error": "WORKER_URL not configured"
        }), 500
    
    try:
        # For Cloud Run, we need to get an identity token
        # But for simplicity in testing, we'll try without auth first
        # In production, you'd use the metadata server
        headers = {}
        
        # Try to get token from metadata server if available (Cloud Run environment)
        try:
            import requests
            metadata_url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=" + tier3_url
            token_response = requests.get(metadata_url, headers={"Metadata-Flavor": "Google"}, timeout=2)
            if token_response.status_code == 200:
                headers = {'Authorization': f'Bearer {token_response.text}'}
                logger.info("Got identity token from metadata server")
        except:
            logger.info("No metadata server, proceeding without auth token")
        
        # Forward request to Tier-3
        response = requests.get(f"{tier3_url}/health", headers=headers, timeout=5)
        
        if response.status_code == 200:
            return jsonify({
                "status": "healthy",
                "tier": 4,
                "tier3_status": "healthy",
                "tier3_response": response.json()
            }), 200
        else:
            return jsonify({
                "status": "degraded",
                "tier": 4,
                "tier3_status": "unhealthy",
                "tier3_response": response.text
            }), 502
    except requests.exceptions.Timeout:
        logger.error("Timeout connecting to Tier-3")
        return jsonify({
            "status": "error",
            "tier": 4,
            "tier3_status": "timeout",
            "error": "Connection to Tier-3 timed out"
        }), 504
    except Exception as e:
        logger.error(f"Error connecting to Tier-3: {str(e)}")
        return jsonify({
            "status": "error",
            "tier": 4,
            "tier3_status": "unreachable",
            "error": str(e)
        }), 503

@app.route('/api/job-update', methods=['POST'])
def job_update():
    data = request.json
    job_id = data.get('job_id')
    logger.info(f"Job update received: {job_id}")
    return jsonify({"status": "received", "job_id": job_id}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)
