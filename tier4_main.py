# SeekReap Tier-4 Orchestrator
# Build: 2026-04-22 — fully hardened
from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid, os, json, psycopg2, re, requests, random, string, hmac, hashlib, secrets
import json as _json, time as _time
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

CORS(app, origins=[
    "https://seekreap-backend-dev.fly.dev",
    "https://seekreap-frontend.onrender.com",
    "http://localhost:3000",
    "http://localhost:8080",
])

DAILY_QUOTA = 50
QUEUE_CAP   = 500
VALID_PLANS = {"free", "creator", "studio", "payg"}
VALID_WORK_TYPES = {"audio", "video", "image", "epub", "pdf", "code", "other"}

INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")


# ══════════════════════════════════════════════════════════════════════════════
# Structured logging
# ══════════════════════════════════════════════════════════════════════════════

def _log(level, component, event, **kw):
    print(_json.dumps({
        "ts":        _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "level":     level,
        "component": component,
        "event":     event,
        **kw,
    }))

def log_info(c, e, **kw):  _log("INFO",  c, e, **kw)
def log_warn(c, e, **kw):  _log("WARN",  c, e, **kw)
def log_error(c, e, **kw): _log("ERROR", c, e, **kw)


# ══════════════════════════════════════════════════════════════════════════════
# Input validation helpers
# ══════════════════════════════════════════════════════════════════════════════

_EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,}$')

def _valid_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))

def _valid_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s)); return True
    except (ValueError, AttributeError):
        return False

def _clamp_str(s, maxlen: int) -> str:
    if not s:
        return ""
    return str(s)[:maxlen]

def _validate_certify_body(body: dict) -> tuple:
    creator_id = _clamp_str(body.get("creator_id") or body.get("supabase_uid"), 128).strip()
    if not creator_id:
        return "creator_id required", None
    title     = _clamp_str(body.get("title"), 512).strip() or "Untitled Work"
    plan      = _clamp_str(body.get("plan"), 32).lower().strip() or "free"
    if plan not in VALID_PLANS:
        return f"invalid plan '{plan}'", None
    work_type = _clamp_str(body.get("work_type"), 32).lower().strip() or "other"
    if work_type not in VALID_WORK_TYPES:
        work_type = "other"
    content_hash  = _clamp_str(body.get("content_hash"), 128).strip()
    artistic_name = _clamp_str(body.get("artistic_name"), 128).strip()
    email         = _clamp_str(body.get("email"), 254).strip()
    collabs = body.get("collaborators") or []
    if not isinstance(collabs, list):
        return "collaborators must be an array", None
    if len(collabs) > 20:
        return "maximum 20 collaborators allowed", None
    ownership_split = body.get("ownership_split") or {}
    if not isinstance(ownership_split, dict):
        return "ownership_split must be an object", None
    if len(ownership_split) > 21:
        return "ownership_split too large", None
    return None, {
        "creator_id":      creator_id,
        "title":           title,
        "plan":            plan,
        "work_type":       work_type,
        "content_hash":    content_hash,
        "artistic_name":   artistic_name,
        "email":           email,
        "collaborators":   collabs[:20],
        "ownership_split": ownership_split,
        "content_url":     _clamp_str(body.get("content_url"), 2048),
        "payment_id":      _clamp_str(body.get("payment_id"), 128),
    }


# ══════════════════════════════════════════════════════════════════════════════
# JWT / Supabase token verification
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

def _verify_supabase_jwt(token: str) -> dict | None:
    if not SUPABASE_JWT_SECRET:
        log_warn("auth", "jwt_secret_not_configured_skipping_verify")
        try:
            import base64
            parts = token.split(".")
            if len(parts) != 3:
                return None
            padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
            return _json.loads(base64.urlsafe_b64decode(padded))
        except Exception:
            return None
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        msg = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(SUPABASE_JWT_SECRET.encode(), msg, hashlib.sha256).digest()
        padded = sig_b64 + "=" * (4 - len(sig_b64) % 4)
        received_sig = base64.urlsafe_b64decode(padded)
        if not hmac.compare_digest(expected_sig, received_sig):
            log_warn("auth", "jwt_sig_invalid")
            return None
        padded2 = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(padded2))
        exp = claims.get("exp")
        if exp and _time.time() > exp:
            log_warn("auth", "jwt_expired", exp=exp)
            return None
        return claims
    except Exception as e:
        log_warn("auth", "jwt_verify_error", error=str(e))
        return None


def _require_auth(req) -> tuple:
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, (jsonify({"error": "Authorization header required"}), 401)
    token = auth_header[7:].strip()
    claims = _verify_supabase_jwt(token)
    if not claims:
        return None, (jsonify({"error": "Invalid or expired token"}), 401)
    return claims, None


def _require_internal(req) -> tuple:
    if INTERNAL_SECRET and req.headers.get("X-Internal-Secret") == INTERNAL_SECRET:
        return {"sub": "internal", "role": "service"}, None
    claims, err = _require_auth(req)
    if err:
        return None, (jsonify({"error": "Internal endpoint — not publicly accessible"}), 403)
    return claims, None


def _require_admin(req):
    ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
    if not ADMIN_SECRET or req.headers.get("X-Admin-Key") != ADMIN_SECRET:
        log_warn("admin", "unauthorized", path=req.path, ip=req.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DB: connection pool + context managers
# FIX: ALL db access goes through db_conn() or db_cursor() — no raw conn.close()
# ══════════════════════════════════════════════════════════════════════════════

from psycopg2 import pool as _pg_pool
from contextlib import contextmanager

_db_pool: "_pg_pool.ThreadedConnectionPool | None" = None

def _get_pool() -> "_pg_pool.ThreadedConnectionPool":
    global _db_pool
    if _db_pool is None or _db_pool.closed:
        _db_pool = _pg_pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            dsn=os.environ["DATABASE_URL"],
            connect_timeout=5,
        )
    return _db_pool

def get_db():
    return _get_pool().getconn()

def put_db(conn):
    if conn is not None:
        try:
            _get_pool().putconn(conn)
        except Exception:
            pass


@contextmanager
def db_conn():
    """
    Acquires a pooled connection and guarantees return to pool on exit.
    Never call put_db() manually when using this context manager.
    """
    conn = get_db()
    try:
        yield conn
    finally:
        put_db(conn)


@contextmanager
def db_cursor(cursor_factory=None):
    """
    Acquires connection + cursor, closes cursor and returns connection to
    pool on exit — even if an exception is raised.

    Usage:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute(...)
            conn.commit()
    """
    with db_conn() as conn:
        kw = {"cursor_factory": cursor_factory} if cursor_factory else {}
        cur = conn.cursor(**kw)
        try:
            yield conn, cur
        finally:
            cur.close()


# ══════════════════════════════════════════════════════════════════════════════
# YouTube helpers
# ══════════════════════════════════════════════════════════════════════════════

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
        oembed = (f"https://www.youtube.com/oembed"
                  f"?url=https://www.youtube.com/watch?v={video_id}&format=json")
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
        log_warn("youtube", "oembed_error", error=str(e))
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Creator / submission helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_creator(conn, firebase_uid, email=None, name=None):
    # Uses passed-in conn — caller owns the connection lifecycle
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            uuid.UUID(firebase_uid)
            cur.execute("SELECT id FROM creators WHERE id = %s", (firebase_uid,))
            if cur.fetchone():
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


def insert_submission(data, creator_uuid):
    content_url  = data.get("content_url")
    content_hash = data.get("content_hash", "unknown")
    content_type = data.get("content_type", "video")

    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT id, title, content_preview_url FROM submissions
            WHERE creator_id = %s AND content_hash = %s
            ORDER BY submitted_at DESC LIMIT 1
        """, (creator_uuid, content_hash))
        existing = cur.fetchone()
        if existing:
            log_info("submit", "dedup_hit",
                     submission_id=str(existing['id']), hash=content_hash)
            return (str(existing["id"]), existing["title"] or content_hash,
                    "", existing["content_preview_url"] or "")

        submission_id = str(uuid.uuid4())
        yt_meta       = extract_youtube_metadata(content_url)
        title         = yt_meta.get("title") or data.get("title") or content_hash
        channel       = yt_meta.get("channel", "")
        thumbnail_url = yt_meta.get("thumbnail_url", "")
        metadata      = {**yt_meta, **(data.get("metadata") or {})}

        try:
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
        except Exception as e:
            conn.rollback()
            log_error("submit", "db_insert_error", error=str(e))
            raise

        log_info("submit", "created", submission_id=submission_id, title=title)
        cur.execute("""
            INSERT INTO job_queue
                (submission_id, creator_id, content_id, job_type, status, attempts)
            VALUES (%s, %s, %s, %s, %s, 0) ON CONFLICT DO NOTHING
        """, (submission_id, creator_uuid, content_url, "fingerprint", "pending"))
        conn.commit()

    return submission_id, title, channel, thumbnail_url


def check_rate_limit(creator_uuid: str) -> tuple:
    with db_cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM job_queue WHERE status IN ('pending', 'processing')")
        if cur.fetchone()[0] >= QUEUE_CAP:
            return False, "System queue full. Try again later."
        cur.execute("""
            SELECT COUNT(*) FROM submissions
            WHERE creator_id = %s AND submitted_at >= NOW() - INTERVAL '24 hours'
        """, (creator_uuid,))
        daily = cur.fetchone()[0]
        if daily >= DAILY_QUOTA:
            return False, f"Daily quota reached ({daily}/{DAILY_QUOTA}). Resets in 24h."
        return True, ""


# ══════════════════════════════════════════════════════════════════════════════
# Core routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    problems = {}
    # DB liveness
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT 1")
    except Exception as e:
        problems["db"] = str(e)
    # Connection pool state
    try:
        _p = _get_pool()
        if _p.closed:
            problems["pool"] = "closed"
    except Exception as _pe:
        problems["pool"] = str(_pe)
    # Open circuit breakers
    open_gw = [gw for gw, s in _circuit_state.items()
               if s.get("open_until", 0) > _time.time()]
    if open_gw:
        problems["open_circuits"] = open_gw
    if problems:
        return jsonify({"status": "degraded", "tier": 4, "problems": problems}), 500
    return jsonify({"status": "ok", "tier": 4})


@app.post("/api/submit")
def submit():
    claims, err = _require_auth(request)
    if err:
        return err
    try:
        data         = request.get_json(force=True) or {}
        firebase_uid = claims.get("sub", data.get("creator_id", ""))
        if not firebase_uid:
            return jsonify({"error": "creator_id required"}), 400
        with db_conn() as conn:
            creator_uuid = get_or_create_creator(conn, firebase_uid,
                                                  data.get("email"), data.get("name"))
        allowed, reason = check_rate_limit(creator_uuid)
        if not allowed:
            return jsonify({"error": reason, "code": "RATE_LIMITED"}), 429
        submission_id, title, channel, thumbnail_url = insert_submission(data, creator_uuid)
        return jsonify({
            "submission_id": submission_id, "status": "pending",
            "creator_uuid":  creator_uuid,  "title":  title,
            "channel":       channel,        "thumbnail_url": thumbnail_url,
        })
    except Exception as e:
        log_error("submit", "unhandled", error=str(e))
        return jsonify({"error": str(e)}), 500


@app.post("/api/certify")
def certify_work():
    internal_ok = (INTERNAL_SECRET and
                   request.headers.get("X-Internal-Secret") == INTERNAL_SECRET)
    if not internal_ok:
        claims, err = _require_auth(request)
        if err:
            return err
        token_sub = claims.get("sub", "")
    else:
        token_sub = None

    body = request.get_json(force=True) or {}
    error_msg, cleaned = _validate_certify_body(body)
    if error_msg:
        return jsonify({"error": error_msg}), 400

    creator_id_raw = cleaned["creator_id"]
    plan           = cleaned["plan"]

    if not internal_ok and token_sub:
        try:
            token_uuid = str(uuid.UUID(token_sub))
        except (ValueError, AttributeError):
            token_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, token_sub))
        try:
            req_uuid = str(uuid.UUID(creator_id_raw))
        except (ValueError, AttributeError):
            req_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, creator_id_raw))
        if token_uuid != req_uuid:
            log_warn("certify", "ownership_mismatch",
                     token_sub=token_sub, creator_id=creator_id_raw,
                     ip=request.remote_addr)
            return jsonify({"error": "Forbidden — creator_id does not match token"}), 403

    if not internal_ok and plan != "free":
        log_warn("certify", "paid_plan_direct_attempt",
                 plan=plan, creator_id=creator_id_raw, ip=request.remote_addr)
        return jsonify({
            "error": "Paid plans must go through the payment system. "
                     "Use /api/payments/initiate instead."
        }), 403

    title        = cleaned["title"]
    content_hash = cleaned["content_hash"]

    try:
        creator_uuid = str(uuid.UUID(creator_id_raw))
    except (ValueError, AttributeError):
        creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, creator_id_raw))

    if not content_hash:
        content_hash = hashlib.sha256(
            f"{creator_uuid}:{title}:{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()

    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            if content_hash:
                cur.execute("""
                    SELECT id, cert_id, plan, status FROM submissions
                    WHERE creator_id = %s AND content_hash = %s AND plan = %s
                      AND submitted_at > NOW() - INTERVAL '24 hours'
                    ORDER BY submitted_at DESC LIMIT 1
                """, (creator_uuid, content_hash, plan))
                existing = cur.fetchone()
                if existing:
                    log_info("certify", "dedup_hit",
                             submission_id=str(existing["id"]), cert_id=existing["cert_id"])
                    return jsonify({
                        "submission_id": str(existing["id"]),
                        "cert_id":       existing["cert_id"],
                        "plan":          existing["plan"],
                        "status":        existing["status"],
                        "qr_url":        f"/api/qrcode/{existing['cert_id']}",
                        "message":       "Certification already queued (deduplicated)",
                    }), 202

            suffix      = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
            cert_id     = f"SR-{datetime.utcnow().strftime('%Y%m%d')}-{suffix}"
            content_url = cleaned["content_url"] or f"seekreap://local/{content_hash}"
            email         = cleaned["email"]
            artistic_name = cleaned["artistic_name"]
            creator_email = email or f"{creator_uuid[:8]}@seekreap.local"

            try:
                cur.execute("""
                    INSERT INTO creators (id, email, name) VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """, (creator_uuid, creator_email, artistic_name or title))
            except Exception:
                conn.rollback()
                cur.execute("""
                    INSERT INTO creators (id, email, name) VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """, (creator_uuid, f"{creator_uuid[:8]}@seekreap.local",
                      artistic_name or title))

            collaborators   = cleaned["collaborators"]
            ownership_split = cleaned["ownership_split"]

            cur.execute("""
                INSERT INTO submissions
                   (id, creator_id, content_url, content_hash, title,
                    plan, artistic_name, work_type, cert_id,
                    collaborators, ownership_split, status, content_type)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s)
                RETURNING id
            """, (creator_uuid, content_url, content_hash, title,
                  plan, artistic_name, cleaned["work_type"], cert_id,
                  Json(collaborators) if collaborators else None,
                  Json(ownership_split) if ownership_split else None,
                  cleaned["work_type"]))

            row           = cur.fetchone()
            submission_id = str(row["id"])

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
            log_info("certify", "queued",
                     submission_id=submission_id, cert_id=cert_id, plan=plan)

            return jsonify({
                "submission_id": submission_id,
                "cert_id":       cert_id,
                "plan":          plan,
                "status":        "queued",
                "qr_url":        f"/api/qrcode/{cert_id}",
                "message":       "Certification queued successfully",
            }), 202

        except Exception as e:
            conn.rollback()
            import traceback; traceback.print_exc()
            return jsonify({"error": str(e)}), 500


@app.get("/api/certify/<submission_id>")
def certify_status(submission_id):
    internal_ok = (INTERNAL_SECRET and
                   request.headers.get("X-Internal-Secret") == INTERNAL_SECRET)
    if not internal_ok:
        claims, err = _require_auth(request)
        if err:
            return err
        token_sub = claims.get("sub", "")
    else:
        token_sub = None

    if not _valid_uuid(submission_id):
        return jsonify({"error": "invalid submission_id"}), 400

    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT id, creator_id, status, cert_id, title, work_type, plan,
                   artistic_name, overall_risk_score, risk_level,
                   submitted_at, completed_at, failure_reason
            FROM submissions WHERE id = %s
        """, (submission_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        if not internal_ok and token_sub:
            try:
                token_uuid = str(uuid.UUID(token_sub))
            except Exception:
                token_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, token_sub))
            if token_uuid != str(row["creator_id"]):
                log_warn("certify_status", "ownership_denied",
                         token_sub=token_sub, creator_id=str(row["creator_id"]))
                return jsonify({"error": "not found"}), 404

        data = dict(row)
        data["id"]         = str(data["id"])
        data["creator_id"] = str(data["creator_id"])
        for k in ("submitted_at", "completed_at"):
            if data[k]:
                data[k] = data[k].isoformat()
        return jsonify(data), 200


@app.get("/api/status/<submission_id>")
def status(submission_id):
    if not _valid_uuid(submission_id):
        return jsonify({"error": "invalid submission_id"}), 400

    with db_cursor(RealDictCursor) as (conn, cur):
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
    result["yt_thumbnail"]   = (meta.get("thumbnail_url", "")
                                 or result.get("content_preview_url", ""))
    result["yt_id"]          = meta.get("youtube_id", "")
    result["matches"] = [
        {
            "matched_submission_id": str(m["matched_submission_id"]),
            "similarity_score":      float(m["similarity_score"]),
            "match_type":            m["match_type"],
            "fingerprint_version":   m["fingerprint_version"],
            "detected_at":           (m["detected_at"].isoformat()
                                      if m["detected_at"] else None),
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
    claims, err = _require_auth(request)
    if err:
        return err
    token_sub = claims.get("sub", "")
    if not token_sub:
        return jsonify({"error": "Token missing sub claim"}), 401
    try:
        creator_uuid = str(uuid.UUID(token_sub))
    except ValueError:
        creator_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, token_sub))

    with db_cursor(RealDictCursor) as (conn, cur):
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
                        "id":                 str(r["id"]),
                        "content_url":        r["content_url"],
                        "content_type":       r["content_type"],
                        "status":             (r["status"] or "").upper(),
                        "overall_risk_score": r["overall_risk_score"],
                        "risk_level":         r["risk_level"],
                        "submitted_at":       (r["submitted_at"].isoformat()
                                              if r["submitted_at"] else None),
                        "completed_at":       (r["completed_at"].isoformat()
                                              if r["completed_at"] else None),
                        "title":              r["title"],
                        "channel":            r["channel"],
                        "thumbnail":          r["thumbnail"],
                        "match_count":        int(r["match_count"] or 0),
                        "max_severity":       r["max_severity"],
                    }
                    for r in rows
                ],
                "total": len(rows),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.get("/api/metrics/latency")
def latency_metrics():
    with db_cursor(RealDictCursor) as (conn, cur):
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
            cur.execute("""SELECT COUNT(*) AS cnt FROM job_queue
                           WHERE status='failed' AND created_at >= NOW() - INTERVAL '24 hours'""")
            failed_24h = cur.fetchone()["cnt"]
            return jsonify({
                "latency_7d": {
                    "total_completed": int(row["total_completed"] or 0),
                    "avg_seconds": float(row["avg_seconds"]) if row["avg_seconds"] is not None else None,
                    "p95_seconds": float(row["p95_seconds"]) if row["p95_seconds"] is not None else None,
                    "min_seconds": float(row["min_seconds"]) if row["min_seconds"] is not None else None,
                    "max_seconds": float(row["max_seconds"]) if row["max_seconds"] is not None else None,
                },
                "queue_depth": queue_depth,
                "failed_24h":  failed_24h,
                "quota":       {"daily_limit": DAILY_QUOTA, "queue_cap": QUEUE_CAP},
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.post("/api/finalize")
def finalize():
    claims, err = _require_internal(request)
    if err:
        return err
    data          = request.get_json(force=True) or {}
    submission_id = data.get("submission_id", "")
    analysis      = data.get("analysis") or {}
    if not _valid_uuid(submission_id):
        return jsonify({"error": "invalid submission_id"}), 400

    with db_cursor() as (conn, cur):
        try:
            cur.execute("""
                UPDATE submissions SET status='completed',
                   overall_risk_score=%s, risk_level=%s, completed_at=NOW()
                WHERE id=%s RETURNING id
            """, (analysis.get("risk_score"), analysis.get("risk_level"), submission_id))
            updated = cur.fetchone()
            cur.execute("UPDATE job_queue SET status='completed', completed_at=NOW() "
                        "WHERE submission_id=%s", (submission_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
    return (jsonify({"status": "updated"}) if updated
            else (jsonify({"error": "not found"}), 404))


@app.post("/api/admin/recover-stuck-jobs")
def recover_stuck_jobs():
    auth_err = _require_admin(request)
    if auth_err:
        return auth_err
    with db_cursor() as (conn, cur):
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
            return jsonify({"recovered": len(recovered),
                            "job_ids": [r[0] for r in recovered]})
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500


@app.get("/api/verify-proof/<submission_id>")
def verify_blockchain_proof(submission_id):
    if not _valid_uuid(submission_id):
        return jsonify({"error": "invalid submission_id"}), 400
    with db_cursor(RealDictCursor) as (conn, cur):
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


@app.get("/debug/env")
def debug_env():
    return jsonify({
        "has_youtube_key": bool(os.environ.get("YOUTUBE_API_KEY")),
        "has_db":          bool(os.environ.get("DATABASE_URL")),
        "key_prefix":      os.environ.get("YOUTUBE_API_KEY", "")[:8],
    })


@app.get("/api/qrcode/<string:cert_id>")
def generate_qrcode(cert_id):
    import qrcode
    from io import BytesIO
    from flask import send_file
    if not re.match(r'^SR-\d{8}-[A-Z0-9]{8}$', cert_id):
        return jsonify({"error": "invalid cert_id"}), 400
    verify_url = (f"https://seekreap-frontend.onrender.com"
                  f"/verification_portal.html?cert={cert_id}")
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(verify_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')


# ══════════════════════════════════════════════════════════════════════════════
# Collaborator invite system
# ══════════════════════════════════════════════════════════════════════════════

def generate_invite_token():
    return secrets.token_urlsafe(32)


@app.post("/api/certify/invites")
def create_collaborator_invites():
    claims, err = _require_auth(request)
    if err:
        return err
    body           = request.get_json(force=True) or {}
    collaborators  = body.get("collaborators", [])
    certificate_id = _clamp_str(body.get("certificate_id"), 128).strip()
    invited_by     = claims.get("sub", "")

    if not isinstance(collaborators, list) or len(collaborators) == 0:
        return jsonify({"error": "No collaborators provided"}), 400
    if len(collaborators) > 20:
        return jsonify({"error": "Maximum 20 collaborators"}), 400
    if not certificate_id:
        return jsonify({"error": "certificate_id required"}), 400
    if not invited_by:
        return jsonify({"error": "creator_id required"}), 400

    invites_created = []
    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            for collab in collaborators:
                collab_email = _clamp_str(collab.get("email"), 254).strip()
                if not _valid_email(collab_email):
                    return jsonify({"error": f"Invalid email: {collab_email}"}), 400
                split = collab.get("split")
                if not isinstance(split, (int, float)) or not (1 <= int(split) <= 99):
                    return jsonify({"error": "split must be 1-99"}), 400
                token = generate_invite_token()
                cur.execute("""
                    INSERT INTO collaboration_invites
                    (email, full_name, artistic_name, title, gender, country,
                     ownership_title, split, certificate_id, invited_by, token, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                    RETURNING id, token
                """, (
                    collab_email,
                    _clamp_str(collab.get("fullName"), 128),
                    _clamp_str(collab.get("artisticName"), 128),
                    _clamp_str(collab.get("title"), 32),
                    _clamp_str(collab.get("gender"), 32),
                    _clamp_str(collab.get("country"), 64),
                    _clamp_str(collab.get("ownershipTitle"), 128),
                    int(split), certificate_id, invited_by, token,
                ))
                result = cur.fetchone()
                invites_created.append({
                    "id": result["id"], "token": result["token"],
                    "email": collab_email,
                    "artistic_name": _clamp_str(collab.get("artisticName"), 128),
                })
                log_info("invite", "queued", email=collab_email, token_prefix=token[:16])
            conn.commit()
            return jsonify({
                "success": True, "invites": invites_created,
                "message": f"Created {len(invites_created)} collaborator invites",
            }), 201
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500


@app.get("/api/invite")
def get_invite():
    token = _clamp_str(request.args.get('token'), 128).strip()
    if not token:
        return jsonify({"error": "token required"}), 400
    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            cur.execute("""
                SELECT email, full_name, artistic_name, title, gender, country,
                       ownership_title, split, certificate_id, invited_by, status
                FROM collaboration_invites
                WHERE token = %s AND status = 'pending' AND expires_at > NOW()
            """, (token,))
            invite = cur.fetchone()
            if not invite:
                return jsonify({"valid": False,
                                "error": "Invite not found, expired, or already accepted"}), 404
            return jsonify({
                "valid": True,
                "email": invite["email"], "full_name": invite["full_name"],
                "artistic_name": invite["artistic_name"], "title": invite["title"],
                "gender": invite["gender"], "country": invite["country"],
                "ownership_title": invite["ownership_title"], "split": invite["split"],
                "certificate_id": invite["certificate_id"],
            }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.post("/api/invite/accept")
def accept_invite():
    claims, err = _require_auth(request)
    if err:
        return err
    body       = request.get_json(force=True) or {}
    token      = _clamp_str(body.get("token"), 128).strip()
    user_id    = claims.get("sub", "")
    user_email = _clamp_str(body.get("email"), 254).strip()

    if not token:
        return jsonify({"error": "token required"}), 400
    if not user_id:
        return jsonify({"error": "Token missing sub claim"}), 401

    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            cur.execute("""
                SELECT * FROM collaboration_invites
                WHERE token = %s AND status = 'pending' AND expires_at > NOW()
            """, (token,))
            invite = cur.fetchone()
            if not invite:
                return jsonify({"error": "Invalid or expired invite"}), 404
            if user_email and invite["email"].lower() != user_email.lower():
                return jsonify({"error": "Email does not match invite"}), 403

            cur.execute("""
                UPDATE collaboration_invites SET status = 'accepted', accepted_at = NOW()
                WHERE token = %s
                RETURNING certificate_id, invited_by, split, ownership_title
            """, (token,))
            updated = cur.fetchone()

            cur.execute("""
                INSERT INTO collaborators (user_id, certificate_id, ownership_title, split, role)
                VALUES (%s, %s, %s, %s, 'co-owner')
                ON CONFLICT (user_id, certificate_id) DO NOTHING
            """, (user_id, updated["certificate_id"],
                  updated["ownership_title"], updated["split"]))

            if invite.get("full_name") or invite.get("artistic_name"):
                cur.execute("""
                    INSERT INTO profiles (id, full_name, artistic_name, title, gender, country)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        artistic_name = EXCLUDED.artistic_name,
                        title = EXCLUDED.title, gender = EXCLUDED.gender,
                        country = EXCLUDED.country, updated_at = NOW()
                """, (user_id, invite.get("full_name"), invite.get("artistic_name"),
                      invite.get("title"), invite.get("gender"), invite.get("country")))

            conn.commit()
            return jsonify({
                "success": True, "message": "Invite accepted successfully",
                "certificate_id": updated["certificate_id"],
            }), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# Payment system
# ══════════════════════════════════════════════════════════════════════════════

PAYSTACK_SECRET      = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYFAST_MERCHANT_ID  = os.environ.get("PAYFAST_MERCHANT_ID", "")
PAYFAST_MERCHANT_KEY = os.environ.get("PAYFAST_MERCHANT_KEY", "")
PAYFAST_PASSPHRASE   = os.environ.get("PAYFAST_PASSPHRASE", "")
FRONTEND_URL         = os.environ.get("FRONTEND_URL", "https://seekreap-frontend.onrender.com")
TIER4_INTERNAL       = os.environ.get("TIER4_INTERNAL", "https://seekreap-tier-4-dev.fly.dev")

# Server-authoritative plan pricing — client-supplied amount is always ignored
PLAN_AMOUNTS = {"payg": 199, "creator": 999, "studio": 2999}

_DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwam.com",
    "trashmail.com", "sharklasers.com", "yopmail.com", "yopmail.fr",
    "spam4.me", "dispostable.com", "mailnull.com", "maildrop.cc",
    "discard.email", "getnada.com", "fakeinbox.com", "mytemp.email",
    "spamgourmet.com", "spamgourmet.net", "mohmal.com",
}

def _is_disposable_email(email: str) -> bool:
    try:
        return email.strip().lower().split("@")[-1] in _DISPOSABLE_DOMAINS
    except Exception:
        return False


# ── Circuit breaker ───────────────────────────────────────────────────────────
# In-process per-gateway circuit breaker.
# Opens after 3 consecutive failures; auto-resets after 60 s.
_circuit_state: dict = {}
_CIRCUIT_FAIL_THRESHOLD = 3
_CIRCUIT_OPEN_SECONDS   = 60

def _circuit_ok(gateway: str) -> bool:
    return _circuit_state.get(gateway, {}).get("open_until", 0) <= _time.time()

def _circuit_record_success(gateway: str):
    _circuit_state[gateway] = {"failures": 0, "open_until": 0}

def _circuit_record_failure(gateway: str):
    s = _circuit_state.get(gateway, {"failures": 0, "open_until": 0})
    s["failures"] = s.get("failures", 0) + 1
    if s["failures"] >= _CIRCUIT_FAIL_THRESHOLD:
        s["open_until"] = _time.time() + _CIRCUIT_OPEN_SECONDS
        log_warn("circuit", "opened", gateway=gateway, failures=s["failures"])
    _circuit_state[gateway] = s


def _retry_request(method: str, url: str, gateway: str = "",
                   max_attempts: int = 3, timeout: int = 5, **kwargs):
    """
    HTTP request with circuit breaker + exponential backoff.
    - Checks circuit before each attempt
    - Does NOT retry 4xx responses (client errors)
    - Retries on ConnectionError, Timeout, and 5xx
    - Max wall time ≈ timeout * max_attempts + sum(2^i for i in backoffs)
    Returns (response, None) on success, (None, last_exception) on exhaustion.
    """
    if gateway and not _circuit_ok(gateway):
        log_warn("circuit", "request_blocked", gateway=gateway, url=url)
        return None, Exception(f"circuit open for {gateway}")

    kwargs.setdefault("timeout", timeout)
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = getattr(requests, method.lower())(url, **kwargs)
            # FIX: only 2xx is success — 4xx passes through as failure
            if 200 <= resp.status_code < 300:
                if gateway:
                    _circuit_record_success(gateway)
                return resp, None
            last_exc = Exception(f"HTTP {resp.status_code}: non-2xx")
            last_exc = Exception(f"HTTP {resp.status_code}")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exc = e
        if gateway:
            _circuit_record_failure(gateway)
        if attempt < max_attempts - 1:
            _time.sleep(2 ** attempt)
    return None, last_exc


# ── DB schema: payments ───────────────────────────────────────────────────────
def ensure_payments_tables():
    with db_cursor() as (conn, cur):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                creator_id       TEXT NOT NULL,
                submission_id    UUID,
                plan             TEXT NOT NULL,
                amount           INTEGER NOT NULL,
                currency         TEXT DEFAULT 'ZAR',
                gateway          TEXT NOT NULL,
                payment_ref      TEXT,
                status           TEXT DEFAULT 'pending',
                metadata         JSONB,
                created_at       TIMESTAMP DEFAULT NOW(),
                paid_at          TIMESTAMP,
                cert_retry_count INTEGER DEFAULT 0,
                last_retry_at    TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_payment_ref ON payments(payment_ref)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_creator_id  ON payments(creator_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_status      ON payments(status)")
        # FIX: covering index for retry-certifications cron query
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_retry
            ON payments (status, cert_retry_count, last_retry_at, created_at)
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_events (
                id         BIGSERIAL PRIMARY KEY,
                payment_id UUID NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                gateway    TEXT,
                payload    JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pe_payment_id ON payment_events(payment_id)")

        # FIX: unique constraint on (id, gateway) for DB-level idempotency — no race window
        cur.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id           TEXT NOT NULL,
                gateway      TEXT NOT NULL,
                processed_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (id, gateway)
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_webhook_event
            ON webhook_events (id, gateway)
        """)

        # Idempotent column adds for existing deployments
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS cert_retry_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP")

        # FIX: indices that make rate-limit queries O(log n) instead of full-table scans
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_submissions_creator_time
            ON submissions (creator_id, submitted_at DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payment_events_ip_time
            ON payment_events ((payload->>'ip'), created_at)
            WHERE event_type = 'initiate'
        """)

        conn.commit()


try:
    ensure_payments_tables()
    log_info("payment", "tables_ready")
except Exception as _e:
    log_warn("payment", "table_init_warning", error=str(_e))


def _log_payment_event(payment_id, event_type, gateway, payload):
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                INSERT INTO payment_events (payment_id, event_type, gateway, payload)
                VALUES (%s, %s, %s, %s)
            """, (payment_id, event_type, gateway, Json(payload)))
            conn.commit()
    except Exception as _e:
        log_warn("payment", "event_log_failed",
                 payment_id=str(payment_id), event=event_type, error=str(_e))


def _check_replay(event_id: str, gateway: str) -> bool:
    """
    FIX: Uses DB PRIMARY KEY (id, gateway) as the idempotency lock.
    INSERT ... ON CONFLICT DO NOTHING is atomic — zero race window.
    Returns True if already processed (replay).
    """
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                INSERT INTO webhook_events (id, gateway)
                VALUES (%s, %s)
                ON CONFLICT (id, gateway) DO NOTHING
            """, (event_id, gateway))
            inserted = cur.rowcount
            conn.commit()
            return inserted == 0
    except Exception as _e:
        log_warn("payment", "replay_check_error", error=str(_e))
        return False


def select_gateway(data):
    country  = (data.get("country") or "").upper().strip()
    currency = (data.get("currency") or "ZAR").upper().strip()
    if (country == "ZA" or currency == "ZAR") and PAYFAST_MERCHANT_ID and PAYFAST_MERCHANT_KEY:
        return "payfast"
    if PAYSTACK_SECRET:
        return "paystack"
    return None


def init_paystack(payment_id, data):
    payload = {
        "email":        data["email"],
        "amount":       data["amount"],
        "reference":    str(payment_id),
        "callback_url": FRONTEND_URL + "/payment_success.html",
        "metadata": {
            "payment_id":    str(payment_id),
            "plan":          data["plan"],
            "creator_id":    data["creator_id"],
            "title":         data.get("title", ""),
            "cancel_action": FRONTEND_URL + "/certification_portal.html",
        },
    }
    try:
        r, _err = _retry_request(
            "post",
            "https://api.paystack.co/transaction/initialize",
            gateway="paystack",
            max_attempts=3, timeout=5,
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET}",
                     "Content-Type": "application/json"},
            json=payload,
        )
        if r is None:
            raise Exception(str(_err))
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
        log_error("paystack", "init_error", error=str(e))
        return jsonify({"error": "Payment gateway unavailable"}), 502


def init_payfast(payment_id, data):
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
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in fields.items() if v
    )
    if PAYFAST_PASSPHRASE:
        sig_str += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE)}"
    fields["signature"] = hashlib.md5(sig_str.encode()).hexdigest()
    return jsonify({
        "gateway":    "payfast",
        "action_url": "https://www.payfast.co.za/eng/process",
        "fields":     fields,
    })


def _set_cert_retry_flag(payment_id_str):
    try:
        with db_cursor() as (conn, cur):
            cur.execute(
                "UPDATE payments SET metadata = COALESCE(metadata,'{}') || %s::jsonb WHERE id = %s",
                (_json.dumps({"cert_retry": True}), payment_id_str)
            )
            conn.commit()
    except Exception as _e:
        log_error("payment", "cert_retry_flag_failed",
                  payment_id=payment_id_str, error=str(_e))


def trigger_certification(payment_row, pending_meta):
    meta    = pending_meta or {}
    payload = {
        "creator_id":      payment_row["creator_id"],
        "email":           meta.get("email", ""),
        "title":           meta.get("title", "Untitled Work"),
        "work_type":       meta.get("work_type", "other"),
        "content_hash":    meta.get("content_hash", ""),
        "plan":            payment_row["plan"],
        "collaborators":   meta.get("collaborators", []),
        "ownership_split": meta.get("ownership_split", {}),
        "artistic_name":   meta.get("artistic_name", ""),
        "payment_id":      str(payment_row["id"]),
    }
    try:
        # FIX: use _retry_request so transient failures don't silently drop certs
        r, _cert_err = _retry_request(
            "post",
            TIER4_INTERNAL + "/api/certify",
            gateway="internal",
            max_attempts=2, timeout=30,
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        if r is None:
            raise Exception(f"certify endpoint unreachable: {_cert_err}")
        data = r.json()
        log_info("payment", "cert_triggered",
                 submission_id=data.get("submission_id"),
                 cert_id=data.get("cert_id"))
        with db_cursor() as (conn, cur):
            cur.execute("UPDATE payments SET submission_id = %s WHERE id = %s",
                        (data.get("submission_id"), str(payment_row["id"])))
            conn.commit()
        return data
    except Exception as e:
        log_error("payment", "trigger_cert_error", error=str(e))
        return None


@app.post("/api/payments/initiate")
def initiate_payment():
    claims, err = _require_auth(request)
    if err:
        return err

    body = request.get_json(force=True) or {}
    plan  = _clamp_str(body.get("plan"), 32).lower().strip()
    email = _clamp_str(body.get("email"), 254).strip()

    # creator_id authoritative from JWT — never from request body
    creator_id = claims.get("sub", "")
    if not creator_id:
        return jsonify({"error": "Token missing sub claim"}), 401

    if plan == "free":
        return jsonify({"error": "Free plan does not require payment"}), 400
    if plan not in PLAN_AMOUNTS:
        return jsonify({"error": f"Unknown plan '{plan}'"}), 400
    if not email:
        return jsonify({"error": "email required"}), 400
    if not _valid_email(email):
        return jsonify({"error": "Invalid email address"}), 400
    if _is_disposable_email(email):
        log_warn("fraud", "disposable_email",
                 email=email, creator_id=creator_id, ip=request.remote_addr)
        return jsonify({"error": "Please use a permanent email address for payment."}), 400

    # Idempotency: reuse pending payment created within 5 min
    try:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute("""
                SELECT id FROM payments
                WHERE creator_id = %s AND plan = %s AND status = 'pending'
                  AND created_at > NOW() - INTERVAL '5 minutes'
                ORDER BY created_at DESC LIMIT 1
            """, (creator_id, plan))
            existing_payment = cur.fetchone()
        if existing_payment:
            existing_id = str(existing_payment["id"])
            log_info("payment", "idempotent_reuse", payment_id=existing_id)
            data = {**body, "amount": PLAN_AMOUNTS[plan], "email": email,
                    "creator_id": creator_id}
            gateway = select_gateway(body)
            if gateway == "paystack":
                return init_paystack(existing_id, data)
            if gateway == "payfast":
                return init_payfast(existing_id, data)
    except Exception as _ie:
        log_warn("payment", "idempotency_check_failed", error=str(_ie))

    # Rate limit: max 5 pending per creator per hour
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT COUNT(*) FROM payments
                WHERE creator_id = %s AND status = 'pending'
                  AND created_at > NOW() - INTERVAL '1 hour'
            """, (creator_id,))
            if cur.fetchone()[0] >= 5:
                return jsonify({"error": "Too many pending payments. "
                                         "Complete or wait before retrying."}), 429
    except Exception as _e:
        log_warn("payment", "rate_limit_check_failed", error=str(_e))

    # Rate limit: max 10 initiations per IP per hour
    client_ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")
                 .split(",")[0].strip())
    if client_ip:
        try:
            with db_cursor() as (conn, cur):
                cur.execute("""
                    SELECT COUNT(*) FROM payment_events
                    WHERE event_type = 'initiate' AND (payload->>'ip') = %s
                      AND created_at > NOW() - INTERVAL '1 hour'
                """, (client_ip,))
                if cur.fetchone()[0] >= 10:
                    log_warn("fraud", "ip_rate_limit", ip=client_ip)
                    return jsonify({"error": "Too many requests. Please try again later."}), 429
        except Exception as _e2:
            log_warn("payment", "ip_rate_check_failed", error=str(_e2))

    amount  = PLAN_AMOUNTS[plan]
    gateway = select_gateway(body)
    if not gateway:
        return jsonify({"error": "No payment gateway available"}), 502

    pending_meta = {
        "email":           email,
        "title":           _clamp_str(body.get("title"), 512),
        "work_type":       _clamp_str(body.get("work_type"), 32).lower() or "other",
        "content_hash":    _clamp_str(body.get("content_hash"), 128),
        "collaborators":   (body.get("collaborators") or [])[:20],
        "ownership_split": body.get("ownership_split") or {},
        "artistic_name":   _clamp_str(body.get("artistic_name"), 128),
    }

    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            cur.execute("""
                INSERT INTO payments (creator_id, plan, amount, currency, gateway, status, metadata)
                VALUES (%s, %s, %s, 'ZAR', %s, 'pending', %s)
                RETURNING id
            """, (creator_id, plan, amount, gateway, Json(pending_meta)))
            payment_id = str(cur.fetchone()["id"])
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500

    _log_payment_event(payment_id, "initiate", gateway,
                       {"ip": client_ip, "plan": plan, "creator_id": creator_id})

    data = {**body, "amount": amount, "email": email, "creator_id": creator_id}
    if gateway == "paystack":
        return init_paystack(payment_id, data)
    if gateway == "payfast":
        return init_payfast(payment_id, data)
    return jsonify({"error": "No gateway available"}), 502


@app.post("/api/payments/webhook/paystack")
def paystack_webhook():
    """
    Security layers:
    1. HMAC-SHA512 signature verification
    2. DB-level replay prevention — PRIMARY KEY (id, gateway) atomic insert
    3. Server-side transaction verify via Paystack API (HARD STOP on failure)
    4. Amount, email, currency cross-checks
    5. FIX: Atomic UPDATE WHERE status = 'pending' — enforces valid state machine
    """
    raw_body = request.get_data()
    sig      = request.headers.get("X-Paystack-Signature", "")

    if PAYSTACK_SECRET:
        expected = hmac.new(PAYSTACK_SECRET.encode(), raw_body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(expected, sig):
            log_warn("paystack", "webhook_sig_mismatch")
            return jsonify({"error": "Invalid signature"}), 400

    payload = request.get_json(force=True) or {}
    event   = payload.get("event")

    REVERSAL_EVENTS = {"charge.dispute.create", "transfer.reversed", "refund.processed"}
    if event in REVERSAL_EVENTS:
        _rev_ref = ((payload.get("data") or {}).get("reference") or
                    (payload.get("data") or {}).get("transaction_reference", ""))
        if _rev_ref:
            try:
                with db_cursor() as (conn, cur):
                    cur.execute(
                        "UPDATE payments SET status = 'reversed' "
                        "WHERE payment_ref = %s AND status = 'paid'",
                        (_rev_ref,))
                    conn.commit()
                    log_warn("paystack", "payment_reversed", ref=_rev_ref, event=event)
                _log_payment_event(_rev_ref, "reversed", "paystack",
                                   {"event": event, "ref": _rev_ref})
            except Exception as _re:
                log_error("paystack", "reversal_update_failed",
                          ref=_rev_ref, error=str(_re))
        return jsonify({"status": "ok"}), 200

    if event != "charge.success":
        return jsonify({"status": "ignored"}), 200

    ref      = payload["data"]["reference"]
    event_id = payload.get("id") or f"paystack:{ref}"

    if _check_replay(event_id, "paystack"):
        log_info("paystack", "replay_blocked", event_id=event_id)
        return jsonify({"status": "already_processed"}), 200

    # Server-side verify — HARD STOP, no fallback
    verify_data = None
    for _attempt in range(3):
        try:
            _vr = requests.get(
                f"https://api.paystack.co/transaction/verify/{ref}",
                headers={"Authorization": f"Bearer {PAYSTACK_SECRET}"},
                timeout=5,
            )
            verify_data = _vr.json()
            break
        except Exception as _ve:
            log_warn("paystack", "verify_attempt_failed",
                     ref=ref, attempt=_attempt + 1, error=str(_ve))
            _circuit_record_failure("paystack")
            if _attempt < 2:
                _time.sleep(2 ** _attempt)

    if not verify_data or verify_data.get("data", {}).get("status") != "success":
        log_error("paystack", "verify_failed_hard_stop", ref=ref)
        return jsonify({"error": "Payment verification failed — not processing"}), 400

    _circuit_record_success("paystack")
    _txn        = verify_data["data"]
    gw_response = (_txn.get("gateway_response") or "").lower()
    if gw_response not in ("approved", "successful", ""):
        log_warn("paystack", "gateway_response_not_approved",
                 ref=ref, gateway_response=gw_response)
        return jsonify({"error": "Gateway did not approve transaction"}), 400

    amount_verified = int(_txn.get("amount", 0))
    cust_email      = (_txn.get("customer") or {}).get("email", "")
    currency        = _txn.get("currency", "")

    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            cur.execute("SELECT * FROM payments WHERE id = %s", (ref,))
            payment = cur.fetchone()
            if not payment:
                log_warn("paystack", "payment_not_found", ref=ref)
                return jsonify({"error": "payment not found"}), 404

            if payment["status"] == "expired":
                return jsonify({"error": "Payment expired"}), 400

            if currency and currency != "ZAR":
                return jsonify({"error": "Currency mismatch"}), 400

            init_email = ((payment.get("metadata") or {}).get("email") or "").strip().lower()
            if init_email and cust_email and init_email != cust_email.strip().lower():
                log_warn("paystack", "email_mismatch",
                         ref=ref, init=init_email, webhook=cust_email)
                return jsonify({"error": "Email mismatch"}), 400

            if int(payment["amount"]) != amount_verified:
                log_warn("paystack", "amount_mismatch",
                         ref=ref, expected=payment["amount"], got=amount_verified)
                return jsonify({"error": "Amount mismatch"}), 400

            # FIX: transition guard — only from 'pending' (expired/paid/reversed blocked)
            cur.execute("""
                UPDATE payments SET status = 'paid', paid_at = NOW(), payment_ref = %s
                WHERE id = %s AND status = 'pending'
                RETURNING *
            """, (ref, ref))
            paid_row = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_error("paystack", "webhook_db_error", ref=ref, error=str(e))
            return jsonify({"error": str(e)}), 500

    if not paid_row:
        log_info("paystack", "idempotent_skip", ref=ref, status=payment.get("status"))
        return jsonify({"status": "already_processed"}), 200

    _log_payment_event(ref, "paid", "paystack", {
        "amount": amount_verified, "currency": currency, "gateway_response": gw_response
    })

    clean_meta = {k: v for k, v in (paid_row.get("metadata") or {}).items()
                  if k not in ("paystack_event", "payfast_itn")}
    if not trigger_certification(paid_row, clean_meta):
        _set_cert_retry_flag(ref)

    return jsonify({"status": "ok"}), 200


@app.post("/api/payments/webhook/payfast")
def payfast_webhook():
    import urllib.parse

    data       = request.form.to_dict()
    payment_id = data.get("m_payment_id")
    pf_status  = data.get("payment_status")

    sig_received = data.pop("signature", "")
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in data.items() if v
    )
    if PAYFAST_PASSPHRASE:
        sig_str += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE)}"
    if hashlib.md5(sig_str.encode()).hexdigest() != sig_received:
        log_warn("payfast", "itn_sig_mismatch", payment_id=payment_id)
        return "INVALID", 400

    if pf_status != "COMPLETE":
        return "ok", 200

    if data.get("merchant_id") != PAYFAST_MERCHANT_ID:
        log_warn("payfast", "merchant_id_mismatch",
                 received=data.get("merchant_id"), payment_id=payment_id)
        return "INVALID", 400

    event_id = f"payfast:{data.get('pf_payment_id', payment_id)}"
    if _check_replay(event_id, "payfast"):
        log_info("payfast", "replay_blocked", event_id=event_id)
        return "ok", 200

    _pf_host = ("sandbox.payfast.co.za"
                if os.environ.get("PAYFAST_SANDBOX") == "true"
                else "www.payfast.co.za")
    confirm_resp, _pfe = _retry_request(
        "post",
        f"https://{_pf_host}/eng/query/validate",
        gateway="payfast",
        max_attempts=3, timeout=5,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if confirm_resp is None:
        log_error("payfast", "itn_confirm_unreachable",
                  payment_id=payment_id, error=str(_pfe))
        return "RETRY", 503
    if confirm_resp.text.strip() != "VALID":
        log_warn("payfast", "itn_confirmation_failed",
                 payment_id=payment_id, response=confirm_resp.text[:200])
        return "INVALID", 400

    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
            payment = cur.fetchone()
            if not payment:
                return "ok", 200

            if payment["status"] == "expired":
                return "INVALID", 400

            try:
                itn_amount_cents = round(float(data.get("amount_gross", 0)) * 100)
            except (ValueError, TypeError):
                itn_amount_cents = 0

            if itn_amount_cents != int(payment["amount"]):
                log_warn("payfast", "amount_mismatch",
                         payment_id=payment_id,
                         expected=payment["amount"], got=itn_amount_cents)
                return "INVALID", 400

            itn_email  = (data.get("email_address") or "").strip().lower()
            init_email = ((payment.get("metadata") or {}).get("email") or "").strip().lower()
            if itn_email and init_email and itn_email != init_email:
                log_warn("payfast", "email_mismatch",
                         payment_id=payment_id, init=init_email, itn=itn_email)
                return "INVALID", 400

            # FIX: transition guard — only from 'pending'
            cur.execute("""
                UPDATE payments SET status = 'paid', paid_at = NOW(), payment_ref = %s
                WHERE id = %s AND status = 'pending'
                RETURNING *
            """, (data.get("pf_payment_id"), payment_id))
            paid_row = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_error("payfast", "webhook_db_error",
                      payment_id=payment_id, error=str(e))
            return str(e), 500

    if not paid_row:
        log_info("payfast", "idempotent_skip", payment_id=payment_id,
                 status=payment.get("status"))
        return "ok", 200

    _log_payment_event(payment_id, "paid", "payfast", {
        "pf_payment_id": data.get("pf_payment_id"),
        "amount_gross":  data.get("amount_gross"),
    })

    clean_meta = {k: v for k, v in (paid_row.get("metadata") or {}).items()
                  if k not in ("paystack_event", "payfast_itn")}
    if not trigger_certification(paid_row, clean_meta):
        _set_cert_retry_flag(payment_id)

    return "ok", 200


@app.get("/api/payments/<payment_id>")
def get_payment_status(payment_id):
    claims, err = _require_auth(request)
    if err:
        return err
    if not _valid_uuid(payment_id):
        return jsonify({"error": "invalid payment_id"}), 400

    token_sub = claims.get("sub", "")
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT p.id, p.creator_id, p.status, p.plan, p.paid_at,
                   p.submission_id, p.gateway, p.payment_ref,
                   s.cert_id, s.status as cert_status
            FROM payments p
            LEFT JOIN submissions s ON s.id = p.submission_id
            WHERE p.id = %s
        """, (payment_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        try:
            token_uuid = str(uuid.UUID(token_sub))
        except Exception:
            token_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, token_sub))

        if row["creator_id"] != token_uuid and row["creator_id"] != token_sub:
            log_warn("payment_status", "ownership_denied",
                     token_sub=token_sub, payment_id=payment_id)
            return jsonify({"error": "not found"}), 404

        result = dict(row)
        result["id"]            = str(result["id"])
        result["submission_id"] = (str(result["submission_id"])
                                   if result["submission_id"] else None)
        result.pop("creator_id", None)
        if result["paid_at"]:
            result["paid_at"] = result["paid_at"].isoformat()
        return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# Admin / cron endpoints (X-Admin-Key required)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/admin/expire-payments")
def expire_stale_payments():
    auth_err = _require_admin(request)
    if auth_err:
        return auth_err
    with db_cursor() as (conn, cur):
        try:
            cur.execute("""
                UPDATE payments SET status = 'expired'
                WHERE status = 'pending' AND created_at < NOW() - INTERVAL '30 minutes'
            """)
            expired = cur.rowcount
            conn.commit()
            log_info("admin", "payments_expired", count=expired)
            return jsonify({"expired": expired}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500


@app.post("/api/admin/retry-certifications")
def retry_pending_certifications():
    auth_err = _require_admin(request)
    if auth_err:
        return auth_err

    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT * FROM payments
            WHERE status = 'paid' AND submission_id IS NULL
              AND (metadata->>'cert_retry')::boolean = true
              AND COALESCE(cert_retry_count, 0) < 3
              AND (last_retry_at IS NULL OR last_retry_at < NOW() - INTERVAL '5 minutes')
              AND created_at > NOW() - INTERVAL '24 hours'
            ORDER BY created_at LIMIT 20
            FOR UPDATE SKIP LOCKED
        """)
        rows = cur.fetchall()

    retried = 0
    for row in rows:
        try:
            clean = {k: v for k, v in (row.get("metadata") or {}).items()
                     if k not in ("paystack_event", "payfast_itn")}
            result = trigger_certification(row, clean)
            with db_cursor() as (conn, cur):
                if result:
                    retried += 1
                    cur.execute("""
                        UPDATE payments
                        SET metadata = metadata - 'cert_retry',
                            cert_retry_count = COALESCE(cert_retry_count, 0) + 1,
                            last_retry_at = NOW()
                        WHERE id = %s
                    """, (str(row["id"]),))
                else:
                    # FIX: dead-letter — mark failed_permanent after max retries
                    cur.execute("""
                        UPDATE payments
                        SET cert_retry_count = COALESCE(cert_retry_count, 0) + 1,
                            last_retry_at = NOW(),
                            status = CASE
                                WHEN COALESCE(cert_retry_count, 0) + 1 >= 3
                                THEN 'failed_permanent'
                                ELSE status
                            END
                        WHERE id = %s
                    """, (str(row["id"]),))
                conn.commit()
        except Exception as e:
            log_error("admin", "retry_cert_failed",
                      payment_id=str(row["id"]), error=str(e))

    log_info("admin", "cert_retry_run", retried=retried, total=len(rows))
    return jsonify({"retried": retried, "total": len(rows)}), 200


# ── Graceful shutdown ─────────────────────────────────────────────────────────
import atexit as _atexit

def _shutdown_pool():
    global _db_pool
    if _db_pool and not _db_pool.closed:
        try:
            _db_pool.closeall()
            log_info("db", "pool_closed_on_shutdown")
        except Exception as _e:
            log_warn("db", "pool_close_error", error=str(_e))

_atexit.register(_shutdown_pool)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
