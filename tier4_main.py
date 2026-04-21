# SeekReap Tier-4 Orchestrator
# Build: 2026-04-15
from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid, os, json, psycopg2, re, requests, hashlib, random, string
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from datetime import datetime
import hmac
import hashlib

load_dotenv()
app = Flask(__name__)
CORS(app, origins=[
    "https://seekreap-backend-dev.fly.dev",
    "https://seekreap-frontend.onrender.com",
    "http://localhost:3000",
    "http://localhost:8080",
])

DAILY_QUOTA = 50
QUEUE_CAP   = 500
VALID_PLANS = {"free", "creator", "studio", "payg"}


def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def normalize_youtube_url(url):
    if not url:
        return url
    m = re.match(r'https?://youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    m = re.match(r'https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    return url


def extract_youtube_metadata(url):
    url = normalize_youtube_url(url)
    if not url or 'youtube' not in url:
        return {}
    try:
        m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
        if not m:
            return {}
        video_id = m.group(1)
        oembed = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(oembed, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        return {
            'title':         data.get('title', ''),
            'channel':       data.get('author_name', ''),
            'duration':      None,
            'upload_date':   '',
            'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            'youtube_id':    video_id,
            'description':   '',
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
    content_url  = data.get("content_url")
    content_hash = data.get("content_hash", "unknown")
    content_type = data.get("content_type", "video")

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, title, content_preview_url
            FROM submissions
            WHERE creator_id = %s AND content_hash = %s
            ORDER BY submitted_at DESC LIMIT 1
        """, (creator_uuid, content_hash))
        existing = cur.fetchone()
        if existing:
            print(f"Dedup: returning {existing['id']} for hash {content_hash}")
            return str(existing["id"]), existing["title"] or content_hash, "", existing["content_preview_url"] or ""

        submission_id = str(uuid.uuid4())
        yt_meta       = extract_youtube_metadata(content_url)
        title         = yt_meta.get("title") or data.get("title") or content_hash
        channel       = yt_meta.get("channel", "")
        thumbnail_url = yt_meta.get("thumbnail_url", "")
        metadata      = {**yt_meta, **(data.get("metadata") or {})}

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


def check_rate_limit(creator_uuid: str) -> tuple:
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM job_queue WHERE status IN ('pending', 'processing')")
        if cur.fetchone()[0] >= QUEUE_CAP:
            return False, f"System queue full. Try again later."

        cur.execute("""
            SELECT COUNT(*) FROM submissions
            WHERE creator_id = %s AND submitted_at >= NOW() - INTERVAL '24 hours'
        """, (creator_uuid,))
        daily = cur.fetchone()[0]
        if daily >= DAILY_QUOTA:
            return False, f"Daily quota reached ({daily}/{DAILY_QUOTA}). Resets in 24h."

        return True, ""
    finally:
        cur.close()
        conn.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "ok", "tier": 4})
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500


@app.post("/api/submit")
def submit():
    try:
        data         = request.get_json()
        firebase_uid = data.get("creator_id", "")
        conn         = get_db()
        try:
            creator_uuid = get_or_create_creator(conn, firebase_uid,
                                                  data.get("email"), data.get("name"))
        finally:
            conn.close()

        allowed, reason = check_rate_limit(creator_uuid)
        if not allowed:
            return jsonify({"error": reason, "code": "RATE_LIMITED"}), 429

        submission_id, title, channel, thumbnail_url = insert_submission(data, creator_uuid)
        return jsonify({
            "submission_id": submission_id,
            "status":        "pending",
            "creator_uuid":  creator_uuid,
            "title":         title,
            "channel":       channel,
            "thumbnail_url": thumbnail_url,
        })
    except Exception as e:
        print(f"Submit error: {e}")
        return jsonify({"error": str(e)}), 500


@app.post("/api/certify")
def certify_work():
    body = request.get_json(force=True) or {}

    creator_id_raw  = (body.get("creator_id") or body.get("supabase_uid") or "").strip()
    title           = (body.get("title") or "Untitled Work").strip()
    plan            = body.get("plan", "free").lower().strip()
    work_type       = (body.get("work_type") or "other").strip()
    artistic_name   = (body.get("artistic_name") or "").strip()
    content_hash    = (body.get("content_hash") or "").strip()
    collaborators   = body.get("collaborators") or []
    ownership_split = body.get("ownership_split") or {}
    email           = (body.get("email") or "").strip()

    if not creator_id_raw:
        return jsonify({"error": "creator_id required"}), 400
    if plan not in VALID_PLANS:
        return jsonify({"error": f"invalid plan '{plan}'"}), 400

    try:
        creator_uuid = str(uuid.UUID(creator_id_raw))
    except (ValueError, AttributeError):
        creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, creator_id_raw))

    if not content_hash:
        content_hash = hashlib.sha256(
            f"{creator_uuid}:{title}:{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()

    suffix      = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    cert_id     = f"SR-{datetime.utcnow().strftime('%Y%m%d')}-{suffix}"
    content_url = body.get("content_url") or f"seekreap://local/{content_hash}"

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        creator_email = email or f"{creator_uuid[:8]}@seekreap.local"
        # Upsert by id; if email conflicts with another row, fall back to placeholder
        try:
            cur.execute("""
                INSERT INTO creators (id, email, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """, (creator_uuid, creator_email, artistic_name or title))
        except Exception:
            conn.rollback()
            cur.execute("""
                INSERT INTO creators (id, email, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """, (creator_uuid, f"{creator_uuid[:8]}@seekreap.local", artistic_name or title))

        cur.execute("""
            INSERT INTO submissions
               (id, creator_id, content_url, content_hash, title,
                plan, artistic_name, work_type, cert_id,
                collaborators, ownership_split, status, content_type)
            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s)
            RETURNING id
        """, (creator_uuid, content_url, content_hash, title,
              plan, artistic_name, work_type, cert_id,
              Json(collaborators) if collaborators else None,
              Json(ownership_split) if ownership_split else None,
              work_type))

        row           = cur.fetchone()
        submission_id = str(row["id"])

        # Insert into content_submissions BEFORE job_queue (FK requirement)
        cur.execute("""
            INSERT INTO content_submissions
                (submission_id, creator_id, title, content_hash, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (submission_id) DO NOTHING
        """, (submission_id, creator_uuid, title, content_hash))

        cur.execute("""
            INSERT INTO job_queue
               (submission_id, creator_id, content_id, job_type, status, attempts)
            VALUES (%s, %s, %s, 'certification', 'pending', 0)
            ON CONFLICT DO NOTHING
        """, (submission_id, creator_uuid, content_url))

        conn.commit()
        print(f"[CERTIFY] submission={submission_id} cert={cert_id} plan={plan}")

        # Generate QR code URL
        qr_url = f"/api/qrcode/{cert_id}"
        
        return jsonify({
            "submission_id": submission_id,
            "cert_id":       cert_id,
            "plan":          plan,
            "status":        "queued",
            "qr_url":        qr_url,
            "message":       "Certification queued successfully",
        }), 202

    except Exception as e:
        conn.rollback()
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.get("/api/certify/<submission_id>")
def certify_status(submission_id):
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, status, cert_id, title, work_type, plan,
                   artistic_name, overall_risk_score, risk_level,
                   submitted_at, completed_at, failure_reason
            FROM submissions WHERE id = %s
        """, (submission_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        data = dict(row)
        data["id"] = str(data["id"])
        for k in ("submitted_at", "completed_at"):
            if data[k]:
                data[k] = data[k].isoformat()
        return jsonify(data), 200
    finally:
        cur.close()
        conn.close()


@app.get("/api/status/<submission_id>")
def status(submission_id):
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
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

        cur.execute("""
            SELECT cm.matched_submission_id, cm.similarity_score,
                   cm.match_type, cm.fingerprint_version, cm.detected_at,
                   ms.title as matched_title, ms.content_url as matched_url,
                   cm.severity
            FROM content_matches cm
            JOIN submissions ms ON ms.id = cm.matched_submission_id
            WHERE cm.submission_id = %s
            ORDER BY cm.similarity_score DESC LIMIT 10
        """, (submission_id,))
        matches = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not row:
        return jsonify({"error": "not found"}), 404

    result = dict(row)
    meta = result.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    result["yt_title"]       = meta.get("title", "") or result.get("title", "")
    result["yt_channel"]     = meta.get("channel", "")
    result["yt_duration"]    = meta.get("duration")
    result["yt_upload_date"] = meta.get("upload_date", "")
    result["yt_thumbnail"]   = meta.get("thumbnail_url", "") or result.get("content_preview_url", "")
    result["yt_id"]          = meta.get("youtube_id", "")
    result["matches"] = [
        {
            "matched_submission_id": str(m["matched_submission_id"]),
            "similarity_score":      float(m["similarity_score"]),
            "match_type":            m["match_type"],
            "fingerprint_version":   m["fingerprint_version"],
            "detected_at":           m["detected_at"].isoformat() if m["detected_at"] else None,
            "matched_title":         m["matched_title"] or "",
            "matched_url":           m["matched_url"] or "",
            "severity":              m["severity"] or "medium",
        }
        for m in matches
    ]
    result["has_match"] = len(result["matches"]) > 0
    return jsonify(result)


@app.get("/api/submissions")
def list_submissions():
    creator_id = request.headers.get('X-Creator-ID') or request.args.get('creator_id')
    if not creator_id:
        return jsonify({"error": "X-Creator-ID header required"}), 400

    try:
        creator_uuid = str(uuid.UUID(creator_id))
    except ValueError:
        creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, creator_id))

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT s.id, s.content_url, s.content_type, s.status,
                   s.overall_risk_score, s.risk_level,
                   s.submitted_at, s.completed_at, s.title,
                   s.content_preview_url AS thumbnail,
                   NULL::text            AS channel,
                   COUNT(cm.id)          AS match_count,
                   MAX(cm.severity)      AS max_severity
            FROM submissions s
            LEFT JOIN content_matches cm ON cm.submission_id = s.id
            WHERE s.creator_id = %s
            GROUP BY s.id
            ORDER BY s.submitted_at DESC
            LIMIT 50
        """, (creator_uuid,))
        rows = cur.fetchall()
        return jsonify({
            "submissions": [
                {
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
                }
                for r in rows
            ],
            "total": len(rows),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.get("/api/metrics/latency")
def latency_metrics():
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT
                COUNT(*)::int AS total_completed,
                ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - submitted_at))))::float AS avg_seconds,
                ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (completed_at - submitted_at))
                ))::float AS p95_seconds,
                ROUND(MIN(EXTRACT(EPOCH FROM (completed_at - submitted_at))))::float AS min_seconds,
                ROUND(MAX(EXTRACT(EPOCH FROM (completed_at - submitted_at))))::float AS max_seconds
            FROM submissions
            WHERE status = 'completed'
              AND completed_at IS NOT NULL AND submitted_at IS NOT NULL
              AND completed_at > submitted_at
              AND submitted_at >= NOW() - INTERVAL '7 days'
        """)
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS cnt FROM job_queue WHERE status IN ('pending','processing')")
        queue_depth = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM job_queue WHERE status='failed' AND created_at >= NOW() - INTERVAL '24 hours'")
        failed_24h = cur.fetchone()["cnt"]
        return jsonify({
            "latency_7d": {
                "total_completed": int(row["total_completed"] or 0),
                "avg_seconds":     float(row["avg_seconds"]) if row["avg_seconds"] is not None else None,
                "p95_seconds":     float(row["p95_seconds"]) if row["p95_seconds"] is not None else None,
                "min_seconds":     float(row["min_seconds"]) if row["min_seconds"] is not None else None,
                "max_seconds":     float(row["max_seconds"]) if row["max_seconds"] is not None else None,
            },
            "queue_depth": queue_depth,
            "failed_24h":  failed_24h,
            "quota":       {"daily_limit": DAILY_QUOTA, "queue_cap": QUEUE_CAP},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.post("/api/finalize")
def finalize():
    data          = request.get_json()
    submission_id = data["submission_id"]
    analysis      = data["analysis"]
    conn = get_db()
    cur  = conn.cursor()
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


@app.post("/api/admin/recover-stuck-jobs")
def recover_stuck_jobs():
    conn = get_db()
    cur  = conn.cursor()
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
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, blockchain_proof, blockchain_timestamp, blockchain_verified
            FROM submissions WHERE id = %s
        """, (submission_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"verified": False, "error": "not found"}), 404
        if not row.get("blockchain_proof"):
            return jsonify({"verified": False, "error": "No blockchain proof found"})
        return jsonify({
            "verified":  row["blockchain_verified"],
            "timestamp": row["blockchain_timestamp"],
            "proof":     row["blockchain_proof"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.get("/debug/env")
def debug_env():
    return jsonify({
        "has_youtube_key": bool(os.environ.get("YOUTUBE_API_KEY")),
        "has_db":          bool(os.environ.get("DATABASE_URL")),
        "key_prefix":      os.environ.get("YOUTUBE_API_KEY", "")[:8],
    })



@app.get("/api/qrcode/<string:cert_id>")
def generate_qrcode(cert_id):
    """Generate QR code for certificate verification"""
    import qrcode
    from io import BytesIO
    from flask import send_file
    
    # Create verification URL
    verify_url = f"https://seekreap-frontend.onrender.com/verification_portal.html?cert={cert_id}"
    
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(verify_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save to bytes
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png')


# ══════════════════════════════════════════════════════════════════════════════
# SeekReap Payment System — append this block into tier4_main.py
# (after the existing imports and before if __name__ == "__main__")
# ══════════════════════════════════════════════════════════════════════════════

# ── Additional imports (merge with existing imports at top of file) ───────────
import hmac
import hashlib

# ── Additional env vars (add to .env) ────────────────────────────────────────
# PAYSTACK_SECRET_KEY=sk_live_xxxx          (or sk_test_xxxx for testing)
# PAYFAST_MERCHANT_ID=xxxxx
# PAYFAST_MERCHANT_KEY=xxxxx
# PAYFAST_PASSPHRASE=xxxxx                  (optional but recommended)
# FRONTEND_URL=https://seekreap-frontend.onrender.com
# TIER4_INTERNAL=https://seekreap-tier-4-dev.fly.dev

PAYSTACK_SECRET  = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYFAST_MERCHANT_ID  = os.environ.get("PAYFAST_MERCHANT_ID", "")
PAYFAST_MERCHANT_KEY = os.environ.get("PAYFAST_MERCHANT_KEY", "")
PAYFAST_PASSPHRASE   = os.environ.get("PAYFAST_PASSPHRASE", "")
FRONTEND_URL     = os.environ.get("FRONTEND_URL", "https://seekreap-frontend.onrender.com")
TIER4_INTERNAL   = os.environ.get("TIER4_INTERNAL", "https://seekreap-tier-4-dev.fly.dev")

# Plan → amount in cents (ZAR)
PLAN_AMOUNTS = {
    "payg":    199,    # R1.99
    "creator": 999,    # R9.99/mo
    "studio":  2999,   # R29.99/mo
}


# ── DB: ensure payments table exists ─────────────────────────────────────────
def ensure_payments_table():
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                creator_id    TEXT NOT NULL,
                submission_id UUID,
                plan          TEXT NOT NULL,
                amount        INTEGER NOT NULL,
                currency      TEXT DEFAULT 'ZAR',
                gateway       TEXT NOT NULL,
                payment_ref   TEXT,
                status        TEXT DEFAULT 'pending',
                metadata      JSONB,
                created_at    TIMESTAMP DEFAULT NOW(),
                paid_at       TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_payment_ref
            ON payments(payment_ref)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_creator_id
            ON payments(creator_id)
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


# Call at startup
try:
    ensure_payments_table()
    print("[PAYMENT] payments table ready")
except Exception as e:
    print(f"[PAYMENT] table init warning: {e}")


# ── Gateway selector ──────────────────────────────────────────────────────────
def select_gateway(data):
    # All regions → Paystack (global). PayFast added as opt-in later.
    return "paystack"


# ── Paystack init ─────────────────────────────────────────────────────────────
def init_paystack(payment_id, data):
    payload = {
        "email":        data["email"],
        "amount":       data["amount"],   # in kobo/cents (integer)
        "reference":    str(payment_id),
        "callback_url": FRONTEND_URL + "/payment_success.html",
        "metadata": {
            "payment_id":  str(payment_id),
            "plan":        data["plan"],
            "creator_id":  data["creator_id"],
            "title":       data.get("title", ""),
            "cancel_action": FRONTEND_URL + "/certification_portal.html",
        }
    }
    try:
        r = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=15,
        )
        resp = r.json()
        if not resp.get("status"):
            return jsonify({"error": resp.get("message", "Paystack error")}), 502
        return jsonify({
            "gateway":           "paystack",
            "authorization_url": resp["data"]["authorization_url"],
            "access_code":       resp["data"]["access_code"],
            "reference":         resp["data"]["reference"],
        })
    except Exception as e:
        print(f"[PAYSTACK] init error: {e}")
        return jsonify({"error": "Payment gateway unavailable"}), 502


# ── PayFast init (secondary) ──────────────────────────────────────────────────
def init_payfast(payment_id, data):
    """
    PayFast uses a redirect form POST rather than an API call.
    Returns the fields the frontend should POST to PayFast.
    """
    import urllib.parse

    fields = {
        "merchant_id":   PAYFAST_MERCHANT_ID,
        "merchant_key":  PAYFAST_MERCHANT_KEY,
        "return_url":    FRONTEND_URL + "/payment_success.html",
        "cancel_url":    FRONTEND_URL + "/certification_portal.html",
        "notify_url":    TIER4_INTERNAL + "/api/payments/webhook/payfast",
        "m_payment_id":  str(payment_id),
        "amount":        f"{data['amount'] / 100:.2f}",
        "item_name":     f"SeekReap {data['plan'].title()} Plan",
        "email_address": data["email"],
    }

    # Generate signature
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}"
        for k, v in fields.items() if v
    )
    if PAYFAST_PASSPHRASE:
        sig_str += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE)}"
    fields["signature"] = hashlib.md5(sig_str.encode()).hexdigest()

    return jsonify({
        "gateway":    "payfast",
        "action_url": "https://www.payfast.co.za/eng/process",
        "fields":     fields,
    })


# ── Internal: trigger certification after payment ────────────────────────────
def trigger_certification(payment_row, pending_meta):
    """
    Called after payment is marked paid.
    Calls /api/certify internally using data stored at initiation time.
    """
    creator_id   = payment_row["creator_id"]
    plan         = payment_row["plan"]
    meta         = pending_meta or {}

    payload = {
        "creator_id":       creator_id,
        "email":            meta.get("email", ""),
        "title":            meta.get("title", "Untitled Work"),
        "work_type":        meta.get("work_type", "other"),
        "content_hash":     meta.get("content_hash", ""),
        "plan":             plan,
        "collaborators":    meta.get("collaborators", []),
        "ownership_split":  meta.get("ownership_split", {}),
        "artistic_name":    meta.get("artistic_name", ""),
        "payment_id":       str(payment_row["id"]),
    }

    try:
        r = requests.post(
            TIER4_INTERNAL + "/api/certify",
            json=payload,
            timeout=30,
        )
        data = r.json()
        print(f"[PAYMENT] triggered cert: submission={data.get('submission_id')} cert={data.get('cert_id')}")

        # Update payment row with submission_id
        conn = get_db()
        cur  = conn.cursor()
        try:
            cur.execute("""
                UPDATE payments SET submission_id = %s
                WHERE id = %s
            """, (data.get("submission_id"), str(payment_row["id"])))
            conn.commit()
        finally:
            cur.close()
            conn.close()

        return data
    except Exception as e:
        print(f"[PAYMENT] trigger_certification error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/payments/initiate")
def initiate_payment():
    """
    Frontend calls this instead of /api/certify for paid plans.
    Stores pending cert data, creates payment record, returns gateway URL.
    """
    body = request.get_json(force=True) or {}

    creator_id = (body.get("creator_id") or "").strip()
    plan       = (body.get("plan") or "free").lower().strip()
    email      = (body.get("email") or "").strip()
    amount     = body.get("amount")  # optional override; else use PLAN_AMOUNTS

    if not creator_id:
        return jsonify({"error": "creator_id required"}), 400
    if plan == "free":
        return jsonify({"error": "Free plan does not require payment"}), 400
    if plan not in PLAN_AMOUNTS:
        return jsonify({"error": f"Unknown plan '{plan}'"}), 400
    if not email:
        return jsonify({"error": "email required"}), 400

    amount = int(amount) if amount else PLAN_AMOUNTS[plan]

    # Store full pending cert metadata so webhook can trigger certification
    pending_meta = {
        "email":           email,
        "title":           body.get("title", "Untitled Work"),
        "work_type":       body.get("work_type", "other"),
        "content_hash":    body.get("content_hash", ""),
        "collaborators":   body.get("collaborators", []),
        "ownership_split": body.get("ownership_split", {}),
        "artistic_name":   body.get("artistic_name", ""),
    }

    gateway = select_gateway(body)

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO payments
                (creator_id, plan, amount, currency, gateway, status, metadata)
            VALUES (%s, %s, %s, 'ZAR', %s, 'pending', %s)
            RETURNING id
        """, (creator_id, plan, amount, gateway, Json(pending_meta)))
        payment_id = str(cur.fetchone()["id"])
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    data = {**body, "amount": amount, "email": email}

    if gateway == "paystack":
        return init_paystack(payment_id, data)
    if gateway == "payfast":
        return init_payfast(payment_id, data)

    return jsonify({"error": "No gateway available"}), 502


@app.post("/api/payments/webhook/paystack")
def paystack_webhook():
    """
    Paystack calls this after a successful charge.
    Verifies HMAC signature, marks payment paid, triggers certification.
    """
    raw_body = request.get_data()
    sig      = request.headers.get("X-Paystack-Signature", "")

    # Verify signature
    if PAYSTACK_SECRET:
        expected = hmac.new(
            PAYSTACK_SECRET.encode(),
            raw_body,
            hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            print("[PAYSTACK] webhook signature mismatch")
            return jsonify({"error": "Invalid signature"}), 400

    payload = request.get_json(force=True) or {}
    event   = payload.get("event")

    if event != "charge.success":
        return jsonify({"status": "ignored"}), 200

    ref    = payload["data"]["reference"]
    amount = payload["data"]["amount"]

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM payments WHERE id = %s", (ref,))
        payment = cur.fetchone()

        if not payment:
            print(f"[PAYSTACK] webhook: payment {ref} not found")
            return jsonify({"error": "payment not found"}), 404

        if payment["status"] == "paid":
            print(f"[PAYSTACK] webhook: {ref} already paid (idempotent)")
            return jsonify({"status": "ok"}), 200

        # Mark paid
        cur.execute("""
            UPDATE payments
            SET status = 'paid', paid_at = NOW(), payment_ref = %s
            WHERE id = %s
            RETURNING *
        """, (ref, ref))
        paid_row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[PAYSTACK] webhook DB error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    # Trigger certification outside DB transaction
    pending_meta = paid_row.get("metadata") or {}
    trigger_certification(paid_row, pending_meta)

    return jsonify({"status": "ok"}), 200


@app.post("/api/payments/webhook/payfast")
def payfast_webhook():
    """
    PayFast ITN (Instant Transaction Notification) handler.
    """
    import urllib.parse

    data = request.form.to_dict()
    payment_id = data.get("m_payment_id")
    pf_status  = data.get("payment_status")

    # Verify signature
    sig_received = data.pop("signature", "")
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}"
        for k, v in data.items() if v
    )
    if PAYFAST_PASSPHRASE:
        sig_str += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE)}"
    expected_sig = hashlib.md5(sig_str.encode()).hexdigest()

    if expected_sig != sig_received:
        print("[PAYFAST] ITN signature mismatch")
        return "INVALID", 400

    if pf_status != "COMPLETE":
        return "ok", 200

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment or payment["status"] == "paid":
            return "ok", 200

        cur.execute("""
            UPDATE payments SET status = 'paid', paid_at = NOW(),
                payment_ref = %s
            WHERE id = %s RETURNING *
        """, (data.get("pf_payment_id"), payment_id))
        paid_row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        return str(e), 500
    finally:
        cur.close()
        conn.close()

    trigger_certification(paid_row, paid_row.get("metadata") or {})
    return "ok", 200


@app.get("/api/payments/<payment_id>")
def get_payment_status(payment_id):
    """
    Frontend polls this to get payment + submission status.
    Returns payment status and, once triggered, the submission/cert IDs.
    """
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT p.id, p.status, p.plan, p.paid_at, p.submission_id,
                   p.gateway, p.payment_ref,
                   s.cert_id, s.status as cert_status
            FROM payments p
            LEFT JOIN submissions s ON s.id = p.submission_id
            WHERE p.id = %s
        """, (payment_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        result = dict(row)
        result["id"]           = str(result["id"])
        result["submission_id"] = str(result["submission_id"]) if result["submission_id"] else None
        if result["paid_at"]:
            result["paid_at"] = result["paid_at"].isoformat()
        return jsonify(result)
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SeekReap Payment System — append this block into tier4_main.py
# (after the existing imports and before if __name__ == "__main__")
# ══════════════════════════════════════════════════════════════════════════════

# ── Additional imports (merge with existing imports at top of file) ───────────
import hmac
import hashlib

# ── Additional env vars (add to .env) ────────────────────────────────────────
# PAYSTACK_SECRET_KEY=sk_live_xxxx          (or sk_test_xxxx for testing)
# PAYFAST_MERCHANT_ID=xxxxx
# PAYFAST_MERCHANT_KEY=xxxxx
# PAYFAST_PASSPHRASE=xxxxx                  (optional but recommended)
# FRONTEND_URL=https://seekreap-frontend.onrender.com
# TIER4_INTERNAL=https://seekreap-tier-4-dev.fly.dev

PAYSTACK_SECRET  = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYFAST_MERCHANT_ID  = os.environ.get("PAYFAST_MERCHANT_ID", "")
PAYFAST_MERCHANT_KEY = os.environ.get("PAYFAST_MERCHANT_KEY", "")
PAYFAST_PASSPHRASE   = os.environ.get("PAYFAST_PASSPHRASE", "")
FRONTEND_URL     = os.environ.get("FRONTEND_URL", "https://seekreap-frontend.onrender.com")
TIER4_INTERNAL   = os.environ.get("TIER4_INTERNAL", "https://seekreap-tier-4-dev.fly.dev")

# Plan → amount in cents (ZAR)
PLAN_AMOUNTS = {
    "payg":    199,    # R1.99
    "creator": 999,    # R9.99/mo
    "studio":  2999,   # R29.99/mo
}


# ── DB: ensure payments table exists ─────────────────────────────────────────
def ensure_payments_table():
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                creator_id    TEXT NOT NULL,
                submission_id UUID,
                plan          TEXT NOT NULL,
                amount        INTEGER NOT NULL,
                currency      TEXT DEFAULT 'ZAR',
                gateway       TEXT NOT NULL,
                payment_ref   TEXT,
                status        TEXT DEFAULT 'pending',
                metadata      JSONB,
                created_at    TIMESTAMP DEFAULT NOW(),
                paid_at       TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_payment_ref
            ON payments(payment_ref)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_creator_id
            ON payments(creator_id)
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


# Call at startup
try:
    ensure_payments_table()
    print("[PAYMENT] payments table ready")
except Exception as e:
    print(f"[PAYMENT] table init warning: {e}")


# ── Gateway selector ──────────────────────────────────────────────────────────
def select_gateway(data):
    # All regions → Paystack (global). PayFast added as opt-in later.
    return "paystack"


# ── Paystack init ─────────────────────────────────────────────────────────────
def init_paystack(payment_id, data):
    payload = {
        "email":        data["email"],
        "amount":       data["amount"],   # in kobo/cents (integer)
        "reference":    str(payment_id),
        "callback_url": FRONTEND_URL + "/payment_success.html",
        "metadata": {
            "payment_id":  str(payment_id),
            "plan":        data["plan"],
            "creator_id":  data["creator_id"],
            "title":       data.get("title", ""),
            "cancel_action": FRONTEND_URL + "/certification_portal.html",
        }
    }
    try:
        r = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=15,
        )
        resp = r.json()
        if not resp.get("status"):
            return jsonify({"error": resp.get("message", "Paystack error")}), 502
        return jsonify({
            "gateway":           "paystack",
            "authorization_url": resp["data"]["authorization_url"],
            "access_code":       resp["data"]["access_code"],
            "reference":         resp["data"]["reference"],
        })
    except Exception as e:
        print(f"[PAYSTACK] init error: {e}")
        return jsonify({"error": "Payment gateway unavailable"}), 502


# ── PayFast init (secondary) ──────────────────────────────────────────────────
def init_payfast(payment_id, data):
    """
    PayFast uses a redirect form POST rather than an API call.
    Returns the fields the frontend should POST to PayFast.
    """
    import urllib.parse

    fields = {
        "merchant_id":   PAYFAST_MERCHANT_ID,
        "merchant_key":  PAYFAST_MERCHANT_KEY,
        "return_url":    FRONTEND_URL + "/payment_success.html",
        "cancel_url":    FRONTEND_URL + "/certification_portal.html",
        "notify_url":    TIER4_INTERNAL + "/api/payments/webhook/payfast",
        "m_payment_id":  str(payment_id),
        "amount":        f"{data['amount'] / 100:.2f}",
        "item_name":     f"SeekReap {data['plan'].title()} Plan",
        "email_address": data["email"],
    }

    # Generate signature
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}"
        for k, v in fields.items() if v
    )
    if PAYFAST_PASSPHRASE:
        sig_str += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE)}"
    fields["signature"] = hashlib.md5(sig_str.encode()).hexdigest()

    return jsonify({
        "gateway":    "payfast",
        "action_url": "https://www.payfast.co.za/eng/process",
        "fields":     fields,
    })


# ── Internal: trigger certification after payment ────────────────────────────
def trigger_certification(payment_row, pending_meta):
    """
    Called after payment is marked paid.
    Calls /api/certify internally using data stored at initiation time.
    """
    creator_id   = payment_row["creator_id"]
    plan         = payment_row["plan"]
    meta         = pending_meta or {}

    payload = {
        "creator_id":       creator_id,
        "email":            meta.get("email", ""),
        "title":            meta.get("title", "Untitled Work"),
        "work_type":        meta.get("work_type", "other"),
        "content_hash":     meta.get("content_hash", ""),
        "plan":             plan,
        "collaborators":    meta.get("collaborators", []),
        "ownership_split":  meta.get("ownership_split", {}),
        "artistic_name":    meta.get("artistic_name", ""),
        "payment_id":       str(payment_row["id"]),
    }

    try:
        r = requests.post(
            TIER4_INTERNAL + "/api/certify",
            json=payload,
            timeout=30,
        )
        data = r.json()
        print(f"[PAYMENT] triggered cert: submission={data.get('submission_id')} cert={data.get('cert_id')}")

        # Update payment row with submission_id
        conn = get_db()
        cur  = conn.cursor()
        try:
            cur.execute("""
                UPDATE payments SET submission_id = %s
                WHERE id = %s
            """, (data.get("submission_id"), str(payment_row["id"])))
            conn.commit()
        finally:
            cur.close()
            conn.close()

        return data
    except Exception as e:
        print(f"[PAYMENT] trigger_certification error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/payments/initiate")
def initiate_payment():
    """
    Frontend calls this instead of /api/certify for paid plans.
    Stores pending cert data, creates payment record, returns gateway URL.
    """
    body = request.get_json(force=True) or {}

    creator_id = (body.get("creator_id") or "").strip()
    plan       = (body.get("plan") or "free").lower().strip()
    email      = (body.get("email") or "").strip()
    # Amount is ALWAYS derived server-side from PLAN_AMOUNTS — never trust client
    # (client-provided amount field is intentionally ignored)

    if not creator_id:
        return jsonify({"error": "creator_id required"}), 400
    if plan == "free":
        return jsonify({"error": "Free plan does not require payment"}), 400
    if plan not in PLAN_AMOUNTS:
        return jsonify({"error": f"Unknown plan '{plan}'"}), 400
    if not email:
        return jsonify({"error": "email required"}), 400

    amount = PLAN_AMOUNTS[plan]  # server-authoritative; client value ignored

    # Store full pending cert metadata so webhook can trigger certification
    pending_meta = {
        "email":           email,
        "title":           body.get("title", "Untitled Work"),
        "work_type":       body.get("work_type", "other"),
        "content_hash":    body.get("content_hash", ""),
        "collaborators":   body.get("collaborators", []),
        "ownership_split": body.get("ownership_split", {}),
        "artistic_name":   body.get("artistic_name", ""),
    }

    gateway = select_gateway(body)

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO payments
                (creator_id, plan, amount, currency, gateway, status, metadata)
            VALUES (%s, %s, %s, 'ZAR', %s, 'pending', %s)
            RETURNING id
        """, (creator_id, plan, amount, gateway, Json(pending_meta)))
        payment_id = str(cur.fetchone()["id"])
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    data = {**body, "amount": amount, "email": email}

    if gateway == "paystack":
        return init_paystack(payment_id, data)
    if gateway == "payfast":
        return init_payfast(payment_id, data)

    return jsonify({"error": "No gateway available"}), 502


@app.post("/api/payments/webhook/paystack")
def paystack_webhook():
    """
    Paystack calls this after a successful charge.
    Verifies HMAC signature, marks payment paid, triggers certification.
    """
    raw_body = request.get_data()
    sig      = request.headers.get("X-Paystack-Signature", "")

    # Verify signature
    if PAYSTACK_SECRET:
        expected = hmac.new(
            PAYSTACK_SECRET.encode(),
            raw_body,
            hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            print("[PAYSTACK] webhook signature mismatch")
            return jsonify({"error": "Invalid signature"}), 400

    payload = request.get_json(force=True) or {}
    event   = payload.get("event")

    if event != "charge.success":
        return jsonify({"status": "ignored"}), 200

    ref           = payload["data"]["reference"]
    amount_webhook = int(payload["data"]["amount"])

    # ── FIX 2: Server-side Paystack verification (prevents spoofed webhooks) ──
    try:
        verify = requests.get(
            f"https://api.paystack.co/transaction/verify/{ref}",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET}"},
            timeout=15,
        )
        verify_data = verify.json()
        if not verify_data.get("status") or verify_data["data"]["status"] != "success":
            print(f"[PAYSTACK] verification failed for {ref}: {verify_data.get('message')}")
            return jsonify({"error": "Paystack verification failed"}), 400
        # Use the verified amount from Paystack, not the webhook payload
        amount_verified = int(verify_data["data"]["amount"])
    except Exception as e:
        print(f"[PAYSTACK] verify call error: {e}")
        return jsonify({"error": "Could not verify transaction"}), 502

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM payments WHERE id = %s", (ref,))
        payment = cur.fetchone()

        if not payment:
            print(f"[PAYSTACK] webhook: payment {ref} not found")
            return jsonify({"error": "payment not found"}), 404

        # ── FIX 1: Amount verification (prevents underpayment attacks) ────────
        if int(payment["amount"]) != amount_verified:
            print(f"[PAYSTACK] amount mismatch: expected {payment['amount']} got {amount_verified}")
            return jsonify({"error": "Amount mismatch"}), 400

        # ── FIX 3: Atomic idempotency — WHERE status != 'paid' prevents races ─
        cur.execute("""
            UPDATE payments
            SET status = 'paid', paid_at = NOW(), payment_ref = %s,
                metadata = COALESCE(metadata, '{}'::jsonb) || %s
            WHERE id = %s AND status != 'paid'
            RETURNING *
        """, (ref, Json({"paystack_event": payload}), ref))
        paid_row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[PAYSTACK] webhook DB error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    # paid_row is None if another webhook already processed this (race condition)
    if not paid_row:
        print(f"[PAYSTACK] webhook: {ref} already processed (concurrent duplicate ignored)")
        return jsonify({"status": "already_processed"}), 200

    # Trigger certification outside DB transaction.
    # metadata contains both init-time cert fields AND the merged paystack_event —
    # trigger_certification only reads cert fields, so this is safe as-is.
    pending_meta = {k: v for k, v in (paid_row.get("metadata") or {}).items()
                    if k not in ("paystack_event", "payfast_itn")}
    trigger_certification(paid_row, pending_meta)

    return jsonify({"status": "ok"}), 200


@app.post("/api/payments/webhook/payfast")
def payfast_webhook():
    """
    PayFast ITN (Instant Transaction Notification) handler.
    """
    import urllib.parse

    data = request.form.to_dict()
    payment_id = data.get("m_payment_id")
    pf_status  = data.get("payment_status")

    # Verify signature
    sig_received = data.pop("signature", "")
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}"
        for k, v in data.items() if v
    )
    if PAYFAST_PASSPHRASE:
        sig_str += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE)}"
    expected_sig = hashlib.md5(sig_str.encode()).hexdigest()

    if expected_sig != sig_received:
        print("[PAYFAST] ITN signature mismatch")
        return "INVALID", 400

    if pf_status != "COMPLETE":
        return "ok", 200

    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment:
            return "ok", 200

        # ── FIX 3: Atomic idempotency ─────────────────────────────────────────
        # ── FIX 4: Store gateway confirmation metadata ─────────────────────────
        cur.execute("""
            UPDATE payments
            SET status = 'paid', paid_at = NOW(), payment_ref = %s,
                metadata = COALESCE(metadata, '{}'::jsonb) || %s
            WHERE id = %s AND status != 'paid'
            RETURNING *
        """, (data.get("pf_payment_id"), Json({"payfast_itn": data}), payment_id))
        paid_row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        return str(e), 500
    finally:
        cur.close()
        conn.close()

    if not paid_row:
        print(f"[PAYFAST] ITN: {payment_id} already processed (concurrent duplicate ignored)")
        return "ok", 200

    pending_meta = {k: v for k, v in (paid_row.get("metadata") or {}).items()
                    if k not in ("paystack_event", "payfast_itn")}
    trigger_certification(paid_row, pending_meta)
    return "ok", 200


@app.get("/api/payments/<payment_id>")
def get_payment_status(payment_id):
    """
    Frontend polls this to get payment + submission status.
    Returns payment status and, once triggered, the submission/cert IDs.
    """
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT p.id, p.status, p.plan, p.paid_at, p.submission_id,
                   p.gateway, p.payment_ref,
                   s.cert_id, s.status as cert_status
            FROM payments p
            LEFT JOIN submissions s ON s.id = p.submission_id
            WHERE p.id = %s
        """, (payment_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        result = dict(row)
        result["id"]           = str(result["id"])
        result["submission_id"] = str(result["submission_id"]) if result["submission_id"] else None
        if result["paid_at"]:
            result["paid_at"] = result["paid_at"].isoformat()
        return jsonify(result)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
# Add these endpoints to tier4_main.py

import secrets
from datetime import datetime
import hmac
import hashlib

def generate_invite_token():
    """Generate secure 64-character token for collaborator invites"""
    return secrets.token_urlsafe(32)  # 64 chars total

# ──────────────────────────────────────────────────────────────
# POST /api/certify/invites - Create invites for collaborators
# ──────────────────────────────────────────────────────────────

@app.post("/api/certify/invites")
def create_collaborator_invites():
    """Create invite records for all collaborators in a certification"""
    body = request.get_json(force=True) or {}
    
    collaborators = body.get("collaborators", [])
    certificate_id = body.get("certificate_id")
    invited_by = body.get("creator_id")
    work_title = body.get("work_title", "Untitled Work")
    
    if not collaborators:
        return jsonify({"error": "No collaborators provided"}), 400
    
    if not certificate_id:
        return jsonify({"error": "certificate_id required"}), 400
    
    if not invited_by:
        return jsonify({"error": "creator_id required"}), 400
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    invites_created = []
    
    try:
        for collab in collaborators:
            token = generate_invite_token()
            
            cur.execute("""
                INSERT INTO collaboration_invites 
                (email, full_name, artistic_name, title, gender, country,
                 ownership_title, split, certificate_id, invited_by, token, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id, token
            """, (
                collab.get("email"),
                collab.get("fullName"),
                collab.get("artisticName"),
                collab.get("title"),
                collab.get("gender"),
                collab.get("country"),
                collab.get("ownershipTitle"),
                collab.get("split"),
                certificate_id,
                invited_by,
                token
            ))
            
            result = cur.fetchone()
            invites_created.append({
                "id": result["id"],
                "token": result["token"],
                "email": collab.get("email"),
                "artistic_name": collab.get("artisticName")
            })
            
            # TODO: Send email here (will implement next)
            print(f"📧 Would send invite to {collab.get('email')} with token {token[:16]}...")
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "invites": invites_created,
            "message": f"Created {len(invites_created)} collaborator invites"
        }), 201
        
    except Exception as e:
        conn.rollback()
        print(f"Error creating invites: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# GET /api/invite?token=XXX - Fetch invite details
# ──────────────────────────────────────────────────────────────

@app.get("/api/invite")
def get_invite():
    """Fetch invite details for pre-filling signup form"""
    token = request.args.get('token')
    
    if not token:
        return jsonify({"error": "token required"}), 400
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Critical: Only return pending, non-expired invites
        cur.execute("""
            SELECT email, full_name, artistic_name, title, gender, country,
                   ownership_title, split, certificate_id, invited_by, status
            FROM collaboration_invites
            WHERE token = %s 
              AND status = 'pending' 
              AND expires_at > NOW()
        """, (token,))
        
        invite = cur.fetchone()
        
        if not invite:
            return jsonify({
                "valid": False,
                "error": "Invite not found, expired, or already accepted"
            }), 404
        
        return jsonify({
            "valid": True,
            "email": invite["email"],
            "full_name": invite["full_name"],
            "artistic_name": invite["artistic_name"],
            "title": invite["title"],
            "gender": invite["gender"],
            "country": invite["country"],
            "ownership_title": invite["ownership_title"],
            "split": invite["split"],
            "certificate_id": invite["certificate_id"]
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# POST /api/invite/accept - Accept an invite after signup
# ──────────────────────────────────────────────────────────────

@app.post("/api/invite/accept")
def accept_invite():
    """
    Accept a collaborator invite after user signs up.
    Token is the ONLY lookup key - email is NOT used for security.
    """
    body = request.get_json(force=True) or {}
    
    token = body.get("token")
    user_id = body.get("user_id")
    user_email = body.get("email")  # For verification only
    
    if not token or not user_id:
        return jsonify({"error": "token and user_id required"}), 400
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Token is the ONLY lookup key (not email)
        cur.execute("""
            SELECT * FROM collaboration_invites
            WHERE token = %s AND status = 'pending' AND expires_at > NOW()
        """, (token,))
        
        invite = cur.fetchone()
        
        if not invite:
            return jsonify({"error": "Invalid or expired invite"}), 404
        
        # Verify email matches (security check)
        if user_email and invite["email"].lower() != user_email.lower():
            return jsonify({"error": "Email does not match invite"}), 403
        
        # Update invite status
        cur.execute("""
            UPDATE collaboration_invites
            SET status = 'accepted', accepted_at = NOW()
            WHERE token = %s
            RETURNING certificate_id, invited_by, split, ownership_title
        """, (token,))
        
        updated = cur.fetchone()
        
        # Create collaborator record
        cur.execute("""
            INSERT INTO collaborators (user_id, certificate_id, ownership_title, split, role)
            VALUES (%s, %s, %s, %s, 'co-owner')
            ON CONFLICT (user_id, certificate_id) DO NOTHING
        """, (
            user_id,
            updated["certificate_id"],
            updated["ownership_title"],
            updated["split"]
        ))
        
        # Update profile if we have data
        if invite.get("full_name") or invite.get("artistic_name"):
            cur.execute("""
                INSERT INTO profiles (id, full_name, artistic_name, title, gender, country)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    artistic_name = EXCLUDED.artistic_name,
                    title = EXCLUDED.title,
                    gender = EXCLUDED.gender,
                    country = EXCLUDED.country,
                    updated_at = NOW()
            """, (
                user_id,
                invite.get("full_name"),
                invite.get("artistic_name"),
                invite.get("title"),
                invite.get("gender"),
                invite.get("country")
            ))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "message": "Invite accepted successfully",
            "certificate_id": updated["certificate_id"]
        }), 200
        
    except Exception as e:
        conn.rollback()
        print(f"Error accepting invite: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()
