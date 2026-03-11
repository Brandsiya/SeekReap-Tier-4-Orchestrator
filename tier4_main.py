import os
import sys
import time
import logging
import threading
import requests
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import psycopg2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
PORT         = int(os.getenv('PORT', 8080))
DATABASE_URL = os.getenv('DATABASE_URL')
WORKER_URL   = os.getenv('WORKER_URL')   # Tier-3 Core Engine
TIER5_URL    = os.getenv('TIER5_URL')    # Tier-5 Worker Pool

logger.info(f"Tier-4 Orchestrator starting on port {PORT}")
logger.info(f"WORKER_URL (Tier-3): {WORKER_URL}")
logger.info(f"TIER5_URL  (Tier-5): {TIER5_URL}")

# ---------------------------------------------------------------------------
# Identity token cache — refreshes 10 min before expiry (tokens live 60 min)
# ---------------------------------------------------------------------------
_token_cache = {}
_token_lock  = threading.Lock()

def _get_identity_token(audience: str):
    with _token_lock:
        entry = _token_cache.get(audience)
        if entry and time.time() < entry["expires_at"] - 600:
            return entry["token"]
    try:
        meta_url = (
            "http://metadata.google.internal/computeMetadata/v1/instance"
            f"/service-accounts/default/identity?audience={audience}"
        )
        resp = requests.get(meta_url, headers={"Metadata-Flavor": "Google"}, timeout=3)
        if resp.status_code == 200:
            token = resp.text
            with _token_lock:
                _token_cache[audience] = {"token": token, "expires_at": time.time() + 3600}
            logger.info("Identity token refreshed for: %s", audience)
            return token
    except Exception as e:
        logger.warning("Could not fetch identity token: %s", e)
    return None

def _auth_headers(audience: str) -> dict:
    token = _get_identity_token(audience)
    return {"Authorization": f"Bearer {token}"} if token else {}

# ---------------------------------------------------------------------------
# Tier-3 call with exponential-backoff retry (handles cold-start 503s)
# ---------------------------------------------------------------------------
def _call_tier3(path: str, method: str = "GET", json_body=None,
                retries: int = 3, backoff: float = 1.0):
    if not WORKER_URL:
        raise RuntimeError("WORKER_URL not configured")
    url     = f"{WORKER_URL}{path}"
    headers = _auth_headers(WORKER_URL)
    for attempt in range(1, retries + 1):
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=10)
            else:
                resp = requests.post(url, headers=headers, json=json_body, timeout=10)
            if resp.status_code == 503 and attempt < retries:
                wait = backoff * (2 ** (attempt - 1))
                logger.warning("Tier-3 503, retry %d/%d in %.1fs", attempt, retries, wait)
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.Timeout:
            if attempt < retries:
                time.sleep(backoff * (2 ** (attempt - 1)))
            else:
                raise
        except Exception:
            if attempt < retries:
                time.sleep(backoff * (2 ** (attempt - 1)))
            else:
                raise
    raise RuntimeError("Tier-3 unreachable after %d attempts" % retries)

# ---------------------------------------------------------------------------
# Tier-5 dispatch (fire-and-forget)
# ---------------------------------------------------------------------------
def _dispatch_to_tier5(submission_id: str, payload: dict) -> bool:
    if not TIER5_URL:
        logger.warning("TIER5_URL not set — skipping Tier-5 dispatch")
        return False
    try:
        body = {"submission_id": submission_id, **payload}
        resp = requests.post(f"{TIER5_URL}/process", json=body, timeout=10)
        if resp.status_code in (200, 202):
            logger.info("Dispatched %s to Tier-5", submission_id)
            return True
        logger.error("Tier-5 rejected: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("Failed to dispatch to Tier-5: %s", e)
    return False

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------
def _get_db():
    return psycopg2.connect(DATABASE_URL)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":    "healthy",
        "tier":      4,
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route('/api/worker-forward/health', methods=['GET'])
def worker_forward_health():
    """Forward health probe to Tier-3 Core Engine."""
    try:
        resp = _call_tier3("/health")
        if resp.status_code == 200:
            return jsonify({
                "status":         "healthy",
                "tier":           4,
                "tier3_status":   "healthy",
                "tier3_response": resp.json(),
            }), 200
        return jsonify({
            "status":         "degraded",
            "tier":           4,
            "tier3_status":   "unhealthy",
            "tier3_response": resp.text,
        }), 502
    except requests.exceptions.Timeout:
        return jsonify({"status": "error", "tier": 4, "tier3_status": "timeout"}), 504
    except Exception as e:
        return jsonify({"status": "error", "tier": 4, "error": str(e)}), 503


@app.route('/api/submit', methods=['POST'])
def submit():
    """
    Tier-6 → Tier-4 submission endpoint.
    Inserts into submissions → Tier-3 analysis → Tier-5 dispatch.
    """
    data         = request.json or {}
    content_hash = data.get('content_hash')
    creator_id   = data.get('creator_id')

    if not content_hash or not creator_id:
        return jsonify({"error": "content_hash and creator_id are required"}), 400

    # 1. DB insert (DB trigger creates scan job automatically)
    submission_id = None
    try:
        conn = _get_db()
        cur  = conn.cursor()
        # Upsert creator so FK constraint never fails for new Firebase users
        cur.execute(
            "INSERT INTO creators (id, email, display_name) "
            "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (creator_id, data.get('email', ''), data.get('display_name', '')),
        )
        cur.execute(
            "INSERT INTO submissions (creator_id, content_hash, content_type, status, submitted_at) "
            "VALUES (%s, %s, 'video', 'QUEUED', NOW()) RETURNING id",
            (creator_id, content_hash),
        )
        submission_id = str(cur.fetchone()[0])
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Submission %s inserted", submission_id)
    except Exception as e:
        logger.error("DB insert failed: %s", e)
        return jsonify({"error": "Database error", "detail": str(e)}), 500

    # 2. Forward to Tier-3 for analysis
    tier3_result = None
    try:
        resp = _call_tier3("/analyze", method="POST",
                           json_body={"submission_id": submission_id, "content_hash": content_hash})
        if resp.status_code == 200:
            tier3_result = resp.json()
    except Exception as e:
        logger.error("Tier-3 call failed for %s: %s", submission_id, e)

    # 3. Dispatch to Tier-5
    _dispatch_to_tier5(submission_id, data)

    return jsonify({
        "submission_id":  submission_id,
        "status":         "QUEUED",
        "tier3_analysis": tier3_result,
        "tier5_dispatch": TIER5_URL is not None,
    }), 202


@app.route('/api/job-update', methods=['POST'])
def job_update():
    """
    Tier-5 → Tier-4 status callback.
    Updates submissions by id (the actual PK — not job_id).
    """
    data          = request.json or {}
    submission_id = data.get('submission_id') or data.get('job_id')
    status        = data.get('status', 'PROCESSING')

    if not submission_id:
        return jsonify({"error": "submission_id is required"}), 400

    try:
        conn = _get_db()
        cur  = conn.cursor()
        cur.execute(
            "UPDATE submissions SET status = %s, completed_at = NOW() WHERE id = %s",
            (status, submission_id),
        )
        rows = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if rows == 0:
            return jsonify({"error": "submission not found"}), 404
        logger.info("Submission %s → %s", submission_id, status)
        return jsonify({"status": "updated", "submission_id": submission_id}), 200
    except Exception as e:
        logger.error("DB update failed: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
