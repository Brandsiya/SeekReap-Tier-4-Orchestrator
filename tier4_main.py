# Build cache bust: 2026-03-16T21:44:18.528814
from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid, os, json, subprocess, psycopg2, re, requests
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

def get_db():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def normalize_youtube_url(url):
    """Convert youtu.be and shorts URLs to standard watch URLs."""
    if not url:
        return url
    # youtu.be/VIDEO_ID
    m = re.match(r'https?://youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    # youtube.com/shorts/VIDEO_ID
    m = re.match(r'https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    return url

def extract_youtube_metadata(url):
    """Fetch YouTube metadata via oEmbed (no API key needed)."""
    url = normalize_youtube_url(url)
    if not url or 'youtube' not in url:
        return {}
    try:
        m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
        if not m:
            return {}
        video_id = m.group(1)
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(oembed_url, timeout=10)
        if resp.status_code != 200:
            print(f"oEmbed status: {resp.status_code}")
            return {}
        data = resp.json()
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        return {
            'title': data.get('title', ''),
            'channel': data.get('author_name', ''),
            'duration': None,
            'upload_date': '',
            'thumbnail_url': thumbnail_url,
            'youtube_id': video_id,
            'description': '',
        }
    except Exception as e:
        print(f'YouTube oEmbed error: {e}')
    return {}

def get_or_create_creator(conn, firebase_uid, email=None, name=None):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        try:
            uuid.UUID(firebase_uid)
            cur.execute("SELECT id FROM creators WHERE id = %s", (firebase_uid,))
            row = cur.fetchone()
            if row:
                return firebase_uid
        except (ValueError, AttributeError):
            pass

        cur.execute("ALTER TABLE creators ADD COLUMN IF NOT EXISTS firebase_uid varchar(128) UNIQUE")
        conn.commit()

        cur.execute("SELECT id FROM creators WHERE firebase_uid = %s", (firebase_uid,))
        row = cur.fetchone()
        if row:
            return str(row["id"])

        new_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO creators (id, email, name, firebase_uid)
            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id
        """, (new_id, email or f"{firebase_uid}@firebase.user", name or "Creator", firebase_uid))
        row = cur.fetchone()
        conn.commit()
        if row:
            return str(row["id"])
        cur.execute("SELECT id FROM creators WHERE firebase_uid = %s", (firebase_uid,))
        row = cur.fetchone()
        return str(row["id"]) if row else new_id
    finally:
        cur.close()

def insert_submission(data, creator_uuid):
    content_url = data.get("content_url")
    content_hash = data.get("content_hash", "unknown")
    content_type = data.get("content_type", "video")

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Deduplication: return existing submission if same creator + content_hash
        cur.execute("""
            SELECT id, title, content_preview_url
            FROM submissions
            WHERE creator_id = %s AND content_hash = %s
            ORDER BY submitted_at DESC LIMIT 1
        """, (creator_uuid, content_hash))
        existing = cur.fetchone()
        if existing:
            print(f"Dedup: returning existing submission {existing['id']} for hash {content_hash}")
            return str(existing["id"]), existing["title"] or content_hash, "", existing["content_preview_url"] or ""

        # New submission
        submission_id = str(uuid.uuid4())
        yt_meta = extract_youtube_metadata(content_url)
        title = yt_meta.get("title") or data.get("title") or content_hash
        channel = yt_meta.get("channel", "")
        thumbnail_url = yt_meta.get("thumbnail_url", "")
        metadata = {**yt_meta, **(data.get("metadata") or {})}

        cur.execute("""
            INSERT INTO submissions
                (id, creator_id, title, description, content_hash, content_type,
                 content_url, content_preview_url, status, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
        """, (submission_id, creator_uuid, title,
              data.get("description") or yt_meta.get("description", ""),
              content_hash, content_type, content_url, thumbnail_url,
              json.dumps(metadata)))
        conn.commit()
        print(f"Created submission {submission_id} title={title!r}")

        cur.execute("""
            INSERT INTO job_queue
                (submission_id, creator_id, content_id, job_type, status, attempts)
            VALUES (%s, %s, %s, %s, %s, 0)
            ON CONFLICT DO NOTHING
        """, (submission_id, creator_uuid, content_url, "fingerprint", "pending"))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"DB insert error: {e}")
        raise
    finally:
        cur.close()
        conn.close()
    return submission_id, title, channel, thumbnail_url

# ── Rate limiting + backpressure helpers ─────────────────────────────────────
DAILY_QUOTA    = 50   # max submissions per creator per day
QUEUE_CAP      = 500  # max total pending+processing before rejecting


def check_rate_limit(creator_uuid: str) -> tuple:
    """Returns (allowed: bool, reason: str)."""
    conn = get_db()
    cur = conn.cursor()
    try:
        # 1. Global queue backpressure
        cur.execute("""
            SELECT COUNT(*) FROM job_queue
            WHERE status IN ('pending', 'processing')
        """)
        queue_depth = cur.fetchone()[0]
        if queue_depth >= QUEUE_CAP:
            return False, f"System queue full ({queue_depth} jobs). Try again later."

        # 2. Per-creator daily quota
        cur.execute("""
            SELECT COUNT(*) FROM submissions
            WHERE creator_id = %s
              AND submitted_at >= NOW() - INTERVAL '24 hours'
        """, (creator_uuid,))
        daily_count = cur.fetchone()[0]
        if daily_count >= DAILY_QUOTA:
            return False, f"Daily quota reached ({daily_count}/{DAILY_QUOTA} submissions). Resets in 24h."

        return True, ""
    finally:
        cur.close()
        conn.close()


@app.post("/api/submit")
def submit():
    try:
        data = request.get_json()
        firebase_uid = data.get("creator_id", "")
        conn = get_db()
        try:
            creator_uuid = get_or_create_creator(conn, firebase_uid,
                                                  data.get("email"), data.get("name"))
        finally:
            conn.close()

        # Rate limiting + backpressure
        allowed, reason = check_rate_limit(creator_uuid)
        if not allowed:
            return jsonify({"error": reason, "code": "RATE_LIMITED"}), 429

        submission_id, title, channel, thumbnail_url = insert_submission(data, creator_uuid)
        return jsonify({
            "submission_id": submission_id,
            "status": "pending",
            "creator_uuid": creator_uuid,
            "title": title,
            "channel": channel,
            "thumbnail_url": thumbnail_url,
        })
    except Exception as e:
        print(f"Submit error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/finalize")
def finalize():
    data = request.get_json()
    submission_id = data["submission_id"]
    analysis = data["analysis"]
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE submissions SET status='completed',
                overall_risk_score=%s, risk_level=%s, completed_at=NOW()
            WHERE id=%s RETURNING id
        """, (analysis.get("risk_score"), analysis.get("risk_level"), submission_id))
        updated = cur.fetchone()
        cur.execute("UPDATE job_queue SET status='completed', completed_at=NOW() WHERE submission_id=%s",
                    (submission_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()
    return jsonify({"status": "updated"}) if updated else (jsonify({"error": "not found"}), 404)

@app.get("/api/status/<submission_id>")
def status(submission_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT s.id, s.status, s.overall_risk_score, s.risk_level,
                   s.title, s.content_url, s.content_preview_url,
                   s.metadata, s.completed_at,
                   j.status as queue_status, j.attempts
            FROM submissions s
            LEFT JOIN job_queue j ON s.id = j.submission_id
            WHERE s.id = %s
        """, (submission_id,))
        row = cur.fetchone()

        # Fetch matches
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
    finally:
        cur.close()
        conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    result = dict(row)
    # Flatten metadata fields to top level for easy frontend access
    meta = result.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    result["yt_title"] = meta.get("title", "") or result.get("title", "")
    result["yt_channel"] = meta.get("channel", "")
    result["yt_duration"] = meta.get("duration")
    result["yt_upload_date"] = meta.get("upload_date", "")
    result["yt_thumbnail"] = meta.get("thumbnail_url", "") or result.get("content_preview_url", "")
    result["yt_id"] = meta.get("youtube_id", "")

    # Add match data
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

@app.get("/api/submissions")
def list_submissions():
    """Return recent submissions + match counts for a creator (used by dashboard)."""
    creator_id = request.headers.get('X-Creator-ID') or request.args.get('creator_id')
    if not creator_id:
        return jsonify({"error": "X-Creator-ID header required"}), 400
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Convert Firebase UID → deterministic UUID (same logic as /api/submit)
        import uuid as _uuid
        try:
            creator_uuid = str(_uuid.UUID(creator_id))
        except ValueError:
            creator_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, creator_id))

        cur.execute("""
            SELECT s.id, s.content_url, s.content_type, s.status,
                   s.overall_risk_score, s.risk_level,
                   s.submitted_at, s.completed_at,
                   s.title,
                   s.content_preview_url AS thumbnail,
                   NULL::text            AS channel,
                   COUNT(cm.id)     AS match_count,
                   MAX(cm.severity) AS max_severity
            FROM submissions s
            LEFT JOIN content_matches cm ON cm.submission_id = s.id
            WHERE s.creator_id = %s
            GROUP BY s.id
            ORDER BY s.submitted_at DESC
            LIMIT 50
        """, (creator_uuid,))
        rows = cur.fetchall()
        submissions = []
        for r in rows:
            submissions.append({
                "id":                str(r["id"]),
                "content_url":       r["content_url"],
                "content_type":      r["content_type"],
                "status":            (r["status"] or "").upper(),
                "overall_risk_score": r["overall_risk_score"],
                "risk_level":        r["risk_level"],
                "submitted_at":      r["submitted_at"].isoformat() if r["submitted_at"] else None,
                "completed_at":      r["completed_at"].isoformat() if r["completed_at"] else None,
                "title":             r["title"],
                "channel":           r["channel"],
                "thumbnail":         r["thumbnail"],
                "match_count":       int(r["match_count"] or 0),
                "max_severity":      r["max_severity"],
            })
        return jsonify({"submissions": submissions, "total": len(submissions)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.get("/api/metrics/latency")
def latency_metrics():
    """Return avg and p95 processing latency for completed submissions."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT
                COUNT(*)                                                            AS total_completed,
                ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - submitted_at))))::float AS avg_seconds,
                ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (completed_at - submitted_at))
                ))::float                                                           AS p95_seconds,
                ROUND(MIN(EXTRACT(EPOCH FROM (completed_at - submitted_at))))::float AS min_seconds,
                ROUND(MAX(EXTRACT(EPOCH FROM (completed_at - submitted_at))))::float AS max_seconds
            FROM submissions
            WHERE status = 'completed'
              AND completed_at IS NOT NULL
              AND submitted_at IS NOT NULL
              AND completed_at > submitted_at
              AND submitted_at >= NOW() - INTERVAL '7 days'
        """)
        row = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM job_queue
            WHERE status IN ('pending', 'processing')
        """)
        queue_depth = cur.fetchone()["cnt"]
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM job_queue
            WHERE status = 'failed'
              AND created_at >= NOW() - INTERVAL '24 hours'
        """)
        failed_24h = cur.fetchone()["cnt"]
        total = int(row["total_completed"] or 0)
        return jsonify({
            "latency_7d": {
                "total_completed": total,
                "avg_seconds":     float(row["avg_seconds"]) if row["avg_seconds"] is not None else None,
                "p95_seconds":     float(row["p95_seconds"]) if row["p95_seconds"] is not None else None,
                "min_seconds":     float(row["min_seconds"]) if row["min_seconds"] is not None else None,
                "max_seconds":     float(row["max_seconds"]) if row["max_seconds"] is not None else None,
            },
            "queue_depth":  queue_depth,
            "failed_24h":   failed_24h,
            "quota": {
                "daily_limit": DAILY_QUOTA,
                "queue_cap":   QUEUE_CAP,
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.post("/api/admin/recover-stuck-jobs")
def recover_stuck_jobs():
    """Reset jobs stuck in 'processing' for more than 10 minutes."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE job_queue
            SET status = 'pending', attempts = attempts + 1,
                failure_reason = 'recovered: stuck in processing'
            WHERE status = 'processing'
              AND processing_started_at < NOW() - INTERVAL '10 minutes'
            RETURNING job_id
        """)
        recovered = cur.fetchall()
        conn.commit()
        return jsonify({"recovered": len(recovered), "job_ids": [r[0] for r in recovered]})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()



@app.get("/api/verify-proof/<submission_id>")
def verify_blockchain_proof(submission_id):
    """Return blockchain proof for a submission"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        cur.execute("""
            SELECT blockchain_proof, blockchain_timestamp
            FROM submissions
            WHERE id = %s
        """, (submission_id,))
        row = cur.fetchone()
        
        if row and row.get('blockchain_proof'):
            proof = row['blockchain_proof']
            return {
                "verified": True,
                "proof": proof,
                "timestamp": row['blockchain_timestamp'].isoformat() if row['blockchain_timestamp'] else None
            }
        else:
            return {
                "error": "No blockchain proof found for this submission",
                "verified": False
            }
            
    except Exception as e:
        return {"error": str(e), "verified": False}, 500
    finally:
        cur.close()
        conn.close()
def verify_blockchain_proof(submission_id):
    """Verify blockchain proof for a submission"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        cur.execute("""
            SELECT id, blockchain_proof, blockchain_timestamp, blockchain_verified
            FROM submissions 
            WHERE id = %s
        """, (submission_id,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({"verified": False, "error": "Submission not found"}), 404
        
        if not row.get('blockchain_proof'):
            return jsonify({
                "verified": False, 
                "error": "No blockchain proof found for this submission"
            })
        
        return jsonify({
            "verified": row['blockchain_verified'],
            "timestamp": row['blockchain_timestamp'],
            "proof": row['blockchain_proof']
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()



@app.get("/health")
def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "db": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)

@app.get("/debug/env")
def debug_env():
    return {
        "has_youtube_key": bool(os.environ.get("YOUTUBE_API_KEY")),
        "has_db": bool(os.environ.get("DATABASE_URL")),
        "key_prefix": os.environ.get("YOUTUBE_API_KEY", "")[:8]
    }

# ── File Upload Endpoint for Content Registration ──
@app.post("/api/upload")
async def upload_content(request: Request):
    """
    Accepts file upload, creates submission, and queues for processing.
    Returns submission_id for tracking.
    """
    import uuid
    import tempfile
    import os
    
    form = await request.form()
    file = form.get("file")
    firebase_uid = form.get("firebase_uid")
    email = form.get("email")
    content_type = form.get("content_type", "file")
    
    if not file or not firebase_uid:
        return jsonify({"error": "file and firebase_uid required"}), 400
    
    try:
        # Generate creator UUID
        try:
            creator_uuid = str(uuid.UUID(firebase_uid))
        except ValueError:
            creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, firebase_uid))
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
            content = await file.read()
            tmp.write(content)
            file_path = tmp.name
        
        # Get audio fingerprint from file
        # We'll let Tier-5 handle this - just store file path reference
        # For now, create submission with file reference
        
        conn = get_db()
        cur = conn.cursor()
        
        # Check if content already exists (by file hash)
        import hashlib
        file_hash = hashlib.sha256(content).hexdigest()
        
        cur.execute("""
            SELECT id FROM submissions 
            WHERE content_hash = %s AND creator_id = %s
        """, (file_hash, creator_uuid))
        existing = cur.fetchone()
        
        if existing:
            cur.close()
            conn.close()
            os.unlink(file_path)
            return jsonify({
                "submission_id": str(existing[0]),
                "status": "already_registered",
                "message": "Content already registered"
            })
        
        # Create submission
        submission_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO submissions 
            (id, creator_id, content_type, content_hash, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (submission_id, creator_uuid, content_type, file_hash, 'pending'))
        
        # Create job queue entry
        cur.execute("""
            INSERT INTO job_queue 
            (submission_id, creator_id, content_id, job_type, status, attempts, params)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (submission_id, creator_uuid, file_hash, 'file_processing', 'pending', 0, 
              json.dumps({"file_path": file_path, "filename": file.filename})))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "submission_id": submission_id,
            "status": "pending",
            "message": "Content registered and queued for processing"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
