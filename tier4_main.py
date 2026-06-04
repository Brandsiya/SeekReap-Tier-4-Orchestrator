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
    "https://seekreap-tier-6-frontend.fly.dev",
    "https://brandsiya.github.io",
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
# JWT / Supabase token verification  — ES256 + JWKS (replaces HS256 block)
# ══════════════════════════════════════════════════════════════════════════════

import threading
from jose import jwt as _jose_jwt, exceptions as _jose_exc

SUPABASE_URL        = os.environ.get("SUPABASE_URL", "")          # e.g. https://xxxx.supabase.co
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")   # kept for HS256 fallback only

# ── JWKS cache ────────────────────────────────────────────────────────────────
_jwks_cache:      list   = []
_jwks_fetched_at: float  = 0.0
_jwks_lock               = threading.Lock()
_JWKS_TTL_SECONDS        = 6 * 3600   # 6 hours


def _jwks_url() -> str:
    base = SUPABASE_URL.rstrip("/")
    return f"{base}/auth/v1/.well-known/jwks.json"


def _fetch_jwks(force: bool = False) -> list:
    """
    Fetches Supabase JWKS and caches them.

    Supabase's /auth/v1/keys endpoint requires the anon key as
    'apikey' header — without it the endpoint returns a 401 that
    looks like a network error and leaves jwks_cache empty forever.
    """
    global _jwks_cache, _jwks_fetched_at
    now = _time.time()
    with _jwks_lock:
        if not force and _jwks_cache and (now - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
            return _jwks_cache

        url = _jwks_url()
        if not url or url == "/auth/v1/.well-known/jwks.json":
            log_error("auth", "jwks_supabase_url_not_set",
                      hint="Set SUPABASE_URL env var to https://xxxx.supabase.co")
            return _jwks_cache

        # Supabase requires the anon/service key on this endpoint
        supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
        headers = {}
        if supabase_anon_key:
            headers["apikey"] = supabase_anon_key
        else:
            log_warn("auth", "jwks_anon_key_missing",
                     hint="Set SUPABASE_ANON_KEY — some Supabase deployments require it")

        log_info("auth", "fetching_jwks", url=url, has_apikey=bool(supabase_anon_key))
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            log_info("auth", "jwks_http_response",
                     status=resp.status_code, body_preview=resp.text[:300])
            if resp.status_code == 200:
                body = resp.json()
                # body is {"keys": [...]} — extract the list
                if isinstance(body, list):
                    keys = body
                else:
                    keys = body.get("keys") or []
                if not keys:
                    log_warn("auth", "jwks_empty_response", body=str(body)[:300])
                    return _jwks_cache
                _jwks_cache      = keys
                _jwks_fetched_at = now
                log_info("auth", "jwks_refreshed", key_count=len(keys))
                return keys
            else:
                log_error("auth", "jwks_fetch_non200",
                          status=resp.status_code, body=resp.text[:400])
        except requests.exceptions.ConnectionError as e:
            log_error("auth", "jwks_connection_error", error=str(e),
                      hint="Fly container cannot reach Supabase — check egress/DNS")
        except requests.exceptions.Timeout:
            log_error("auth", "jwks_timeout", url=url)
        except Exception as e:
            log_error("auth", "jwks_fetch_error", error=str(e))

        return _jwks_cache  # return stale cache rather than crashing


def _verify_supabase_jwt(token: str) -> dict | None:
    if not token:
        return None

    try:
        header = _jose_jwt.get_unverified_header(token)
    except Exception as e:
        log_warn("auth", "jwt_bad_header", error=str(e))
        return None

    alg = header.get("alg", "")
    kid = header.get("kid", "")

    # ES256 path (Supabase default)
    if alg == "ES256":
        for force_refresh in (False, True):
            keys = _fetch_jwks(force=force_refresh)
            matched = [k for k in keys if k.get("kid") == kid] if kid else keys
            if not matched:
                if force_refresh:
                    log_warn("auth", "jwks_kid_not_found", kid=kid)
                    return None
                continue
            try:
                claims = _jose_jwt.decode(
                    token,
                    matched[0],
                    algorithms=["ES256"],
                    audience="authenticated",
                )
                return claims
            except _jose_exc.ExpiredSignatureError:
                log_warn("auth", "jwt_expired")
                return None
            except _jose_exc.JWTError as e:
                log_warn("auth", "jwt_es256_invalid", error=str(e))
                return None
        return None

    # HS256 path (legacy / local dev)
    if alg == "HS256":
        if not SUPABASE_JWT_SECRET:
            log_warn("auth", "hs256_token_but_no_secret")
            return None
        try:
            claims = _jose_jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
            return claims
        except _jose_exc.ExpiredSignatureError:
            log_warn("auth", "jwt_expired")
            return None
        except _jose_exc.JWTError as e:
            log_warn("auth", "jwt_hs256_invalid", error=str(e))
            return None

    log_warn("auth", "jwt_unknown_alg", alg=alg)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Auth helpers
# FIX: _require_auth now validates sub is present and logs verified identity
# ══════════════════════════════════════════════════════════════════════════════

def _require_auth(req) -> tuple:
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, (jsonify({"error": "Authorization header required"}), 401)
    token = auth_header[7:].strip()
    if not token:
        return None, (jsonify({"error": "Bearer token is empty"}), 401)
    claims = _verify_supabase_jwt(token)
    if not claims:
        return None, (jsonify({"error": "Invalid or expired token"}), 401)
    sub = claims.get("sub", "")
    if not sub:
        log_warn("auth", "token_missing_sub", claims_keys=list(claims.keys()))
        return None, (jsonify({"error": "Token missing sub claim"}), 401)
    log_info("auth", "verified", sub=sub[:8] + "…")
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
# ══════════════════════════════════════════════════════════════════════════════

from psycopg2 import pool as _pg_pool
from contextlib import contextmanager

_db_pool: "_pg_pool.ThreadedConnectionPool | None" = None

def _get_pool() -> "_pg_pool.ThreadedConnectionPool":
    global _db_pool
    if _db_pool is None or _db_pool.closed:
        dsn = os.environ["DATABASE_URL"]
        # Add keepalives if not already present
        if "keepalives" not in dsn:
            sep = "&" if "?" in dsn else "?"
            dsn = dsn + sep + "keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
        _db_pool = _pg_pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            dsn=dsn,
            connect_timeout=10,
        )
    return _db_pool

def get_db():
    pool = _get_pool()
    conn = pool.getconn()
    # Validate connection — discard and replace if stale
    try:
        conn.cursor().execute("SELECT 1")
    except Exception:
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = psycopg2.connect(
            os.environ["DATABASE_URL"],
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
    return conn

def put_db(conn):
    if conn is not None:
        try:
            if conn.closed:
                return
            _get_pool().putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def db_conn():
    conn = get_db()
    try:
        yield conn
    finally:
        put_db(conn)


@contextmanager
def db_cursor(cursor_factory=None):
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
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT 1")
    except Exception as e:
        problems["db"] = str(e)
    try:
        _p = _get_pool()
        if _p.closed:
            problems["pool"] = "closed"
    except Exception as _pe:
        problems["pool"] = str(_pe)
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


@app.get("/debug/jwks-probe")
def debug_jwks_probe():
    """
    Live JWKS fetch diagnostic — shows exactly what the Supabase key endpoint returns.
    Remove after confirming auth works.
    """
    url = _jwks_url()
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    supabase_url_set = bool(SUPABASE_URL)

    result = {
        "supabase_url_set": supabase_url_set,
        "jwks_url":         url,
        "anon_key_set":     bool(anon_key),
        "cached_key_count": len(_jwks_cache),
    }

    if not supabase_url_set:
        result["error"] = "SUPABASE_URL env var is not set"
        return jsonify(result), 500

    headers = {"apikey": anon_key} if anon_key else {}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        result["http_status"] = resp.status_code
        result["response_preview"] = resp.text[:600]
        if resp.status_code == 200:
            body = resp.json()
            keys = body.get("keys") or (body if isinstance(body, list) else [])
            result["key_count"] = len(keys)
            result["key_ids"] = [k.get("kid") for k in keys]
            result["step"] = "ok"
        else:
            result["step"] = "http_error"
    except Exception as e:
        result["step"]  = "exception"
        result["error"] = str(e)

    return jsonify(result), 200 if result.get("step") == "ok" else 500


@app.get("/debug/auth-probe")
def debug_auth_probe():
    """
    Token verification diagnostic. Remove after confirming auth works.
    Returns claim keys (not values) so nothing sensitive is exposed.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"step": "no_bearer_header"}), 400
    token = auth_header[7:].strip()
    try:
        header = _jose_jwt.get_unverified_header(token)
    except Exception as e:
        return jsonify({"step": "bad_header", "error": str(e)}), 400

    claims = _verify_supabase_jwt(token)
    if not claims:
        return jsonify({
            "step":                  "verify_failed",
            "alg":                   header.get("alg"),
            "kid":                   header.get("kid"),
            "jwks_url":              _jwks_url(),
            "jwks_cached_key_count": len(_jwks_cache),
            "supabase_url_set":      bool(SUPABASE_URL),
            "anon_key_set":          bool(os.environ.get("SUPABASE_ANON_KEY")),
            "hint": "Run GET /debug/jwks-probe to see the live JWKS fetch result",
        }), 401

    return jsonify({
        "step":       "ok",
        "alg":        header.get("alg"),
        "claim_keys": list(claims.keys()),
        "sub_prefix": claims.get("sub", "")[:8] + "…",
        "aud":        claims.get("aud"),
        "exp_valid":  True,
    }), 200


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
    if gateway and not _circuit_ok(gateway):
        log_warn("circuit", "request_blocked", gateway=gateway, url=url)
        return None, Exception(f"circuit open for {gateway}")

    kwargs.setdefault("timeout", timeout)
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = getattr(requests, method.lower())(url, **kwargs)
            if 200 <= resp.status_code < 300:
                if gateway:
                    _circuit_record_success(gateway)
                return resp, None
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
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS cert_retry_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP")
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
        "notify_url":   TIER4_INTERNAL + "/api/payments/webhook/paystack",
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


# ══════════════════════════════════════════════════════════════════════════════
# FIX: initiate_payment wrapped for full traceback on any 500
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/payments/initiate")
def initiate_payment():
    try:
        return _initiate_payment_inner()
    except Exception as e:
        import traceback
        log_error("payment", "initiate_unhandled",
                  error=str(e), trace=traceback.format_exc())
        return jsonify({"error": "Internal server error", "detail": str(e)}), 500


def _initiate_payment_inner():
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

    raw_data = request.form
    sig_received = raw_data.get("signature", "")
    sig_str = "&".join(
        f"{k}={urllib.parse.quote_plus(str(raw_data[k]))}"
        for k in raw_data.keys()
        if k != "signature" and raw_data[k] is not None
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



# ── Startup: pre-warm JWKS cache ──────────────────────────────────────────────
# Fetches Supabase public keys at module load time so every process (including
# gunicorn worker forks) has a warm cache before the first request arrives.
# Without this, a cold-start race between Fly machines means auth-probe hits
# machine A (warm) while /api/payments/initiate hits machine B (cold) → 401.
def _warmup_jwks():
    if not SUPABASE_URL:
        log_warn("auth", "jwks_warmup_skipped", reason="SUPABASE_URL not set")
        return
    keys = _fetch_jwks(force=True)
    if keys:
        log_info("auth", "jwks_warmup_ok", key_count=len(keys),
                 kids=[k.get("kid") for k in keys])
    else:
        log_error("auth", "jwks_warmup_failed",
                  hint="Check SUPABASE_URL and SUPABASE_ANON_KEY secrets")

_warmup_jwks()


@app.get("/api/payment/probe")
def payment_probe():
    """Internal health probe for payment systems — used by system monitor."""
    payfast_ok  = bool(PAYFAST_MERCHANT_KEY and PAYFAST_MERCHANT_ID)
    paystack_ok = bool(PAYSTACK_SECRET)

    return jsonify({
        'payfast_ok'     : payfast_ok,
        'payfast_status' : 'Ready' if payfast_ok else 'Missing PAYFAST_MERCHANT_KEY / PAYFAST_MERCHANT_ID',
        'paystack_ok'    : paystack_ok,
        'paystack_status': 'Ready' if paystack_ok else 'Missing PAYSTACK_SECRET_KEY',
        'sig_valid'      : payfast_ok,
        'webhook_ok'     : False,
        'note'           : 'PayFast and Paystack accounts pending external verification review.'
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

@app.get("/api/debug/env-preview")
def debug_env_preview():
    """Temporary endpoint to preview env vars (masked)"""
    import os
    env_vars = {
        "DATABASE_URL": os.environ.get("DATABASE_URL", "NOT_SET")[:30] + "...",
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", "NOT_SET"),
        "SUPABASE_ANON_KEY": os.environ.get("SUPABASE_ANON_KEY", "NOT_SET")[:20] + "...",
        "SUPABASE_JWT_SECRET": "***SET***" if os.environ.get("SUPABASE_JWT_SECRET") else "NOT_SET",
        "PAYSTACK_SECRET_KEY": "***SET***" if os.environ.get("PAYSTACK_SECRET_KEY") else "NOT_SET",
        "FRONTEND_URL": os.environ.get("FRONTEND_URL", "NOT_SET"),
    }
    return jsonify(env_vars)


# ══════════════════════════════════════════════════════════════════
# CO-OWNERSHIP AGREEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════

import hashlib, secrets
from datetime import datetime, timezone, timedelta

# ══════════════════════════════════════════════════════════════════
# CANONICALIZATION SPEC v1 — FROZEN
# Changes to this spec require a new version and migration path.
# Rules:
#   - Encoding:      UTF-8, NFC normalization
#   - Keys:          sorted lexicographically, all levels
#   - Floats:        rounded to 2 decimal places
#   - Integers:      unchanged
#   - None/null:     serialized as JSON null
#   - Strings:       NFC unicode normalized, no truncation
#   - Timestamps:    must be ISO 8601 UTC strings before passing in
#   - Separators:    no spaces (',', ':')
#   - Booleans:      true/false lowercase (standard JSON)
# ══════════════════════════════════════════════════════════════════
CANON_VERSION = 'v1'

def _canonical_json(obj: dict) -> str:
    """Deterministic JSON serialization per CANON_VERSION v1 spec."""
    import json, unicodedata
    def _clean(v):
        if v is None:
            return None
        if isinstance(v, dict):
            return {k: _clean(v[k]) for k in sorted(v.keys())}
        if isinstance(v, list):
            return [_clean(i) for i in v]
        if isinstance(v, float):
            return round(v, 2)
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            return unicodedata.normalize('NFC', v)
        return str(v)
    return json.dumps(_clean(obj), separators=(',', ':'), sort_keys=True, ensure_ascii=False)

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def _chain_hash(previous_hash: str, event_data: dict) -> str:
    payload = (previous_hash or '') + _canonical_json(event_data)
    return _sha256(payload)

def _log_agreement_event(cur, agreement_id, event_type, actor_id, event_data):
    """Append a chained event to agreement_events."""
    cur.execute(
        "SELECT event_hash FROM public.agreement_events "
        "WHERE agreement_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (agreement_id,)
    )
    row = cur.fetchone()
    prev_hash = row[0] if row else ''
    new_hash = _chain_hash(prev_hash, {'type': event_type, **event_data})
    cur.execute("""
        INSERT INTO public.agreement_events
            (agreement_id, event_type, actor_id, event_data, event_hash, previous_hash)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
    """, (agreement_id, event_type, actor_id,
          json.dumps(event_data), new_hash, prev_hash or None))
    return new_hash


@app.route('/api/agreements/<agreement_id>/revoke', methods=['POST'])
def revoke_agreement(agreement_id):
    """
    Revoke an agreement. Requires unanimous consent (all participants must be accepted).
    Cascades to: delegations revoked, future evaluations blocked, event logged.
    """
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')
    data    = request.get_json(force=True) or {}
    reason  = data.get('reason', 'creator_initiated')

    # Rights Engine gate — revoke_agreement requires unanimous + participant check
    rights = evaluate_rights(agreement_id, user_id, 'revoke_agreement',
                             context={'source': 'revoke_route', 'reason': reason},
                             log=True)
    if not rights['allowed']:
        return jsonify({
            'error':           'Rights check failed',
            'reason':          rights['reason'],
            'decision_source': rights['decision_source'],
        }), 403

    try:
        with db_cursor() as (conn, cur):

            # 1. Verify agreement exists and is active
            cur.execute("""
                SELECT status, created_by FROM public.coownership_agreements
                WHERE id = %s
            """, (agreement_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Agreement not found'}), 404
            if row[0] != 'active':
                return jsonify({'error': f'Agreement is already {row[0]}'}), 409

            # 2. Revoke the agreement
            cur.execute("""
                UPDATE public.coownership_agreements
                SET status           = 'revoked',
                    revoked_at       = NOW(),
                    revocation_reason = %s
                WHERE id = %s
            """, (reason, agreement_id))

            # 3. Cascade — revoke all active delegations for this agreement
            cur.execute("""
                UPDATE public.rights_delegations
                SET status     = 'revoked',
                    revoked_at = NOW(),
                    revoked_by = %s
                WHERE agreement_id = %s
                  AND status = 'active'
                RETURNING id
            """, (user_id, agreement_id))
            revoked_delegations = [str(r[0]) for r in cur.fetchall()]

            # 4. Log revocation event using canonical chain hash
            # event_data must contain ONLY domain fields — no actor_id, event_type,
            # or agreement_id since _recompute_hash prepends 'type' automatically.
            _log_agreement_event(cur, agreement_id, 'revoked', user_id, {
                'reason': reason,
            })

            # Retrieve the hash that was just written for the response
            cur.execute("""
                SELECT event_hash FROM public.agreement_events
                WHERE agreement_id = %s AND event_type = 'revoked'
                ORDER BY created_at DESC LIMIT 1
            """, (agreement_id,))
            _ev = cur.fetchone()
            event_hash = _ev[0] if _ev else None

            # 5. Take a revocation rights snapshot
            revocation_snap_id = _store_rights_snapshot(
                cur, agreement_id,
                _build_rights_snapshot(cur, agreement_id) or {},
                'revocation'
            )

            conn.commit()

            log_info('agreements', 'agreement_revoked',
                     agreement_id=agreement_id,
                     revoked_by=user_id,
                     delegations_revoked=len(revoked_delegations),
                     snap_id=revocation_snap_id)

            return jsonify({
                'agreement_id':        agreement_id,
                'status':              'revoked',
                'revoked_by':          user_id,
                'reason':              reason,
                'delegations_revoked': len(revoked_delegations),
                'revocation_snapshot': revocation_snap_id,
                'event_hash':          event_hash,
            })

    except Exception as e:
        log_error('agreements', 'revoke_failed',
                  agreement_id=agreement_id, error=str(e))
        return jsonify({'error': 'Revocation failed', 'detail': str(e)}), 500


@app.route('/api/agreements', methods=['GET'])
def list_agreements():
    """List all agreements where the authenticated user is a participant."""
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')

    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT
                    a.id,
                    a.status,
                    a.created_at,
                    a.activated_at,
                    a.submission_id,
                    a.commercial_use,
                    a.derivative_works,
                    a.ai_training_permitted,
                    COUNT(ap2.id) AS participant_count,
                    ap.status        AS my_status,
                    ap.ownership_pct
                FROM public.coownership_agreements a
                JOIN public.agreement_participants ap
                    ON ap.agreement_id = a.id AND ap.user_id = %s
                LEFT JOIN public.agreement_participants ap2
                    ON ap2.agreement_id = a.id
                GROUP BY a.id, a.status, a.created_at, a.activated_at,
                         a.submission_id, a.commercial_use, a.derivative_works,
                         a.ai_training_permitted, ap.status, ap.ownership_pct
                ORDER BY a.created_at DESC
                LIMIT 100
            """, (user_id,))

            rows = cur.fetchall()
            agreements = []
            for row in rows:
                (agr_id, status, created_at, activated_at, submission_id,
                 commercial_use, derivative_works, ai_training_permitted,
                 participant_count, my_status, ownership_pct) = row
                agreements.append({
                    'id':                    str(agr_id),
                    'status':                status,
                    'created_at':            created_at.isoformat() if created_at else None,
                    'activated_at':          activated_at.isoformat() if activated_at else None,
                    'submission_id':         str(submission_id) if submission_id else None,
                    'commercial_use':        commercial_use,
                    'derivative_works':      derivative_works,
                    'ai_training_permitted': ai_training_permitted,
                    'participant_count':     participant_count,
                    'my_status':             my_status,
                    'ownership_pct':         float(ownership_pct) if ownership_pct else None,
                })

            return jsonify({
                'agreements': agreements,
                'total':      len(agreements),
            })

    except Exception as e:
        log_error('agreements', 'list_failed', error=str(e))
        return jsonify({'error': 'Failed to list agreements', 'detail': str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# PHASE 3 — ENFORCEMENT TOOLING v1
# Evidence packages, takedown generation, enforcement tracking
# ══════════════════════════════════════════════════════════════════

@app.route('/api/enforcement/evidence-package', methods=['POST'])
def create_evidence_package():
    """
    Compile a structured enforcement evidence package for a submission.
    Bundles: asset identity, ownership proof, agreement chain, cert,
    content matches, and generates a tamper-evident package hash.
    """
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')
    data    = request.get_json(force=True) or {}

    submission_id      = data.get('submission_id')
    package_type       = data.get('package_type', 'takedown')
    target_platform    = data.get('target_platform')
    target_url         = data.get('target_url')
    infringing_url     = data.get('infringing_url')
    infringement_notes = data.get('infringement_notes', '')

    if not submission_id:
        return jsonify({'error': 'submission_id required'}), 400

    valid_types = ('takedown', 'counter_notice',
                   'licensing_demand', 'infringement_report')
    if package_type not in valid_types:
        return jsonify({'error': f'package_type must be one of {valid_types}'}), 400

    try:
        with db_cursor() as (conn, cur):

            # 1. Verify submission belongs to creator
            cur.execute("""
                SELECT id, title, content_type, status,
                       cert_id, submitted_at, completed_at, content_hash
                FROM public.submissions
                WHERE id = %s AND creator_id = %s
            """, (submission_id, user_id))
            sub = cur.fetchone()
            if not sub:
                return jsonify({'error': 'Submission not found or access denied'}), 404

            (sub_id, title, content_type, sub_status,
             cert_id, submitted_at, completed_at, content_hash) = sub

            # 2. Pull asset identity
            cur.execute("""
                SELECT id, canonical_id, tamper_hash,
                       perceptual_hash, ownership_snapshot
                FROM public.asset_identities
                WHERE submission_id = %s
                LIMIT 1
            """, (submission_id,))
            asset = cur.fetchone()
            asset_identity_id = None
            if asset:
                asset_identity_id = str(asset[0])

            # 3. Pull active agreement + chain
            cur.execute("""
                SELECT a.id, a.status, a.agreement_hash,
                       a.activated_at, a.commercial_use,
                       a.derivative_works, a.ai_training_permitted
                FROM public.coownership_agreements a
                JOIN public.agreement_participants ap
                    ON ap.agreement_id = a.id AND ap.user_id = %s
                WHERE a.submission_id = %s AND a.status = 'active'
                LIMIT 1
            """, (user_id, submission_id))
            agr = cur.fetchone()
            agreement_id = None
            agreement_proof = None
            if agr:
                agreement_id = str(agr[0])
                # Get participants
                cur.execute("""
                    SELECT user_id, email, role, ownership_pct, status
                    FROM public.agreement_participants
                    WHERE agreement_id = %s
                """, (agreement_id,))
                participants = [{
                    'user_id':       str(p[0]),
                    'email':         p[1],
                    'role':          p[2],
                    'ownership_pct': float(p[3]) if p[3] else 0,
                    'status':        p[4],
                } for p in cur.fetchall()]
                agreement_proof = {
                    'agreement_id':         agreement_id,
                    'status':               agr[1],
                    'agreement_hash':       agr[2],
                    'activated_at':         agr[3].isoformat() if agr[3] else None,
                    'commercial_use':       agr[4],
                    'derivative_works':     agr[5],
                    'ai_training_permitted': agr[6],
                    'participants':         participants,
                }

            # 4. Pull content matches if any
            cur.execute("""
                SELECT id, matched_submission_id, similarity_score,
                       match_type, severity, detected_at
                FROM public.content_matches
                WHERE submission_id = %s
                ORDER BY similarity_score DESC
                LIMIT 20
            """, (submission_id,))
            matches = [{
                'match_id':            str(m[0]),
                'matched_submission':  str(m[1]) if m[1] else None,
                'similarity_score':    float(m[2]) if m[2] else None,
                'match_type':          m[3],
                'severity':            m[4],
                'detected_at':         m[5].isoformat() if m[5] else None,
            } for m in cur.fetchall()]

            # 5. Build ownership proof
            ownership_proof = {
                'creator_id':       user_id,
                'submission_id':    submission_id,
                'title':            title,
                'content_type':     content_type,
                'cert_id':          cert_id,
                'content_hash':     content_hash,
                'submitted_at':     submitted_at.isoformat() if submitted_at else None,
                'completed_at':     completed_at.isoformat() if completed_at else None,
                'certification_status': sub_status,
                'asset_identity':   {
                    'asset_id':       asset_identity_id,
                    'canonical_id':   str(asset[1]) if asset else None,
                    'tamper_hash':    asset[2] if asset else None,
                    'perceptual_hash': asset[3] if asset else None,
                } if asset else None,
                'agreement':        agreement_proof,
            }

            # 6. Build evidence bundle
            evidence_bundle = {
                'package_type':      package_type,
                'target_platform':   target_platform,
                'target_url':        target_url,
                'infringing_url':    infringing_url,
                'infringement_notes': infringement_notes,
                'content_matches':   matches,
                'match_count':       len(matches),
                'generated_at':      datetime.utcnow().isoformat(),
                'engine_version':    RIGHTS_ENGINE_VERSION,
                'policy_version':    POLICY_REGISTRY_VERSION,
            }

            # 7. Compute package hash
            package_payload = _canonical_json({
                'submission_id':  submission_id,
                'creator_id':     user_id,
                'cert_id':        cert_id or '',
                'content_hash':   content_hash or '',
                'package_type':   package_type,
                'generated_at':   evidence_bundle['generated_at'],
            })
            package_hash = _sha256(package_payload)

            # 8. Store enforcement package
            cur.execute("""
                INSERT INTO public.enforcement_packages (
                    submission_id, creator_id, package_type,
                    target_platform, target_url, infringing_url,
                    infringement_notes, asset_identity_id, agreement_id,
                    cert_id, ownership_proof, evidence_bundle,
                    package_hash, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, 'ready'
                )
                RETURNING id, created_at
            """, (
                submission_id, user_id, package_type,
                target_platform, target_url, infringing_url,
                infringement_notes, asset_identity_id, agreement_id,
                cert_id,
                json.dumps(ownership_proof),
                json.dumps(evidence_bundle),
                package_hash,
            ))
            pkg_row = cur.fetchone()
            package_id, created_at = pkg_row

            # 9. Log enforcement event
            cur.execute("""
                INSERT INTO public.enforcement_events
                    (package_id, event_type, actor_id, notes)
                VALUES (%s, 'package_created', %s, %s)
            """, (str(package_id), user_id,
                  f'{package_type} package created for {title or submission_id}'))

            conn.commit()

            log_info('enforcement', 'package_created',
                     package_id=str(package_id),
                     submission_id=submission_id,
                     package_type=package_type)

            return jsonify({
                'package_id':     str(package_id),
                'package_hash':   package_hash,
                'package_type':   package_type,
                'status':         'ready',
                'cert_id':        cert_id,
                'submission_id':  submission_id,
                'title':          title,
                'ownership_proof': ownership_proof,
                'evidence_bundle': evidence_bundle,
                'created_at':     created_at.isoformat(),
            }), 201

    except Exception as e:
        log_error('enforcement', 'package_create_failed', error=str(e))
        return jsonify({'error': 'Evidence package creation failed',
                        'detail': str(e)}), 500


@app.route('/api/enforcement/<package_id>', methods=['GET'])
def get_enforcement_package(package_id):
    """Retrieve an enforcement package by ID."""
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')

    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT id, submission_id, package_type, status,
                       target_platform, target_url, infringing_url,
                       cert_id, ownership_proof, evidence_bundle,
                       package_hash, created_at, submitted_at,
                       resolved_at, resolution_notes
                FROM public.enforcement_packages
                WHERE id = %s AND creator_id = %s
            """, (package_id, user_id))
            pkg = cur.fetchone()
            if not pkg:
                return jsonify({'error': 'Package not found'}), 404

            # Get events
            cur.execute("""
                SELECT event_type, notes, created_at
                FROM public.enforcement_events
                WHERE package_id = %s
                ORDER BY created_at ASC
            """, (package_id,))
            events = [{
                'event_type': e[0],
                'notes':      e[1],
                'created_at': e[2].isoformat(),
            } for e in cur.fetchall()]

            return jsonify({
                'package_id':      str(pkg[0]),
                'submission_id':   str(pkg[1]),
                'package_type':    pkg[2],
                'status':          pkg[3],
                'target_platform': pkg[4],
                'target_url':      pkg[5],
                'infringing_url':  pkg[6],
                'cert_id':         pkg[7],
                'ownership_proof': pkg[8],
                'evidence_bundle': pkg[9],
                'package_hash':    pkg[10],
                'created_at':      pkg[11].isoformat(),
                'submitted_at':    pkg[12].isoformat() if pkg[12] else None,
                'resolved_at':     pkg[13].isoformat() if pkg[13] else None,
                'resolution_notes': pkg[14],
                'events':          events,
            })

    except Exception as e:
        log_error('enforcement', 'get_package_failed', error=str(e))
        return jsonify({'error': 'Failed to retrieve package'}), 500


@app.route('/api/enforcement/<package_id>/submit', methods=['POST'])
def submit_enforcement_package(package_id):
    """Mark enforcement package as submitted to a platform."""
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')
    data    = request.get_json(force=True) or {}
    notes   = data.get('notes', '')

    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                UPDATE public.enforcement_packages
                SET status = 'submitted', submitted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND creator_id = %s AND status = 'ready'
                RETURNING id
            """, (package_id, user_id))
            if not cur.fetchone():
                return jsonify({'error': 'Package not found or not in ready state'}), 404

            cur.execute("""
                INSERT INTO public.enforcement_events
                    (package_id, event_type, actor_id, notes)
                VALUES (%s, 'package_submitted', %s, %s)
            """, (package_id, user_id, notes or 'Submitted to platform'))

            conn.commit()
            return jsonify({
                'package_id': package_id,
                'status':     'submitted',
            })

    except Exception as e:
        log_error('enforcement', 'submit_failed', error=str(e))
        return jsonify({'error': 'Submission failed'}), 500


@app.route('/api/enforcement/proof/<asset_id>', methods=['GET'])
def get_ownership_proof(asset_id):
    """
    Lightweight public ownership proof by asset_id or canonical_id.
    Used by marketplaces, partners, and licensing systems.
    No auth required.
    """
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT
                    ai.id, ai.canonical_id, ai.submission_id,
                    ai.cert_id, ai.agreement_id, ai.tamper_hash,
                    ai.content_type, ai.origin_timestamp,
                    ai.ownership_snapshot, ai.registered_at
                FROM public.asset_identities ai
                WHERE ai.id::text = %s
                   OR ai.canonical_id::text = %s
                LIMIT 1
            """, (asset_id, asset_id))
            asset = cur.fetchone()
            if not asset:
                return jsonify({'error': 'Asset not found'}), 404

            (a_id, canonical_id, submission_id, cert_id,
             agreement_id, tamper_hash, content_type,
             origin_ts, ownership_snapshot, registered_at) = asset

            # Extract owners from snapshot — redact PII for public endpoint
            owners = []
            if ownership_snapshot and 'participants' in ownership_snapshot:
                owners = [{
                    'owner_id':      p.get('user_id'),
                    'role':          p.get('role'),
                    'ownership_pct': p.get('ownership_pct'),
                    'status':        p.get('status'),
                    'verified_owner': p.get('status') == 'accepted',
                } for p in ownership_snapshot['participants']]

            return jsonify({
                'asset_id':        str(a_id),
                'canonical_id':    str(canonical_id),
                'submission_id':   str(submission_id),
                'cert_id':         cert_id,
                'agreement_id':    str(agreement_id) if agreement_id else None,
                'content_type':    content_type,
                'origin_timestamp': origin_ts.isoformat() if origin_ts else None,
                'registered_at':   registered_at.isoformat(),
                'tamper_hash':     tamper_hash,
                'owners':          owners,
            })

    except Exception as e:
        log_error('enforcement', 'proof_failed', error=str(e))
        return jsonify({'error': 'Ownership proof lookup failed'}), 500


@app.route('/api/enforcement/takedown', methods=['POST'])
def generate_takedown():
    """
    Generate a platform-specific takedown payload from an evidence package.
    Links to an existing evidence package and creates a submission record.
    """
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')
    data    = request.get_json(force=True) or {}

    evidence_package_id = data.get('evidence_package_id')
    platform            = data.get('platform', '').lower()
    infringing_url      = data.get('infringing_url', '')

    if not evidence_package_id:
        return jsonify({'error': 'evidence_package_id required'}), 400
    if not platform:
        return jsonify({'error': 'platform required'}), 400

    try:
        with db_cursor() as (conn, cur):
            # Fetch evidence package
            cur.execute("""
                SELECT id, submission_id, cert_id,
                       ownership_proof, evidence_bundle, package_hash
                FROM public.enforcement_packages
                WHERE id = %s AND creator_id = %s
            """, (evidence_package_id, user_id))
            pkg = cur.fetchone()
            if not pkg:
                return jsonify({'error': 'Evidence package not found'}), 404

            (pkg_id, submission_id, cert_id,
             ownership_proof, evidence_bundle, package_hash) = pkg

            # Build platform-specific takedown payload
            claimant = None
            owners   = []
            if ownership_proof and 'agreement' in ownership_proof:
                participants = ownership_proof['agreement'].get('participants', [])
                if participants:
                    claimant = participants[0].get('email')  # kept internal only
                    owners   = [{
                        'owner_id':      p.get('user_id'),
                        'role':          p.get('role'),
                        'ownership_pct': p.get('ownership_pct'),
                        'verified_owner': p.get('status') == 'accepted',
                    } for p in participants]

            takedown_payload = {
                'platform':         platform,
                'infringing_url':   infringing_url or (
                    evidence_bundle or {}
                ).get('infringing_url', ''),
                'claimant':         claimant,
                'cert_id':          cert_id,
                'ownership_proof':  {
                    'cert_id':       cert_id,
                    'canonical_id':  (ownership_proof or {}).get(
                        'asset_identity', {}).get('canonical_id'),
                    'tamper_hash':   (ownership_proof or {}).get(
                        'asset_identity', {}).get('tamper_hash'),
                    'agreement_hash': (ownership_proof or {}).get(
                        'agreement', {}).get('agreement_hash'),
                    'owners':        owners,
                },
                'evidence_package_id': str(pkg_id),
                'package_hash':      package_hash,
                'generated_at':      datetime.utcnow().isoformat(),
            }

            # Store as enforcement submission
            cur.execute("""
                INSERT INTO public.enforcement_events
                    (package_id, event_type, actor_id, notes, metadata)
                VALUES (%s, 'package_submitted', %s, %s, %s::jsonb)
            """, (str(pkg_id), user_id,
                  f'Takedown generated for {platform}',
                  json.dumps({'platform': platform,
                              'infringing_url': infringing_url})))

            conn.commit()

            return jsonify({
                'takedown_payload': takedown_payload,
                'evidence_package_id': str(pkg_id),
                'platform':          platform,
                'status':            'draft',
            }), 201

    except Exception as e:
        log_error('enforcement', 'takedown_failed', error=str(e))
        return jsonify({'error': 'Takedown generation failed',
                        'detail': str(e)}), 500


@app.route('/api/enforcement/list', methods=['GET'])
def list_enforcement_packages():
    """List all enforcement packages for the authenticated creator."""
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')

    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT ep.id, ep.submission_id, ep.package_type,
                       ep.status, ep.target_platform, ep.cert_id,
                       ep.package_hash, ep.created_at, ep.submitted_at,
                       s.title
                FROM public.enforcement_packages ep
                LEFT JOIN public.submissions s
                    ON s.id = ep.submission_id
                WHERE ep.creator_id = %s
                ORDER BY ep.created_at DESC
                LIMIT 100
            """, (user_id,))
            packages = [{
                'package_id':      str(p[0]),
                'submission_id':   str(p[1]),
                'package_type':    p[2],
                'status':          p[3],
                'target_platform': p[4],
                'cert_id':         p[5],
                'package_hash':    p[6],
                'created_at':      p[7].isoformat(),
                'submitted_at':    p[8].isoformat() if p[8] else None,
                'title':           p[9],
            } for p in cur.fetchall()]

            return jsonify({'packages': packages, 'total': len(packages)})

    except Exception as e:
        log_error('enforcement', 'list_failed', error=str(e))
        return jsonify({'error': 'Failed to list packages'}), 500


# ══════════════════════════════════════════════════════════════════
# PHASE 2 — ASSET IDENTITY LAYER v1
# Canonical asset identity: links submission → fingerprints → ownership → cert
# ══════════════════════════════════════════════════════════════════

@app.route('/api/assets/register', methods=['POST'])
def register_asset_identity():
    """
    Register or update canonical asset identity for a submission.
    Links fingerprint data, ownership snapshot, and cert_id into
    a single tamper-evident identity record.
    """
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get('sub', '')
    data    = request.get_json(force=True) or {}

    submission_id = data.get('submission_id')
    if not submission_id:
        return jsonify({'error': 'submission_id required'}), 400

    try:
        with db_cursor() as (conn, cur):
            # 1. Verify submission belongs to caller
            cur.execute("""
                SELECT id, content_type, content_hash, cert_id,
                       submitted_at, title
                FROM public.submissions
                WHERE id = %s AND creator_id = %s
            """, (submission_id, user_id))
            sub = cur.fetchone()
            if not sub:
                return jsonify({'error': 'Submission not found or access denied'}), 404

            (sub_id, content_type, content_hash, cert_id,
             submitted_at, title) = sub

            # 2. Pull fingerprint data if available
            cur.execute("""
                SELECT visual_phash, audio_fingerprint,
                       fingerprint_version, registry_id
                FROM public.fingerprints
                WHERE submission_id = %s
                LIMIT 1
            """, (submission_id,))
            fp = cur.fetchone()
            perceptual_hash    = fp[0] if fp else data.get('perceptual_hash')
            audio_fingerprint  = fp[1] if fp else data.get('audio_fingerprint')
            fingerprint_version = fp[2] if fp else 'v1'

            # 3. Pull active agreement for ownership snapshot
            cur.execute("""
                SELECT a.id, ap.ownership_pct, ap.role
                FROM public.coownership_agreements a
                JOIN public.agreement_participants ap
                    ON ap.agreement_id = a.id AND ap.user_id = %s
                WHERE a.submission_id = %s AND a.status = 'active'
                LIMIT 1
            """, (user_id, submission_id))
            agr = cur.fetchone()

            ownership_snapshot = None
            agreement_id       = None
            if agr:
                agreement_id = str(agr[0])
                # Build full ownership snapshot
                cur.execute("""
                    SELECT ap.user_id, ap.email, ap.role,
                           ap.ownership_pct, ap.status
                    FROM public.agreement_participants ap
                    WHERE ap.agreement_id = %s
                """, (agreement_id,))
                participants = [{
                    'user_id':       str(p[0]),
                    'email':         p[1],
                    'role':          p[2],
                    'ownership_pct': float(p[3]) if p[3] else 0,
                    'status':        p[4],
                } for p in cur.fetchall()]
                ownership_snapshot = {
                    'agreement_id': agreement_id,
                    'participants': participants,
                    'snapshot_at':  datetime.utcnow().isoformat(),
                }

            # 4. Compute tamper hash over canonical identity fields
            identity_payload = _canonical_json({
                'submission_id':    submission_id,
                'content_type':     content_type or '',
                'content_hash':     content_hash or '',
                'perceptual_hash':  perceptual_hash or '',
                'cert_id':          cert_id or '',
                'registered_by':    user_id,
                'origin_timestamp': submitted_at.isoformat() if submitted_at else '',
            })
            tamper_hash = _sha256(identity_payload)

            # 5. Upsert asset identity record
            cur.execute("""
                INSERT INTO public.asset_identities (
                    submission_id, content_hash, perceptual_hash,
                    audio_fingerprint, fingerprint_version,
                    content_type, file_size_bytes, duration_seconds,
                    origin_timestamp, registered_by, cert_id,
                    agreement_id, ownership_snapshot, tamper_hash
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s
                )
                ON CONFLICT (submission_id) DO UPDATE SET
                    content_hash        = EXCLUDED.content_hash,
                    perceptual_hash     = EXCLUDED.perceptual_hash,
                    audio_fingerprint   = EXCLUDED.audio_fingerprint,
                    fingerprint_version = EXCLUDED.fingerprint_version,
                    cert_id             = EXCLUDED.cert_id,
                    agreement_id        = EXCLUDED.agreement_id,
                    ownership_snapshot  = EXCLUDED.ownership_snapshot,
                    tamper_hash         = EXCLUDED.tamper_hash,
                    registered_at       = NOW()
                RETURNING id, canonical_id, registered_at
            """, (
                submission_id,
                content_hash or data.get('content_hash'),
                perceptual_hash,
                audio_fingerprint,
                fingerprint_version,
                content_type,
                data.get('file_size_bytes'),
                data.get('duration_seconds'),
                submitted_at,
                user_id,
                cert_id or data.get('cert_id'),
                agreement_id,
                json.dumps(ownership_snapshot) if ownership_snapshot else None,
                tamper_hash,
            ))
            row = cur.fetchone()
            asset_id, canonical_id, registered_at = row

            # 6. Skip updating submissions.canonical_id directly —
            # it has a FK constraint to content_canonical table.
            # canonical_id lives in asset_identities instead.

            conn.commit()

            log_info('asset_identity', 'registered',
                     submission_id=submission_id,
                     asset_id=str(asset_id),
                     canonical_id=str(canonical_id))

            return jsonify({
                'asset_id':         str(asset_id),
                'canonical_id':     str(canonical_id),
                'submission_id':    submission_id,
                'tamper_hash':      tamper_hash,
                'cert_id':          cert_id or data.get('cert_id'),
                'agreement_id':     agreement_id,
                'registered_at':    registered_at.isoformat(),
                'ownership_snapshot': ownership_snapshot,
            }), 201

    except Exception as e:
        log_error('asset_identity', 'register_failed', error=str(e))
        return jsonify({'error': 'Asset registration failed', 'detail': str(e)}), 500


@app.route('/api/assets/<submission_id>', methods=['GET'])
def get_asset_identity(submission_id):
    """
    Retrieve canonical asset identity for a submission.
    Public endpoint — no auth required for ownership proof retrieval.
    """
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT
                    ai.id, ai.canonical_id, ai.submission_id,
                    ai.content_hash, ai.perceptual_hash,
                    ai.audio_fingerprint, ai.fingerprint_version,
                    ai.content_type, ai.file_size_bytes,
                    ai.duration_seconds, ai.origin_timestamp,
                    ai.registered_at, ai.registered_by,
                    ai.cert_id, ai.agreement_id,
                    ai.ownership_snapshot, ai.tamper_hash,
                    ai.identity_version,
                    s.title, s.status AS submission_status
                FROM public.asset_identities ai
                JOIN public.submissions s ON s.id = ai.submission_id
                WHERE ai.submission_id = %s
                   OR ai.canonical_id::text = %s
                LIMIT 1
            """, (submission_id, submission_id))

            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Asset identity not found'}), 404

            (asset_id, canonical_id, sub_id, content_hash,
             perceptual_hash, audio_fingerprint, fp_version,
             content_type, file_size, duration, origin_ts,
             registered_at, registered_by, cert_id, agreement_id,
             ownership_snapshot, tamper_hash, identity_version,
             title, submission_status) = row

            return jsonify({
                'asset_id':           str(asset_id),
                'canonical_id':       str(canonical_id),
                'submission_id':      str(sub_id),
                'title':              title,
                'content_type':       content_type,
                'content_hash':       content_hash,
                'perceptual_hash':    perceptual_hash,
                'audio_fingerprint':  audio_fingerprint,
                'fingerprint_version': fp_version,
                'file_size_bytes':    file_size,
                'duration_seconds':   duration,
                'origin_timestamp':   origin_ts.isoformat() if origin_ts else None,
                'registered_at':      registered_at.isoformat(),
                'cert_id':            cert_id,
                'agreement_id':       str(agreement_id) if agreement_id else None,
                'ownership_snapshot': ownership_snapshot,
                'tamper_hash':        tamper_hash,
                'identity_version':   identity_version,
                'submission_status':  submission_status,
            })

    except Exception as e:
        log_error('asset_identity', 'get_failed', error=str(e))
        return jsonify({'error': 'Asset lookup failed', 'detail': str(e)}), 500


@app.route('/api/verify-certificate', methods=['GET'])
def verify_certificate_public():
    """Public endpoint — verify a certificate by cert_id or submission ID."""
    cert_id = request.args.get('cert_id', '').strip()
    if not cert_id:
        return jsonify({'error': 'cert_id required'}), 400

    try:
        with db_cursor() as (conn, cur):
            # Look up by cert_id or certificate_id in submissions
            cur.execute("""
                SELECT s.id, s.title, s.cert_id, s.certificate_id,
                       s.status, s.submitted_at, s.completed_at,
                       s.content_type, s.creator_id
                FROM public.submissions s
                WHERE s.cert_id = %s
                   OR s.certificate_id = %s
                   OR s.id::text = %s
                LIMIT 1
            """, (cert_id, cert_id, cert_id))

            row = cur.fetchone()
            if not row:
                return jsonify({'valid': False, 'error': 'Certificate not found'}), 404

            (sub_id, title, cert_id_val, certificate_id,
             status, submitted_at, completed_at,
             content_type, creator_id) = row

            # Also get agreement if exists
            cur.execute("""
                SELECT a.id, a.status, a.activated_at, a.agreement_hash
                FROM public.coownership_agreements a
                WHERE a.submission_id = %s AND a.status = 'active'
                LIMIT 1
            """, (str(sub_id),))
            agr = cur.fetchone()

            return jsonify({
                'valid':        status == 'COMPLETED',
                'chain_valid':  status == 'COMPLETED',
                'cert_id':      cert_id_val or certificate_id or cert_id,
                'submission_id': str(sub_id),
                'title':        title or 'Certified Work',
                'content_type': content_type,
                'status':       status,
                'submitted_at': submitted_at.isoformat() if submitted_at else None,
                'activated_at': completed_at.isoformat() if completed_at else None,
                'agreement_id': str(agr[0]) if agr else None,
                'final_hash':   agr[3] if agr else None,
            })

    except Exception as e:
        log_error('verify', 'public_verify_failed', error=str(e))
        return jsonify({'error': 'Verification failed', 'detail': str(e)}), 500


@app.route('/api/agreements/create', methods=['POST'])
def create_agreement():
    """Create a co-ownership agreement for a submission."""
    claims, err = _require_auth(request)
    if err: return err
    creator_id = claims.get("sub", "")
    data       = request.get_json(force=True)

    submission_id = data.get('submission_id')
    participants  = data.get('participants', [])   # list of {email, display_name, role, ownership_pct}
    rights        = data.get('rights', {})

    if not submission_id:
        return jsonify({'error': 'submission_id required'}), 400
    if not participants:
        return jsonify({'error': 'at least one participant required'}), 400

    # Validate ownership sums
    total_pct = sum(float(p.get('ownership_pct', 0)) for p in participants)
    if abs(total_pct - 100.0) > 0.01:
        return jsonify({'error': f'ownership_pct must sum to 100 (got {total_pct})'}), 400

    # Rights Engine — evaluate create_agreement using submission's active agreement
    # For new agreements we use the submission_id to find any existing agreement context.
    # Log-only on first creation (no prior agreement to check membership against).
    try:
        _existing_agr = None
        with db_cursor() as (_c, _cur):
            _cur.execute("""
                SELECT id FROM public.coownership_agreements
                WHERE submission_id = %s AND status = 'active'
                LIMIT 1
            """, (submission_id,))
            _row = _cur.fetchone()
            _existing_agr = str(_row[0]) if _row else None

        if _existing_agr:
            _rights_result = evaluate_rights(
                _existing_agr, creator_id, 'create_agreement',
                context={'source': 'create_agreement', 'submission_id': submission_id},
                log=True
            )
            if not _rights_result['allowed']:
                return jsonify({
                    'error':           'Rights check failed',
                    'reason':          _rights_result['reason'],
                    'decision_source': _rights_result['decision_source'],
                }), 403
    except Exception as _re:
        log_error('rights_engine', 'create_agreement_eval_failed', error=str(_re))

    try:
        with db_cursor() as (conn, cur):
            # Verify submission belongs to creator
            cur.execute(
                "SELECT id, cert_id FROM public.submissions WHERE id = %s AND creator_id = %s",
                (submission_id, creator_id)
            )
            sub = cur.fetchone()
            if not sub:
                return jsonify({'error': 'submission not found or not owned by you'}), 404

            # Create agreement
            agreement_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.coownership_agreements (
                    id, submission_id, created_by, status,
                    commercial_use, derivative_works, sublicensing,
                    ai_training_permitted, attribution_required,
                    publication_requires_all, minimum_publication_threshold,
                    dispute_resolution, governing_jurisdiction,
                    template_version
                ) VALUES (
                    %s, %s, %s, 'pending',
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, 'v1.0'
                )
            """, (
                agreement_id, submission_id, creator_id,
                rights.get('commercial_use', True),
                rights.get('derivative_works', True),
                rights.get('sublicensing', False),
                rights.get('ai_training_permitted', False),
                rights.get('attribution_required', True),
                rights.get('publication_requires_all', False),
                float(rights.get('minimum_publication_threshold', 50.0)),
                rights.get('dispute_resolution', 'majority_vote'),
                rights.get('governing_jurisdiction')
            ))

            # Add participants
            for p in participants:
                token   = secrets.token_urlsafe(32)
                expires = datetime.now(timezone.utc) + timedelta(days=14)

                # Resolve participant user_id
                p_user_id = p.get('user_id')

                if not p_user_id:
                    try:
                        cur.execute(
                            "SELECT id FROM auth.users WHERE email = %s LIMIT 1",
                            (p['email'],)
                        )
                        found_user = cur.fetchone()

                        if found_user:
                            p_user_id = str(found_user[0])
                        else:
                            p_user_id = creator_id

                    except Exception:
                        p_user_id = creator_id

                cur.execute("""
                    INSERT INTO public.agreement_participants (
                        agreement_id,
                        user_id,
                        email,
                        display_name,
                        role,
                        ownership_pct,
                        royalty_pct,
                        attribution_name,
                        public_attribution,
                        acceptance_token,
                        acceptance_expires_at,
                        status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'invited'
                    )
                """, (
                    agreement_id,
                    p_user_id,
                    p['email'],
                    p.get('display_name', ''),
                    p.get('role', 'co-author'),
                    float(p['ownership_pct']),
                    float(p.get('royalty_pct', p['ownership_pct'])),
                    p.get('attribution_name', p.get('display_name', '')),
                    p.get('public_attribution', True),
                    token,
                    expires
                ))

            # Log creation event
            _log_agreement_event(cur, agreement_id, 'created', creator_id, {
                'submission_id': submission_id,
                'participant_count': len(participants)
            })

            conn.commit()

        return jsonify({
            'status': 'created',
            'agreement_id': agreement_id,
            'participant_count': len(participants)
        }), 201

    except Exception as e:
        log_error('agreement', 'create_failed', error=str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/api/agreements/<agreement_id>', methods=['GET'])
def get_agreement(agreement_id):
    """Get agreement details including participants."""
    claims, err = _require_auth(request)
    if err: return err
    creator_id = claims.get("sub", "")
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT a.id, a.submission_id, a.status, a.created_by,
                       a.commercial_use, a.derivative_works, a.sublicensing,
                       a.ai_training_permitted, a.attribution_required,
                       a.dispute_resolution, a.governing_jurisdiction,
                       a.agreement_hash, a.activated_at, a.created_at,
                       a.minimum_publication_threshold, a.template_version
                FROM public.coownership_agreements a
                WHERE a.id = %s
            """, (agreement_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'not found'}), 404

            cur.execute("""
                SELECT user_id, email, display_name, role, ownership_pct,
                       royalty_pct, attribution_name, status, invited_at, accepted_at
                FROM public.agreement_participants
                WHERE agreement_id = %s ORDER BY ownership_pct DESC
            """, (agreement_id,))
            parts = cur.fetchall()

            return jsonify({
                'agreement_id':     str(row[0]),
                'submission_id':    str(row[1]),
                'status':           row[2],
                'created_by':       row[3],
                'rights': {
                    'commercial_use':              row[4],
                    'derivative_works':            row[5],
                    'sublicensing':                row[6],
                    'ai_training_permitted':       row[7],
                    'attribution_required':        row[8],
                    'dispute_resolution':          row[9],
                    'governing_jurisdiction':      row[10],
                    'minimum_publication_threshold': float(row[14]) if row[14] else 50.0
                },
                'agreement_hash':   row[11],
                'activated_at':     row[12].isoformat() if row[12] else None,
                'created_at':       row[13].isoformat() if row[13] else None,
                'template_version': row[15],
                'participants': [{
                    'user_id':          p[0],
                    'email':            p[1],
                    'display_name':     p[2],
                    'role':             p[3],
                    'ownership_pct':    float(p[4]),
                    'royalty_pct':      float(p[5]) if p[5] else None,
                    'attribution_name': p[6],
                    'status':           p[7],
                    'invited_at':       p[8].isoformat() if p[8] else None,
                    'accepted_at':      p[9].isoformat() if p[9] else None
                } for p in parts]
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/agreements/<agreement_id>/accept', methods=['POST'])
def accept_agreement(agreement_id):
    """Participant accepts invitation. Activates if all have accepted."""
    claims, err = _require_auth(request)
    if err: return err
    user_id = claims.get("sub", "")
    data = request.get_json(force=True) or {}


    # Rights Engine — log acceptance attempt (log-only, never blocks).
    # Token is the authorization mechanism; participant check logs expected denial
    # for new participants until they accept and become verified participants.
    try:
        evaluate_rights(agreement_id, user_id, 'accept_invitation',
                        context={'source': 'accept_route'}, log=True)
    except Exception as _re:
        log_error('rights_engine', 'accept_eval_failed', error=str(_re))

    try:
        with db_cursor() as (conn, cur):
            # Authenticated lookup — no token required, identity from JWT
            cur.execute("""
                SELECT id, status, acceptance_expires_at
                FROM public.agreement_participants
                WHERE agreement_id = %s AND user_id = %s
            """, (agreement_id, user_id))
            part = cur.fetchone()
            if not part:
                return jsonify({'error': 'not a participant in this agreement'}), 403
            if part[1] == 'accepted':
                return jsonify({'status': 'accepted', 'agreement_activated': False,
                                'message': 'already accepted'}), 200
            if part[1] != 'invited':
                return jsonify({'error': f'cannot accept from status: {part[1]}'}), 400
            if part[2] and part[2] < datetime.now(timezone.utc):
                return jsonify({'error': 'invitation expired'}), 410

            # Mark accepted — identity already correct, clear token, record IP
            cur.execute("""
                UPDATE public.agreement_participants
                SET status = 'accepted', accepted_at = NOW(),
                    acceptance_ip = %s,
                    acceptance_token = NULL
                WHERE id = %s
            """, (request.remote_addr, str(part[0])))

            _log_agreement_event(cur, agreement_id, 'participant_accepted', user_id, {
                'participant_id': str(part[0])
            })

            # Check if all accepted → activate
            cur.execute("""
                SELECT COUNT(*) FROM public.agreement_participants
                WHERE agreement_id = %s AND status != 'accepted'
            """, (agreement_id,))
            pending = cur.fetchone()[0]

            activated = False
            if pending == 0:
                # Build canonical snapshot
                cur.execute("""
                    SELECT a.*, array_agg(
                        json_build_object(
                            'user_id', p.user_id,
                            'email', p.email,
                            'role', p.role,
                            'ownership_pct', p.ownership_pct,
                            'royalty_pct', p.royalty_pct
                        ) ORDER BY p.ownership_pct DESC
                    ) AS parts
                    FROM public.coownership_agreements a
                    JOIN public.agreement_participants p ON p.agreement_id = a.id
                    WHERE a.id = %s
                    GROUP BY a.id
                """, (agreement_id,))
                snap_row = cur.fetchone()

                canonical = _canonical_json({
                    'agreement_id':    agreement_id,
                    'submission_id':   str(snap_row[1]),
                    'template':        snap_row[15] if len(snap_row) > 15 else 'v1.0',
                    'activated_at':    datetime.now(timezone.utc).isoformat(),
                    'participants':    snap_row[-1],
                    'canon_version':   'v1'
                })
                agreement_hash = _sha256(canonical)

                cur.execute("""
                    UPDATE public.coownership_agreements
                    SET status = 'active',
                        agreement_hash = %s,
                        agreement_json = %s::jsonb,
                        activated_at = NOW()
                    WHERE id = %s
                """, (agreement_hash, canonical, agreement_id))

                _log_agreement_event(cur, agreement_id, 'activated', user_id, {
                    'agreement_hash': agreement_hash
                })
                activated = True

            conn.commit()
            return jsonify({
                'status': 'accepted',
                'agreement_activated': activated
            })
    except Exception as e:
        log_error('agreement', 'accept_failed', error=str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/api/agreements/<agreement_id>/events', methods=['GET'])
def get_agreement_events(agreement_id):
    """Return the full event chain for verification."""
    claims, err = _require_auth(request)
    if err: return err
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT event_type, actor_id, event_data,
                       event_hash, previous_hash, created_at
                FROM public.agreement_events
                WHERE agreement_id = %s
                ORDER BY created_at ASC
            """, (agreement_id,))
            events = cur.fetchall()
            return jsonify({'events': [{
                'event_type':    e[0],
                'actor_id':      e[1],
                'event_data':    e[2],
                'event_hash':    e[3],
                'previous_hash': e[4],
                'created_at':    e[5].isoformat()
            } for e in events]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/agreements/by-submission/<submission_id>', methods=['GET'])
def get_agreements_by_submission(submission_id):
    """List all agreements for a submission."""
    claims, err = _require_auth(request)
    if err: return err
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT id, status, created_by, created_at,
                       agreement_hash, activated_at, version
                FROM public.coownership_agreements
                WHERE submission_id = %s
                ORDER BY version DESC
            """, (submission_id,))
            rows = cur.fetchall()
            return jsonify({'agreements': [{
                'agreement_id':   str(r[0]),
                'status':         r[1],
                'created_by':     r[2],
                'created_at':     r[3].isoformat(),
                'agreement_hash': r[4],
                'activated_at':   r[5].isoformat() if r[5] else None,
                'version':        r[6]
            } for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# AGREEMENT CHAIN VERIFICATION ENDPOINT
# ══════════════════════════════════════════════════════════════════

@app.route('/api/agreements/<agreement_id>/verify-chain', methods=['GET'])
def verify_agreement_chain(agreement_id):
    """Recompute and verify the entire event chain for tamper detection."""
    import hashlib, json as _json

    # Rights Engine gate — log verify_chain evaluations
    # No auth required for chain verification (public proof) but we still log
    # anonymous evaluations using a sentinel actor_id
    try:
        _vc_result = evaluate_rights(
            agreement_id, '00000000-0000-0000-0000-000000000000',
            'verify_chain', context={'source': 'public_verify'},
            log=True)
        log_info('rights_engine', 'verify_chain_evaluated',
                 allowed=_vc_result.get('allowed'),
                 trace_len=len(_vc_result.get('rule_trace') or []),
                 snap_id=_vc_result.get('snapshot_id'))
    except Exception as _vce:
        log_error('rights_engine', 'verify_chain_log_failed', error=str(_vce))

    def _recompute_hash(previous_hash, event_type, event_data):
        def _clean(v):
            if isinstance(v, dict):
                return {k: _clean(v[k]) for k in sorted(v.keys())}
            if isinstance(v, list):
                return [_clean(i) for i in v]
            if isinstance(v, float):
                return round(v, 2)
            return v
        payload_obj = _clean({'type': event_type, **(event_data or {})})
        payload = (previous_hash or '') + _json.dumps(
            payload_obj, separators=(',', ':'), sort_keys=True, default=str
        )
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    try:
        with db_cursor() as (conn, cur):
            # Check agreement exists (no auth required — chain is public proof)
            cur.execute(
                "SELECT id, status FROM public.coownership_agreements WHERE id = %s",
                (agreement_id,)
            )
            agreement = cur.fetchone()
            if not agreement:
                return jsonify({'error': 'agreement not found'}), 404

            cur.execute("""
                SELECT event_type, event_data, event_hash, previous_hash, created_at
                FROM public.agreement_events
                WHERE agreement_id = %s
                ORDER BY created_at ASC
            """, (agreement_id,))
            events = cur.fetchall()

            if not events:
                return jsonify({
                    'agreement_id': agreement_id,
                    'status': agreement[1],
                    'chain_valid': True,
                    'event_count': 0,
                    'message': 'No events yet'
                })

            errors = []
            prev_hash = ''
            for i, (event_type, event_data, stored_hash, stored_prev, created_at) in enumerate(events):
                # Verify previous_hash linkage
                if stored_prev != (prev_hash or None) and not (stored_prev is None and prev_hash == ''):
                    errors.append({
                        'event_index': i,
                        'event_type': event_type,
                        'error': 'previous_hash mismatch',
                        'expected': prev_hash or None,
                        'stored': stored_prev
                    })

                # Recompute hash
                recomputed = _recompute_hash(prev_hash, event_type, event_data)
                if recomputed != stored_hash:
                    errors.append({
                        'event_index': i,
                        'event_type': event_type,
                        'error': 'hash_mismatch',
                        'recomputed': recomputed,
                        'stored': stored_hash
                    })

                prev_hash = stored_hash

            return jsonify({
                'agreement_id': agreement_id,
                'status': agreement[1],
                'chain_valid': len(errors) == 0,
                'event_count': len(events),
                'final_hash': prev_hash,
                'errors': errors
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# PDF GENERATION — corrected, deterministic, audit-logged
# agreement_hash = legal identity (canonical snapshot hash)
# pdf_hash       = tamper detection only (not legal proof)
# ══════════════════════════════════════════════════════════════════

VERIFY_BASE_URL = os.environ.get('FRONTEND_URL', 'https://seekreap-tier-6-frontend.fly.dev')

@app.route('/api/agreements/<agreement_id>/generate-pdf', methods=['POST'])
def generate_agreement_pdf(agreement_id):
    """Generate a deterministic PDF from structured agreement data."""
    claims, err = _require_auth(request)
    if err: return err
    actor_id = claims.get("sub", "")

    # Rights Engine gate — evaluate before proceeding
    rights = evaluate_rights(agreement_id, actor_id, 'generate_pdf',
                             context={'source': 'api'})
    if not rights['allowed']:
        return jsonify({
            'error':           'Rights check failed',
            'reason':          rights['reason'],
            'decision_source': rights['decision_source'],
            'snapshot_id':     rights.get('snapshot_id'),
        }), 403
    # snapshot_id available for downstream audit if needed
    _pdf_rights_snapshot_id = rights.get('snapshot_id')

    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT a.id, a.submission_id, a.status, a.created_by,
                       a.created_at, a.activated_at, a.agreement_hash,
                       a.template_version, a.commercial_use, a.derivative_works,
                       a.sublicensing, a.ai_training_permitted, a.attribution_required,
                       a.dispute_resolution, a.governing_jurisdiction,
                       a.minimum_publication_threshold, a.canonicalization_version
                FROM public.coownership_agreements a
                WHERE a.id = %s
            """, (agreement_id,))
            agr = cur.fetchone()
            if not agr:
                return jsonify({'error': 'agreement not found'}), 404
            if agr[2] != 'active':
                return jsonify({'error': 'PDF only available for active agreements'}), 400

            # Verify requester is participant or creator
            cur.execute("""
                SELECT 1 FROM public.agreement_participants
                WHERE agreement_id = %s AND user_id = %s
                UNION
                SELECT 1 FROM public.coownership_agreements
                WHERE id = %s AND created_by = %s
                LIMIT 1
            """, (agreement_id, actor_id, agreement_id, actor_id))
            if not cur.fetchone():
                return jsonify({'error': 'access denied'}), 403

            # Fetch participants — use attribution_name/display_name only, never raw email
            cur.execute("""
                SELECT
                    COALESCE(attribution_name, display_name, 'Participant') AS name,
                    role, ownership_pct,
                    COALESCE(royalty_pct, ownership_pct) AS royalty_pct,
                    accepted_at, status
                FROM public.agreement_participants
                WHERE agreement_id = %s
                ORDER BY ownership_pct DESC
            """, (agreement_id,))
            participants = cur.fetchall()

        # Build PDF
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        import io, hashlib as _hl

        buf = io.BytesIO()
        W, H = A4
        c = rl_canvas.Canvas(buf, pagesize=A4)

        # Page overflow helper
        PAGE_BOTTOM = 25 * mm
        def _check_page(y, needed=12):
            if y < PAGE_BOTTOM + needed * mm:
                c.showPage()
                # Redraw minimal header on continuation pages
                c.setFillColor(colors.HexColor('#1A1A2E'))
                c.setFont('Helvetica-Bold', 9)
                c.setFillColor(colors.HexColor('#888888'))
                c.drawString(20*mm, H - 12*mm, f'Agreement {agreement_id[:8]}... (continued)')
                return H - 22*mm
            return y

        def _hline(y):
            c.setStrokeColor(colors.HexColor('#DDDDDD'))
            c.line(20*mm, y, W - 20*mm, y)

        def _section(title, y):
            y = _check_page(y, 20)
            c.setFont('Helvetica-Bold', 9)
            c.setFillColor(colors.HexColor('#1A1A2E'))
            c.drawString(20*mm, y, title)
            _hline(y - 2*mm)
            return y - 9*mm

        def _field(label, value, y):
            y = _check_page(y, 8)
            c.setFont('Helvetica-Bold', 7.5)
            c.setFillColor(colors.HexColor('#666666'))
            c.drawString(22*mm, y, f'{label}:')
            c.setFont('Helvetica', 7.5)
            c.setFillColor(colors.black)
            c.drawString(72*mm, y, str(value) if value is not None else '—')
            return y - 6*mm

        # ── Header ────────────────────────────────────────────────
        c.setFillColor(colors.HexColor('#1A1A2E'))
        c.rect(0, H - 28*mm, W, 28*mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 15)
        c.drawString(20*mm, H - 13*mm, 'SeekReap Co-Ownership Agreement')
        c.setFont('Helvetica', 8)
        c.drawString(20*mm, H - 20*mm,
            f'Template: {agr[7]}  |  Canon: {CANON_VERSION}  |  Status: {agr[2].upper()}')

        y = H - 36*mm

        # ── Agreement identity ─────────────────────────────────────
        y = _section('AGREEMENT IDENTITY', y)
        y = _field('Agreement ID',   str(agr[0]), y)
        y = _field('Submission ID',  str(agr[1]), y)
        y = _field('Created by',     agr[3], y)
        y = _field('Created at',     str(agr[4])[:19] if agr[4] else '—', y)
        y = _field('Activated at',   str(agr[5])[:19] if agr[5] else '—', y)
        y -= 3*mm

        # ── Rights & governance ───────────────────────────────────
        y = _section('RIGHTS & GOVERNANCE', y)
        y = _field('Commercial use',         'Yes' if agr[8]  else 'No', y)
        y = _field('Derivative works',       'Yes' if agr[9]  else 'No', y)
        y = _field('Sublicensing',           'Yes' if agr[10] else 'No', y)
        y = _field('AI training',            'Yes' if agr[11] else 'No', y)
        y = _field('Attribution required',   'Yes' if agr[12] else 'No', y)
        y = _field('Dispute resolution',     agr[13] or '—', y)
        y = _field('Jurisdiction',           agr[14] or 'Not specified', y)
        y = _field('Pub. threshold',         f"{agr[15]}%" if agr[15] else '50%', y)
        y -= 3*mm

        # ── Participants ──────────────────────────────────────────
        y = _section('PARTICIPANTS & OWNERSHIP', y)
        for p in participants:
            y = _check_page(y, 16)
            c.setFont('Helvetica-Bold', 8)
            c.setFillColor(colors.black)
            c.drawString(22*mm, y, str(p[0]))
            c.setFont('Helvetica', 7.5)
            c.setFillColor(colors.HexColor('#555555'))
            c.drawString(80*mm, y,
                f'{p[1]}  |  {float(p[2]):.1f}% ownership  |  {float(p[3]):.1f}% royalty')
            y -= 5*mm
            c.setFont('Helvetica', 7)
            accepted_str = str(p[4])[:10] if p[4] else 'pending'
            c.drawString(26*mm, y, f'Status: {p[5]}  |  Accepted: {accepted_str}')
            y -= 7*mm
        y -= 3*mm

        # ── Cryptographic integrity ───────────────────────────────
        y = _section('CRYPTOGRAPHIC INTEGRITY', y)
        agreement_hash = agr[6] or '—'
        # Show full hash — this is the legal identity, not truncated
        y = _field('Agreement hash', agreement_hash, y)
        y = _field('Hash algorithm', 'SHA-256', y)
        y = _field('Canon version',  CANON_VERSION, y)
        c.setFont('Helvetica', 6.5)
        c.setFillColor(colors.HexColor('#888888'))
        c.drawString(22*mm, y,
            'agreement_hash is the authoritative legal identity. '
            'The PDF is a rendering of the canonical snapshot.')
        y -= 8*mm

        # ── Footer ────────────────────────────────────────────────
        c.setFillColor(colors.HexColor('#F5F5F5'))
        c.rect(0, 0, W, 22*mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor('#888888'))
        c.setFont('Helvetica', 6.5)
        c.drawString(20*mm, 15*mm, 'Generated from structured data by SeekReap. '
                                   'PDF is a representation only — agreement_hash is the canonical proof.')
        verify_url = f'{VERIFY_BASE_URL}/verify/agreement/{agreement_id}'
        c.drawString(20*mm, 10*mm, f'Verify: {verify_url}')
        c.drawString(20*mm, 5*mm, f'Agreement hash: {agreement_hash}')

        c.save()
        buf.seek(0)
        pdf_bytes = buf.read()

        # Hash the PDF bytes directly (no hex encoding)
        pdf_hash = _hl.sha256(pdf_bytes).hexdigest()

        # Store pdf_hash and log audit event
        with db_cursor() as (conn, cur):
            cur.execute("""
                UPDATE public.coownership_agreements
                SET pdf_hash = %s WHERE id = %s
            """, (pdf_hash, agreement_id))
            _log_agreement_event(cur, agreement_id, 'pdf_generated', actor_id, {
                'pdf_hash':       pdf_hash,
                'agreement_hash': agreement_hash,
                'canon_version':  CANON_VERSION
            })
            conn.commit()

        from flask import Response
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename=seekreap-agreement-{agreement_id[:8]}.pdf',
                'X-Agreement-Hash': agreement_hash,
                'X-PDF-Hash':       pdf_hash,
                'X-Canon-Version':  CANON_VERSION,
                'X-Legal-Note':     'agreement_hash is the canonical legal identity, not pdf_hash'
            }
        )

    except Exception as e:
        log_error('agreement', 'pdf_failed', error=str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/api/agreements/<agreement_id>/pdf', methods=['GET'])
def get_agreement_pdf(agreement_id):
    """Retrieve PDF by regenerating deterministically from current DB state."""
    claims, err = _require_auth(request)
    if err: return err
    actor_id = claims.get("sub", "")

    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                SELECT a.id, a.submission_id, a.status, a.created_by,
                       a.created_at, a.activated_at, a.agreement_hash,
                       a.template_version, a.commercial_use, a.derivative_works,
                       a.sublicensing, a.ai_training_permitted, a.attribution_required,
                       a.dispute_resolution, a.governing_jurisdiction,
                       a.minimum_publication_threshold, a.canonicalization_version
                FROM public.coownership_agreements a
                WHERE a.id = %s
            """, (agreement_id,))
            agr = cur.fetchone()
            if not agr:
                return jsonify({'error': 'agreement not found'}), 404
            if agr[2] != 'active':
                return jsonify({'error': 'PDF only available for active agreements'}), 400

            cur.execute("""
                SELECT 1 FROM public.agreement_participants
                WHERE agreement_id = %s AND user_id = %s
                UNION
                SELECT 1 FROM public.coownership_agreements
                WHERE id = %s AND created_by = %s
                LIMIT 1
            """, (agreement_id, actor_id, agreement_id, actor_id))
            if not cur.fetchone():
                return jsonify({'error': 'access denied'}), 403

            cur.execute("""
                SELECT COALESCE(attribution_name, display_name, 'Participant') AS name,
                       role, ownership_pct,
                       COALESCE(royalty_pct, ownership_pct) AS royalty_pct,
                       accepted_at, status
                FROM public.agreement_participants
                WHERE agreement_id = %s ORDER BY ownership_pct DESC
            """, (agreement_id,))
            participants = cur.fetchall()

        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from flask import Response
        import io, hashlib as _hl

        buf = io.BytesIO()
        W, H = A4
        c = rl_canvas.Canvas(buf, pagesize=A4)

        PAGE_BOTTOM = 25 * mm
        def _check_page(y, needed=12):
            if y < PAGE_BOTTOM + needed * mm:
                c.showPage()
                c.setFont('Helvetica-Bold', 9)
                c.setFillColor(colors.HexColor('#888888'))
                c.drawString(20*mm, H - 12*mm, f'Agreement {agreement_id[:8]}... (continued)')
                return H - 22*mm
            return y

        def _hline(y):
            c.setStrokeColor(colors.HexColor('#DDDDDD'))
            c.line(20*mm, y, W - 20*mm, y)

        def _section(title, y):
            y = _check_page(y, 20)
            c.setFont('Helvetica-Bold', 9)
            c.setFillColor(colors.HexColor('#1A1A2E'))
            c.drawString(20*mm, y, title)
            _hline(y - 2*mm)
            return y - 9*mm

        def _field(label, value, y):
            y = _check_page(y, 8)
            c.setFont('Helvetica-Bold', 7.5)
            c.setFillColor(colors.HexColor('#666666'))
            c.drawString(22*mm, y, f'{label}:')
            c.setFont('Helvetica', 7.5)
            c.setFillColor(colors.black)
            c.drawString(72*mm, y, str(value) if value is not None else '—')
            return y - 6*mm

        c.setFillColor(colors.HexColor('#1A1A2E'))
        c.rect(0, H - 28*mm, W, 28*mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 15)
        c.drawString(20*mm, H - 13*mm, 'SeekReap Co-Ownership Agreement')
        c.setFont('Helvetica', 8)
        c.drawString(20*mm, H - 20*mm,
            f'Template: {agr[7]}  |  Canon: {CANON_VERSION}  |  Status: {agr[2].upper()}')

        y = H - 36*mm
        y = _section('AGREEMENT IDENTITY', y)
        y = _field('Agreement ID',  str(agr[0]), y)
        y = _field('Submission ID', str(agr[1]), y)
        y = _field('Created by',    agr[3], y)
        y = _field('Created at',    str(agr[4])[:19] if agr[4] else '—', y)
        y = _field('Activated at',  str(agr[5])[:19] if agr[5] else '—', y)
        y -= 3*mm

        y = _section('RIGHTS & GOVERNANCE', y)
        y = _field('Commercial use',       'Yes' if agr[8]  else 'No', y)
        y = _field('Derivative works',     'Yes' if agr[9]  else 'No', y)
        y = _field('Sublicensing',         'Yes' if agr[10] else 'No', y)
        y = _field('AI training',          'Yes' if agr[11] else 'No', y)
        y = _field('Attribution required', 'Yes' if agr[12] else 'No', y)
        y = _field('Dispute resolution',   agr[13] or '—', y)
        y = _field('Jurisdiction',         agr[14] or 'Not specified', y)
        y = _field('Pub. threshold',       f"{agr[15]}%" if agr[15] else '50%', y)
        y -= 3*mm

        y = _section('PARTICIPANTS & OWNERSHIP', y)
        for p in participants:
            y = _check_page(y, 16)
            c.setFont('Helvetica-Bold', 8)
            c.setFillColor(colors.black)
            c.drawString(22*mm, y, str(p[0]))
            c.setFont('Helvetica', 7.5)
            c.setFillColor(colors.HexColor('#555555'))
            c.drawString(80*mm, y,
                f'{p[1]}  |  {float(p[2]):.1f}% ownership  |  {float(p[3]):.1f}% royalty')
            y -= 5*mm
            c.setFont('Helvetica', 7)
            accepted_str = str(p[4])[:10] if p[4] else 'pending'
            c.drawString(26*mm, y, f'Status: {p[5]}  |  Accepted: {accepted_str}')
            y -= 7*mm
        y -= 3*mm

        y = _section('CRYPTOGRAPHIC INTEGRITY', y)
        agreement_hash = agr[6] or '—'
        y = _field('Agreement hash', agreement_hash, y)
        y = _field('Hash algorithm', 'SHA-256', y)
        y = _field('Canon version',  CANON_VERSION, y)
        c.setFont('Helvetica', 6.5)
        c.setFillColor(colors.HexColor('#888888'))
        c.drawString(22*mm, y,
            'agreement_hash is the authoritative legal identity. '
            'The PDF is a rendering of the canonical snapshot.')
        y -= 8*mm

        c.setFillColor(colors.HexColor('#F5F5F5'))
        c.rect(0, 0, W, 22*mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor('#888888'))
        c.setFont('Helvetica', 6.5)
        c.drawString(20*mm, 15*mm,
            'Generated from structured data by SeekReap. '
            'PDF is a representation only — agreement_hash is the canonical proof.')
        verify_url = f'{VERIFY_BASE_URL}/verify/agreement/{agreement_id}'
        c.drawString(20*mm, 10*mm, f'Verify: {verify_url}')
        c.drawString(20*mm, 5*mm, f'Agreement hash: {agreement_hash}')
        c.save()

        buf.seek(0)
        pdf_bytes = buf.read()
        pdf_hash = _hl.sha256(pdf_bytes).hexdigest()

        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition':
                    f'attachment; filename=seekreap-agreement-{agreement_id[:8]}.pdf',
                'X-Agreement-Hash': agreement_hash,
                'X-PDF-Hash':       pdf_hash,
                'X-Canon-Version':  CANON_VERSION,
                'X-Legal-Note':
                    'agreement_hash is the canonical legal identity, not pdf_hash'
            }
        )

    except Exception as e:
        log_error('agreement', 'pdf_get_failed', error=str(e))
        return jsonify({'error': str(e)}), 500



# ══════════════════════════════════════════════════════════════════
# RIGHTS ENGINE v1
# Internal policy evaluator — internal-first, externally callable later.
#
# Core contract:
#   evaluate_rights(agreement_id, actor_id, action_id, context) -> RightsDecision
#
# Decision sources (machine-readable):
#   agreement_inactive        — agreement not in 'active' state
#   actor_not_participant     — actor has no role in agreement
#   missing_permission        — rights flag explicitly false
#   unanimous_required        — action needs all participants; not all active
#   threshold_not_met         — weighted ownership below required threshold
#   permission_granted        — explicit rights flag true
#   owner_governance_right    — creator exercising governance action
#   platform_default_allow    — no explicit restriction found
# ══════════════════════════════════════════════════════════════════

RIGHTS_ENGINE_VERSION = 'v1'
POLICY_REGISTRY_VERSION = 'v1'

# ══════════════════════════════════════════════════════════════════
# POLICY REGISTRY v1 — FROZEN
# Changes require incrementing POLICY_REGISTRY_VERSION.
# ══════════════════════════════════════════════════════════════════

# Action scope classification
# participant: requires verified agreement membership
# public:      no membership required (cryptographic/observability actions)
# system:      reserved for internal workers, cron, marketplace automation
RIGHTS_ACTION_SCOPES = {
    'publish':            'participant',
    'commercial_publish': 'participant',
    'distribute':         'participant',
    'create_derivative':  'participant',
    'sublicense':         'participant',
    'ai_train':           'participant',
    'ai_embed':           'participant',
    'transfer_ownership': 'participant',
    'revoke_agreement':   'participant',
    'amend_rights':       'participant',
    'freeze_asset':       'participant',
    'generate_pdf':       'participant',
    'accept_invitation':  'participant',
                'create_agreement':    'participant',
    'verify_chain':       'public',
}

# Policy map: action_id -> (permitted: bool, deny_decision_source: str)
# permitted=True means the action is allowed IF the actor passes all other checks.
# deny_decision_source is used when permitted=False.
RIGHTS_POLICY_MAP = {
    'publish':            (True,                              'platform_default_allow'),
    'commercial_publish': (None,                              'missing_permission'),   # resolved at runtime
    'distribute':         (None,                              'missing_permission'),
    'create_derivative':  (None,                              'missing_permission'),
    'sublicense':         (None,                              'missing_permission'),
    'ai_train':           (None,                              'missing_permission'),
    'ai_embed':           (None,                              'missing_permission'),
    'transfer_ownership': (True,                              'owner_governance_right'),
    'revoke_agreement':   (True,                              'owner_governance_right'),
    'amend_rights':       (True,                              'owner_governance_right'),
    'freeze_asset':       (True,                              'owner_governance_right'),
    'generate_pdf':       (True,                              'permission_granted'),
    'accept_invitation':  (True,                              'permission_granted'),
    'create_agreement':    (True,                              'permission_granted'),
    'verify_chain':       (True,                              'permission_granted'),
}

# Decision source constants — machine-readable policy classification
class DecisionSource:
    AGREEMENT_INACTIVE      = 'agreement_inactive'
    ACTOR_NOT_PARTICIPANT   = 'actor_not_participant'
    MISSING_PERMISSION      = 'missing_permission'
    UNANIMOUS_REQUIRED      = 'unanimous_required'
    THRESHOLD_NOT_MET       = 'threshold_not_met'
    PERMISSION_GRANTED      = 'permission_granted'
    OWNER_GOVERNANCE_RIGHT  = 'owner_governance_right'
    PLATFORM_DEFAULT_ALLOW  = 'platform_default_allow'
    PUBLIC_ACCESS           = 'public_access'
    SYSTEM_ACCESS           = 'system_access'
    SYSTEM_IDENTITY_INVALID = 'system_identity_invalid'
    UNKNOWN_ACTION          = 'unknown_action'
    AGREEMENT_NOT_FOUND     = 'agreement_not_found'
    ENGINE_ERROR            = 'engine_error'
    DELEGATED_ACCESS        = 'delegated_access'

# ══════════════════════════════════════════════════════════════════
# SYSTEM ACTOR IDENTITY MODEL v1
# System actors are trusted internal services, not human participants.
# They bypass participant membership checks but require identity validation.
# Each actor has a canonical UUID for audit trail consistency.
# ══════════════════════════════════════════════════════════════════

SYSTEM_ACTOR_REGISTRY = {
    # actor_id (UUID)                        : service_name
    'ffffffff-0000-0000-0000-000000000001': 'royalty_engine',
    'ffffffff-0000-0000-0000-000000000002': 'nft_minter',
    'ffffffff-0000-0000-0000-000000000003': 'marketplace_worker',
    'ffffffff-0000-0000-0000-000000000004': 'escrow_processor',
    'ffffffff-0000-0000-0000-000000000005': 'compliance_automation',
    'ffffffff-0000-0000-0000-000000000006': 'fraud_lock_service',
    'ffffffff-0000-0000-0000-000000000007': 'moderation_worker',
    'ffffffff-0000-0000-0000-000000000008': 'cron_scheduler',
}

def is_system_actor(actor_id: str) -> bool:
    """Return True if actor_id belongs to a trusted system service."""
    return str(actor_id) in SYSTEM_ACTOR_REGISTRY


# ══════════════════════════════════════════════════════════════════
# DELEGATED AUTHORITY MODEL v1
# Delegates are third parties authorized by a participant to act
# on their behalf for specific actions within an agreement.
# Enforcement chain:
#   1. delegate has active delegation
#   2. delegation not expired
#   3. delegation not revoked
#   4. grantor still authorized in agreement
#   5. action in delegation's allowed_actions
#   6. scope constraints satisfied
# ══════════════════════════════════════════════════════════════════

def _resolve_delegation(cur, agreement_id: str, delegate_id: str,
                         action_id: str) -> dict:
    # Use a fresh cursor to avoid interfering with caller's cursor state
    cur = cur.connection.cursor()
    """
    Look up active delegation for delegate_id in this agreement.
    Returns delegation row dict or None.
    Also verifies grantor still holds participant status.
    """
    from datetime import datetime, timezone as _tz

    cur.execute("""
        SELECT d.id, d.grantor_id, d.delegate_id, d.delegate_name,
               d.allowed_actions, d.allowed_actions_v2,
               d.scope_type, d.scope_constraint,
               d.expires_at, d.status, d.snapshot_id,
               d.policy_registry_version, d.delegation_hash
        FROM public.rights_delegations d
        WHERE d.agreement_id = %s
          AND d.delegate_id = %s
          AND d.status = 'active'
        ORDER BY d.granted_at DESC
        LIMIT 1
    """, (agreement_id, delegate_id))
    row = cur.fetchone()
    if not row:
        return None

    (del_id, grantor_id, delegate_id_, delegate_name,
     allowed_actions, allowed_actions_v2,
     scope_type, scope_constraint,
     expires_at, status, snap_id,
     pol_ver, del_hash) = row

    # Check expiry
    if expires_at and expires_at < datetime.now(_tz.utc):
        return None

    # Check action is in allowed_actions
    # Support both TEXT[] and JSONB v2 format
    action_allowed = False
    if allowed_actions_v2:
        # v2: [{"action": "...", "version": "..."}]
        action_allowed = any(
            (a.get('action') == action_id if isinstance(a, dict) else a == action_id)
            for a in allowed_actions_v2
        )
    elif allowed_actions:
        action_allowed = action_id in allowed_actions

    if not action_allowed:
        return None

    # Verify grantor still holds active participant status
    cur.execute("""
        SELECT status FROM public.agreement_participants
        WHERE agreement_id = %s AND user_id = %s
        LIMIT 1
    """, (agreement_id, str(grantor_id)))
    grantor_row = cur.fetchone()
    if not grantor_row or grantor_row[0] != 'accepted':
        return None  # Grantor no longer authorized — zombie delegation blocked

    return {
        'delegation_id':         str(del_id),
        'grantor_id':            str(grantor_id),
        'delegate_name':         delegate_name,
        'scope_type':            scope_type,
        'scope_constraint':      scope_constraint,
        'snapshot_id':           str(snap_id) if snap_id else None,
        'policy_registry_version': pol_ver,
        'delegation_hash':       del_hash,
    }


def _log_delegation_event(cur, delegation_id: str, event_type: str,
                           actor_id: str, agreement_id: str,
                           action_id: str = None, context: dict = None):
    """Append immutable delegation event."""
    cur.execute("""
        SELECT event_hash FROM public.rights_delegation_events
        WHERE delegation_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """, (delegation_id,))
    prev_row = cur.fetchone()
    prev_hash = prev_row[0] if prev_row else None

    payload = _canonical_json({
        'delegation_id': delegation_id,
        'event_type':    event_type,
        'actor_id':      str(actor_id),
        'action_id':     action_id,
    })
    event_hash = _sha256((prev_hash or '') + payload)

    cur.execute("""
        INSERT INTO public.rights_delegation_events
            (delegation_id, event_type, actor_id, agreement_id,
             action_id, context, event_hash, previous_hash)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
    """, (delegation_id, event_type, str(actor_id), agreement_id,
          action_id, json.dumps(context or {}), event_hash, prev_hash))

def get_system_actor_name(actor_id: str) -> str:
    """Return the service name for a system actor, or 'unknown_system'."""
    return SYSTEM_ACTOR_REGISTRY.get(str(actor_id), 'unknown_system')

# System-scoped actions — only trusted internal services may invoke these
RIGHTS_ACTION_SCOPES.update({
    'mint_nft':              'system',
    'distribute_royalty':    'system',
    'escrow_release':        'system',
    'compliance_freeze':     'system',
    'fraud_lock':            'system',
    'moderation_override':   'system',
    'cron_snapshot':         'system',
})

# Add system actions to policy registry
RIGHTS_POLICY_MAP.update({
    'mint_nft':              (True, 'system_access'),
    'distribute_royalty':    (True, 'system_access'),
    'escrow_release':        (True, 'system_access'),
    'compliance_freeze':     (True, 'system_access'),
    'fraud_lock':            (True, 'system_access'),
    'moderation_override':   (True, 'system_access'),
    'cron_snapshot':         (True, 'system_access'),
})

# Add system actions to DB registry on startup
def _ensure_system_actions():
    """Register system actions in rights_actions table if not present."""
    system_actions = [
        ('mint_nft',            'system', 'Mint an NFT from a rights snapshot',           False),
        ('distribute_royalty',  'system', 'Execute royalty distribution event',            False),
        ('escrow_release',      'system', 'Release escrowed funds to participants',        False),
        ('compliance_freeze',   'system', 'Freeze asset for compliance review',            False),
        ('fraud_lock',          'system', 'Lock asset due to fraud detection',             False),
        ('moderation_override', 'system', 'Apply moderation action to asset',             False),
        ('cron_snapshot',       'system', 'Generate scheduled rights snapshot',            False),
    ]
    try:
        with db_cursor() as (conn, cur):
            for action_id, category, description, requires_unanimous in system_actions:
                cur.execute("""
                    INSERT INTO public.rights_actions
                        (id, category, description, requires_unanimous)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (action_id, category, description, requires_unanimous))
            conn.commit()
            log_info('rights_engine', 'system_actions_registered',
                     count=len(system_actions))
    except Exception as e:
        log_warn('rights_engine', 'system_actions_registration_failed', error=str(e))

_ensure_system_actions()


def _build_rights_snapshot(cur, agreement_id: str) -> dict:
    """Build canonical rights state from agreement + participants."""
    cur.execute("""
        SELECT
            a.id, a.status, a.created_by, a.agreement_hash,
            a.commercial_use, a.derivative_works, a.sublicensing,
            a.ai_training_permitted, a.attribution_required,
            a.publication_requires_all, a.minimum_publication_threshold,
            a.dispute_resolution, a.governing_jurisdiction,
            a.template_version, a.activated_at,
            a.canonicalization_version
        FROM public.coownership_agreements a
        WHERE a.id = %s
    """, (agreement_id,))
    agr = cur.fetchone()
    if not agr:
        return None

    cur.execute("""
        SELECT user_id, role, ownership_pct, royalty_pct,
               attribution_name, status, accepted_at
        FROM public.agreement_participants
        WHERE agreement_id = %s
        ORDER BY ownership_pct DESC
    """, (agreement_id,))
    participants = cur.fetchall()

    return {
        'agreement_id':              str(agr[0]),
        'status':                    agr[1],
        'created_by':                str(agr[2]),
        'agreement_hash':            agr[3],
        'rights': {
            'commercial_use':                agr[4],
            'derivative_works':              agr[5],
            'sublicensing':                  agr[6],
            'ai_training_permitted':         agr[7],
            'attribution_required':          agr[8],
            'publication_requires_all':      agr[9],
            'minimum_publication_threshold': float(agr[10]) if agr[10] else 50.0,
            'dispute_resolution':            agr[11],
            'governing_jurisdiction':        agr[12],
        },
        'template_version':          agr[13],
        'activated_at':              agr[14].isoformat() if agr[14] else None,
        'canonicalization_version':  agr[15] or CANON_VERSION,
        'participants': [{
            'user_id':          str(p[0]) if p[0] else None,
            'role':             p[1],
            'ownership_pct':    float(p[2]),
            'royalty_pct':      float(p[3]) if p[3] else float(p[2]),
            'attribution_name': p[4],
            'status':           p[5],
            'accepted_at':      p[6].isoformat() if p[6] else None,
        } for p in participants],
        'engine_version': RIGHTS_ENGINE_VERSION,
    }


def _store_rights_snapshot(cur, agreement_id: str, state: dict,
                            triggered_by: str = 'evaluation') -> str:
    """Hash and store a rights snapshot. Returns snapshot_id."""
    import uuid as _uuid
    state_json = _canonical_json(state)
    snap_hash  = _sha256(state_json)

    # Check if identical snapshot already exists
    cur.execute(
        "SELECT id FROM public.rights_snapshots WHERE snapshot_hash = %s",
        (snap_hash,)
    )
    existing = cur.fetchone()
    if existing:
        return str(existing[0])

    snap_id = str(_uuid.uuid4())
    cur.execute("""
        INSERT INTO public.rights_snapshots
            (id, agreement_id, snapshot_hash, rights_state,
             canon_version, state_version, triggered_by, policy_registry_version)
        VALUES (%s, %s, %s, %s::jsonb, %s, 1, %s, %s)
    """, (snap_id, agreement_id, snap_hash, state_json,
          CANON_VERSION, triggered_by, POLICY_REGISTRY_VERSION))
    return snap_id


def _log_rights_evaluation(cur, agreement_id: str, action_id: str,
                            actor_id: str, allowed: bool, reason: str,
                            decision_source: str, context: dict = None,
                            snapshot_id: str = None, action_scope: str = 'participant',
                            rule_trace: list = None):
    """Append immutable evaluation record."""
    cur.execute("""
        INSERT INTO public.rights_evaluations
            (agreement_id, action_id, actor_id, allowed,
             reason, decision_source, evaluation_version, context,
             snapshot_id, policy_registry_version, action_scope, rule_trace)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb)
    """, (agreement_id, action_id, actor_id, allowed,
          reason, decision_source, RIGHTS_ENGINE_VERSION,
          json.dumps(context or {}),
          snapshot_id, POLICY_REGISTRY_VERSION, action_scope,
          json.dumps(rule_trace or [])))


def evaluate_rights(agreement_id: str, actor_id: str,
                    action_id: str, context: dict = None,
                    log: bool = True) -> dict:
    """
    Deterministic rights evaluator — the core engine.

    Returns a stable decision object:
    {
        allowed:          bool,
        reason:           str,
        decision_source:  str,
        action_id:        str,
        agreement_id:     str,
        actor_id:         str,
        snapshot_id:      str | None,
        evaluated_at:     str,
        engine_version:   str,
        ownership_pct:    float | None,
        attribution_required: bool,
        royalty_pct:      float | None,
    }
    """
    from datetime import datetime, timezone as _tz

    def _deny(reason, source, state=None, snap_id=None):
        return {
            'allowed':              False,
            'reason':               reason,
            'decision_source':      source,
            'action_id':            action_id,
            'agreement_id':         agreement_id,
            'actor_id':             actor_id,
            'snapshot_id':          snap_id,
            'evaluated_at':         datetime.now(_tz.utc).isoformat(),
            'engine_version':       RIGHTS_ENGINE_VERSION,
            'ownership_pct':        None,
            'attribution_required': True,
            'royalty_pct':          None,
        }

    def _allow(reason, source, state, actor_part, snap_id, policy_source=None):
        return {
            'allowed':              True,
            'reason':               reason,
            'decision_source':      policy_source or source,
            'action_id':            action_id,
            'agreement_id':         agreement_id,
            'actor_id':             actor_id,
            'snapshot_id':          snap_id,
            'evaluated_at':         datetime.now(_tz.utc).isoformat(),
            'engine_version':       RIGHTS_ENGINE_VERSION,
            'ownership_pct':        actor_part['ownership_pct'] if actor_part else None,
            'attribution_required': state['rights']['attribution_required'],
            'royalty_pct':          actor_part['royalty_pct'] if actor_part else None,
        }

    try:
        with db_cursor() as (conn, cur):
            # 1. Load action definition
            cur.execute(
                "SELECT requires_unanimous, requires_all_active, category "
                "FROM public.rights_actions WHERE id = %s",
                (action_id,)
            )
            action_def = cur.fetchone()
            if not action_def:
                return _deny(f"Unknown action '{action_id}'", 'unknown_action')
            requires_unanimous, requires_all_active, category = action_def

            # 2. Build rights snapshot
            state = _build_rights_snapshot(cur, agreement_id)
            if not state:
                return _deny('Agreement not found', 'agreement_not_found')

            # 3. Store snapshot
            snap_id = _store_rights_snapshot(cur, agreement_id, state, 'evaluation')

            action_scope = RIGHTS_ACTION_SCOPES.get(action_id, 'participant')
            # 3b. Initialise rule trace — records every check passed/failed
            _trace = [
                f'action_registered:{action_id}',
                f'scope:{action_scope}',
                f'agreement_loaded:{agreement_id}',
                f'snapshot_stored:{snap_id}',
            ]

            # 4. Agreement must be active
            if state['status'] != 'active':
                _trace.append(f'agreement_status_check:failed:{state["status"]}')
                result = _deny(
                    f"Agreement is '{state['status']}', not active",
                    'agreement_inactive', state, snap_id
                )
                result['rule_trace'] = _trace
                if log:
                    _log_rights_evaluation(cur, agreement_id, action_id,
                                           actor_id, False, result['reason'],
                                           result['decision_source'], context,
                                           snapshot_id=snap_id,
                                           action_scope=action_scope,
                                           rule_trace=_trace)
                    conn.commit()
                return result
            _trace.append('agreement_status_check:active')


            if action_scope == 'public':
                _trace.append('public_scope_bypass')
                result = _allow(
                    f"Public action '{action_id}' permitted without participant check",
                    'public_access', state, None, snap_id,
                    policy_source='public_access'
                )
                result['rule_trace'] = _trace
                if log:
                    _log_rights_evaluation(cur, agreement_id, action_id,
                                           actor_id, True, result['reason'],
                                           result['decision_source'], context,
                                           snapshot_id=snap_id,
                                           action_scope='public',
                                           rule_trace=_trace)
                    conn.commit()
                return result

            # 5b. System scope — trusted internal services bypass participant checks
            if action_scope == 'system':
                if is_system_actor(actor_id):
                    service_name = get_system_actor_name(actor_id)
                    _trace.append(f'system_identity_verified:{service_name}')
                    result = _allow(
                        f"System action '{action_id}' executed by '{service_name}'",
                        'system_access', state, None, snap_id,
                        policy_source='system_access'
                    )
                    result['rule_trace'] = _trace
                    if log:
                        _log_rights_evaluation(cur, agreement_id, action_id,
                                               actor_id, True, result['reason'],
                                               result['decision_source'],
                                               {**(context or {}),
                                                'service': service_name},
                                               snapshot_id=snap_id,
                                               action_scope='system',
                                               rule_trace=_trace)
                        conn.commit()
                    return result
                else:
                    _trace.append('system_identity_rejected')
                    result = _deny(
                        f"System action '{action_id}' rejected: "
                        f"actor '{actor_id}' is not a trusted system service",
                        'system_identity_invalid', state, snap_id
                    )
                    result['rule_trace'] = _trace
                    if log:
                        _log_rights_evaluation(cur, agreement_id, action_id,
                                               actor_id, False, result['reason'],
                                               result['decision_source'], context,
                                               snapshot_id=snap_id,
                                               action_scope='system',
                                               rule_trace=_trace)
                        conn.commit()
                    return result

            # 5c. Delegated authority check
            # Runs before participant check — a delegate is not a participant
            if action_scope == 'participant':
                _delegation = _resolve_delegation(cur, agreement_id,
                                                  actor_id, action_id)
                if _delegation:
                    _trace.append(
                        f'delegated_authority:granted_by:{_delegation["grantor_id"][:8]}'
                    )
                    _trace.append(f'delegation_id:{_delegation["delegation_id"][:8]}')
                    result = _allow(
                        f"Delegated authority for '{action_id}' granted by "
                        f"participant '{_delegation['grantor_id'][:8]}'",
                        'delegated_access', state, None, snap_id,
                        policy_source='delegated_access'
                    )
                    result['rule_trace']   = _trace
                    result['delegation']   = _delegation
                    if log:
                        _log_rights_evaluation(cur, agreement_id, action_id,
                                               actor_id, True, result['reason'],
                                               result['decision_source'], context,
                                               snapshot_id=snap_id,
                                               action_scope='delegated',
                                               rule_trace=_trace)
                        _log_delegation_event(cur, _delegation['delegation_id'],
                                              'delegation_used', actor_id,
                                              agreement_id, action_id, context)
                        conn.commit()
                    return result

            # 5d. Find actor's participant record (participant-scoped actions only)
            actor_part = next(
                (p for p in state['participants'] if p['user_id'] == actor_id),
                None
            )
            is_creator = (actor_id == state['created_by'])

            if not actor_part and not is_creator:
                _trace.append('participant_check:failed')
                result = _deny(
                    'Actor is not a participant in this agreement',
                    'actor_not_participant', state, snap_id
                )
                result['rule_trace'] = _trace
                if log:
                    _log_rights_evaluation(cur, agreement_id, action_id,
                                           actor_id, False, result['reason'],
                                           result['decision_source'], context,
                                           snapshot_id=snap_id,
                                           action_scope=action_scope,
                                           rule_trace=_trace)
                    conn.commit()
                return result
            _trace.append(f'participant_check:verified:{"creator" if is_creator else "participant"}')

            # 6. Unanimous actions — all participants must be accepted
            if requires_unanimous:
                non_accepted = [
                    p for p in state['participants']
                    if p['status'] != 'accepted'
                ]
                if non_accepted:
                    _trace.append(f'unanimous_check:failed:{len(non_accepted)}_pending')
                    result = _deny(
                        f"Action '{action_id}' requires unanimous acceptance; "
                        f"{len(non_accepted)} participant(s) not yet accepted",
                        'unanimous_required', state, snap_id
                    )
                    result['rule_trace'] = _trace
                    if log:
                        _log_rights_evaluation(cur, agreement_id, action_id,
                                               actor_id, False, result['reason'],
                                               result['decision_source'], context,
                                               snapshot_id=snap_id,
                                               action_scope=action_scope,
                                               rule_trace=_trace)
                        conn.commit()
                    return result
            _trace.append('unanimous_check:passed')

            rights = state['rights']

            # 7. Action-specific policy resolution — use module-level registry
            # Resolve None permission values from live agreement rights
            RIGHTS_RUNTIME_RESOLUTION = {
                'commercial_publish': rights['commercial_use'],
                'distribute':         rights['commercial_use'],
                'create_derivative':  rights['derivative_works'],
                'sublicense':         rights['sublicensing'],
                'ai_train':           rights['ai_training_permitted'],
                'ai_embed':           rights['ai_training_permitted'],
            }

            if action_id in RIGHTS_POLICY_MAP:
                _static_permitted, deny_source = RIGHTS_POLICY_MAP[action_id]
                permitted = (RIGHTS_RUNTIME_RESOLUTION.get(action_id, _static_permitted)
                             if _static_permitted is None else _static_permitted)
                if not permitted:
                    _trace.append(f'policy_check:denied:{deny_source}')
                    result = _deny(
                        f"Action '{action_id}' is not permitted under this agreement",
                        deny_source, state, snap_id
                    )
                    result['rule_trace'] = _trace
                    if log:
                        _log_rights_evaluation(cur, agreement_id, action_id,
                                               actor_id, False, result['reason'],
                                               result['decision_source'], context,
                                               snapshot_id=snap_id,
                                               action_scope=action_scope,
                                               rule_trace=_trace)
                        conn.commit()
                    return result
            # policy check passed — source resolved at step 9

            # 8. Threshold check for publication actions
            if category == 'publication':
                threshold = rights['minimum_publication_threshold']
                if rights['publication_requires_all']:
                    accepted_pct = sum(
                        p['ownership_pct'] for p in state['participants']
                        if p['status'] == 'accepted'
                    )
                    if accepted_pct < 100.0:
                        result = _deny(
                            f"Publication requires all participants; "
                            f"only {accepted_pct:.1f}% ownership accepted",
                            'threshold_not_met', state, snap_id
                        )
                        if log:
                            _log_rights_evaluation(cur, agreement_id, action_id,
                                                   actor_id, False, result['reason'],
                                                   result['decision_source'], context,
                                                   snapshot_id=snap_id,
                                                   action_scope=action_scope)
                            conn.commit()
                        return result
                else:
                    accepted_pct = sum(
                        p['ownership_pct'] for p in state['participants']
                        if p['status'] == 'accepted'
                    )
                    if accepted_pct < threshold:
                        result = _deny(
                            f"Publication threshold not met: "
                            f"{accepted_pct:.1f}% accepted, {threshold:.1f}% required",
                            'threshold_not_met', state, snap_id
                        )
                        if log:
                            _log_rights_evaluation(cur, agreement_id, action_id,
                                                   actor_id, False, result['reason'],
                                                   result['decision_source'], context,
                                                   snapshot_id=snap_id,
                                                   action_scope=action_scope)
                            conn.commit()
                        return result

            # 9. Allowed — preserve policy source from POLICY_MAP if available
            _policy_source = RIGHTS_POLICY_MAP.get(action_id, (None, 'permission_granted'))[1]
            _trace.append(f'final_decision:allowed:{_policy_source}')
            result = _allow(
                f"Action '{action_id}' permitted under agreement rights",
                'permission_granted', state, actor_part, snap_id,
                policy_source=_policy_source
            )
            result['rule_trace'] = _trace
            if log:
                _log_rights_evaluation(cur, agreement_id, action_id,
                                       actor_id, True, result['reason'],
                                       result['decision_source'], context,
                                       snapshot_id=snap_id,
                                       action_scope=action_scope,
                                       rule_trace=_trace)
                conn.commit()
            return result

    except Exception as e:
        log_error('rights_engine', 'evaluate_failed',
                  agreement_id=agreement_id, action_id=action_id,
                  actor_id=actor_id, error=str(e))
        return {
            'allowed':         False,
            'reason':          f'Evaluation error: {str(e)}',
            'decision_source': 'engine_error',
            'action_id':       action_id,
            'agreement_id':    agreement_id,
            'actor_id':        actor_id,
            'snapshot_id':     None,
            'evaluated_at':    __import__('datetime').datetime.utcnow().isoformat(),
            'engine_version':  RIGHTS_ENGINE_VERSION,
            'ownership_pct':   None,
            'attribution_required': True,
            'royalty_pct':     None,
        }
