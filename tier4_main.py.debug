import os
import uuid
import json
import hashlib
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Database connection
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set")
    return psycopg2.connect(db_url)

# ── Submit content for registration ──
@app.post("/api/submit")
def submit_content():
    data = request.get_json()
    firebase_uid = data.get("firebase_uid")
    email = data.get("email")
    content_url = data.get("content_url")
    content_type = data.get("content_type", "youtube_video")
    creator_id = data.get("creator_id", firebase_uid)
    
    if not firebase_uid or not content_url:
        return jsonify({"error": "firebase_uid and content_url required"}), 400
    
    try:
        creator_uuid = str(uuid.UUID(creator_id))
    except ValueError:
        creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, creator_id))
    
    content_hash = hashlib.sha256(content_url.encode()).hexdigest()
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        cur.execute("""
            SELECT id FROM submissions WHERE content_hash = %s AND creator_id = %s
        """, (content_hash, creator_uuid))
        existing = cur.fetchone()
        if existing:
            return jsonify({
                "submission_id": str(existing["id"]),
                "status": "already_registered"
            })
        
        submission_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO submissions (id, creator_id, content_url, content_type, content_hash, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', NOW())
        """, (submission_id, creator_uuid, content_url, content_type, content_hash))
        
        cur.execute("""
            INSERT INTO job_queue (submission_id, creator_id, content_id, job_type, status, attempts)
            VALUES (%s, %s, %s, 'process', 'pending', 0)
        """, (submission_id, creator_uuid, content_hash))
        
        conn.commit()
        return jsonify({
            "submission_id": submission_id,
            "status": "pending"
        })
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── File upload endpoint ──
@app.post("/api/upload")
def upload_content():
    file = request.files.get("file")
    firebase_uid = request.form.get("firebase_uid")
    email = request.form.get("email")
    content_type = request.form.get("content_type", "file")
    
    if not file or not firebase_uid:
        return jsonify({"error": "file and firebase_uid required"}), 400
    
    try:
        creator_uuid = str(uuid.UUID(firebase_uid))
    except ValueError:
        creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, firebase_uid))
    
    content = file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    filename = file.filename
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        cur.execute("""
            SELECT id FROM submissions WHERE content_hash = %s AND creator_id = %s
        """, (file_hash, creator_uuid))
        existing = cur.fetchone()
        if existing:
            return jsonify({
                "submission_id": str(existing["id"]),
                "status": "already_registered"
            })
        
        submission_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO submissions (id, creator_id, content_type, content_hash, status, submitted_at)
            VALUES (%s, %s, %s, %s, 'pending', NOW())
        """, (submission_id, creator_uuid, content_type, file_hash))
        
        cur.execute("""
            INSERT INTO job_queue (submission_id, creator_id, content_id, job_type, status, attempts, params)
            VALUES (%s, %s, %s, 'file_processing', 'pending', 0, %s)
        """, (submission_id, creator_uuid, file_hash, json.dumps({"filename": filename})))
        
        conn.commit()
        return jsonify({
            "submission_id": submission_id,
            "status": "pending",
            "message": "Content registered and queued for processing"
        })
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Status endpoint ──
@app.get("/api/status/<submission_id>")
def get_status(submission_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT s.id, s.status, s.overall_risk_score, s.risk_level,
                   s.content_url, s.submitted_at, s.completed_at,
                   j.status as queue_status, j.attempts
            FROM submissions s
            LEFT JOIN job_queue j ON s.id = j.submission_id
            WHERE s.id = %s
        """, (submission_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        
        cur.execute("""
            SELECT cm.matched_submission_id, cm.similarity_score,
                   cm.match_type, cm.fingerprint_version, cm.detected_at,
                   ms.title as matched_title, ms.content_url as matched_url,
                   cm.severity
            FROM content_matches cm
            JOIN submissions ms ON ms.id = cm.matched_submission_id
            WHERE cm.submission_id = %s
            ORDER BY cm.similarity_score DESC
            LIMIT 10
        """, (submission_id,))
        matches = cur.fetchall()
        
        result = dict(row)
        result["matches"] = [
            {
                "matched_submission_id": str(m["matched_submission_id"]),
                "similarity_score": float(m["similarity_score"]),
                "match_type": m["match_type"],
                "fingerprint_version": m["fingerprint_version"],
                "detected_at": m["detected_at"].isoformat() if m["detected_at"] else None,
                "matched_title": m["matched_title"] or "",
                "matched_url": m["matched_url"] or "",
                "severity": m["severity"] or "medium",
            }
            for m in matches
        ]
        result["has_match"] = len(result["matches"]) > 0
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Finalize endpoint ──
@app.post("/api/finalize")
def finalize():
    data = request.get_json()
    submission_id = data.get("submission_id")
    analysis = data.get("analysis", {})
    
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE submissions
            SET status='completed',
                overall_risk_score=%s, risk_level=%s, completed_at=NOW()
            WHERE id=%s RETURNING id
        """, (analysis.get("risk_score"), analysis.get("risk_level"), submission_id))
        updated = cur.fetchone()
        cur.execute("""
            UPDATE job_queue SET status='completed', completed_at=NOW()
            WHERE submission_id=%s
        """, (submission_id,))
        conn.commit()
        return jsonify({"status": "updated"}) if updated else (jsonify({"error": "not found"}), 404)
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Verify blockchain proof ──
@app.get("/api/verify-proof/<submission_id>")
def verify_blockchain_proof(submission_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT blockchain_proof, blockchain_timestamp
            FROM submissions WHERE id = %s
        """, (submission_id,))
        row = cur.fetchone()
        if row and row.get('blockchain_proof'):
            return {
                "verified": True,
                "proof": row['blockchain_proof'],
                "timestamp": row['blockchain_timestamp'].isoformat() if row['blockchain_timestamp'] else None
            }
        else:
            return {"error": "No blockchain proof found", "verified": False}
    except Exception as e:
        return {"error": str(e), "verified": False}, 500
    finally:
        cur.close()
        conn.close()

# ── Health check ──
@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
