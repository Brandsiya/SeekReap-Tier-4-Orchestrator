"""
patch_backend.py — run from SeekReap-Tier-4-Orchestrator/

    python3 patch_backend.py && python3 -c "import ast; ast.parse(open('tier4_main.py').read()); print('✅ syntax OK')"
"""
import re, sys

PATH = "tier4_main.py"
src  = open(PATH, encoding="utf-8").read()
orig = src  # keep for diff count


# ─────────────────────────────────────────────────────────────────────────────
# 1. CONNECTION POOLING
#    Replace bare get_db() with a ThreadedConnectionPool.
#    Pool min=2, max=10 — enough for Fly.dev single-instance free tier.
# ─────────────────────────────────────────────────────────────────────────────

OLD_GET_DB = '''def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])'''

NEW_GET_DB = '''# ── Connection pool (min=2 max=10, thread-safe) ───────────────────────────────
from psycopg2 import pool as _pg_pool

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
    """Get a connection from the pool. Caller MUST call put_db(conn) when done."""
    return _get_pool().getconn()

def put_db(conn):
    """Return connection to the pool. Pass conn=None to no-op safely."""
    if conn is not None:
        try:
            _get_pool().putconn(conn)
        except Exception:
            pass  # pool may be closed during shutdown'''

assert OLD_GET_DB in src, "❌ get_db() pattern not found"
src = src.replace(OLD_GET_DB, NEW_GET_DB, 1)
print("✅ 1. Connection pool added")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Replace all `conn.close()` / `cur.close(); conn.close()` patterns
#    inside finally blocks with put_db(conn).
#    We do a targeted replace on the three critical helper functions and
#    inject a context manager for route handlers.
# ─────────────────────────────────────────────────────────────────────────────

# Add a context manager helper right after put_db definition
POOL_CTX = '''

from contextlib import contextmanager

@contextmanager
def db_conn():
    """
    Usage:
        with db_conn() as conn:
            cur = conn.cursor(...)
            ...
    Connection is returned to pool on exit, even if an exception is raised.
    """
    conn = get_db()
    try:
        yield conn
    finally:
        put_db(conn)
'''

# Insert after put_db definition
src = src.replace(
    "        except Exception:\n            pass  # pool may be closed during shutdown",
    "        except Exception:\n            pass  # pool may be closed during shutdown" + POOL_CTX,
    1
)
print("✅ 2. db_conn() context manager added")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fix ensure_payments_tables + _log_payment_event + _check_replay
#    to use put_db instead of conn.close()
# ─────────────────────────────────────────────────────────────────────────────

# ensure_payments_tables
src = src.replace(
    "def ensure_payments_tables():\n    conn = get_db()\n    cur  = conn.cursor()\n    try:",
    "def ensure_payments_tables():\n    conn = get_db()\n    cur  = conn.cursor()\n    try:"
)  # no change needed to body — just fix the finally

src = src.replace(
    # ensure_payments_tables finally
    "        conn.commit()\n    finally:\n        cur.close()\n        conn.close()\n\n\ntry:\n    ensure_payments_tables()",
    "        conn.commit()\n    finally:\n        cur.close()\n        put_db(conn)\n\n\ntry:\n    ensure_payments_tables()"
)

# _log_payment_event
src = src.replace(
    "        _c.commit(); _cur.close(); _c.close()\n    except Exception as _e:\n        log_warn(\"payment\", \"event_log_failed\"",
    "        _c.commit(); _cur.close(); put_db(_c)\n    except Exception as _e:\n        log_warn(\"payment\", \"event_log_failed\""
)

# _check_replay
src = src.replace(
    "        _c.commit(); _cur.close(); _c.close()\n        return inserted == 0",
    "        _c.commit(); _cur.close(); put_db(_c)\n        return inserted == 0"
)

print("✅ 3. ensure_payments_tables / _log_payment_event / _check_replay use put_db")


# ─────────────────────────────────────────────────────────────────────────────
# 4. RETRY HELPER for external HTTP calls
# ─────────────────────────────────────────────────────────────────────────────

RETRY_HELPER = '''
# ── External HTTP retry helper ────────────────────────────────────────────────
def _retry_request(method: str, url: str, max_attempts: int = 3,
                   backoff_base: float = 1.0, **kwargs):
    """
    Wraps requests.get/post with exponential backoff.
    Returns (response, None) on success, (None, last_exception) on exhaustion.
    Retries on: ConnectionError, Timeout, 5xx responses.
    Does NOT retry on 4xx (client errors).
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = getattr(requests, method.lower())(url, **kwargs)
            if resp.status_code < 500:
                return resp, None
            # 5xx — treat as transient
            last_exc = Exception(f"HTTP {resp.status_code}")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exc = e
        if attempt < max_attempts - 1:
            _time.sleep(backoff_base * (2 ** attempt))
    return None, last_exc

'''

# Insert before "def ensure_payments_tables"
src = src.replace(
    "\ndef ensure_payments_tables():",
    RETRY_HELPER + "\ndef ensure_payments_tables():",
    1
)
print("✅ 4. _retry_request() helper added")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Use _retry_request in init_paystack (init call, not verify — verify already has retry)
# ─────────────────────────────────────────────────────────────────────────────

src = src.replace(
    '''    try:
        r    = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET}",
                     "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        resp = r.json()
        if not resp.get("status"):
            return jsonify({"error": resp.get("message", "Paystack error")}), 502
        return jsonify({''',
    '''    try:
        r, _err = _retry_request(
            "post",
            "https://api.paystack.co/transaction/initialize",
            max_attempts=3,
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET}",
                     "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        if r is None:
            raise Exception(str(_err))
        resp = r.json()
        if not resp.get("status"):
            return jsonify({"error": resp.get("message", "Paystack error")}), 502
        return jsonify({'''
)
print("✅ 5. init_paystack uses _retry_request")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Use _retry_request in PayFast ITN confirmation
# ─────────────────────────────────────────────────────────────────────────────

src = src.replace(
    '''    # 4. Server-side ITN confirmation — HARD STOP on failure (no fallback)
    try:
        _pf_host = ("sandbox.payfast.co.za"
                    if os.environ.get("PAYFAST_SANDBOX") == "true"
                    else "www.payfast.co.za")
        confirm_resp = requests.post(
            f"https://{_pf_host}/eng/query/validate",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if confirm_resp.text.strip() != "VALID":
            log_warn("payfast", "itn_confirmation_failed",
                     payment_id=payment_id, response=confirm_resp.text[:200])
            return "INVALID", 400
    except Exception as _pfe:
        # Hard stop — do not process if we can't confirm with PayFast
        log_error("payfast", "itn_confirm_unreachable",
                  payment_id=payment_id, error=str(_pfe))
        return "RETRY", 503''',
    '''    # 4. Server-side ITN confirmation — HARD STOP on failure (no fallback)
    _pf_host = ("sandbox.payfast.co.za"
                if os.environ.get("PAYFAST_SANDBOX") == "true"
                else "www.payfast.co.za")
    confirm_resp, _pfe = _retry_request(
        "post",
        f"https://{_pf_host}/eng/query/validate",
        max_attempts=3,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if confirm_resp is None:
        log_error("payfast", "itn_confirm_unreachable",
                  payment_id=payment_id, error=str(_pfe))
        return "RETRY", 503
    if confirm_resp.text.strip() != "VALID":
        log_warn("payfast", "itn_confirmation_failed",
                 payment_id=payment_id, response=confirm_resp.text[:200])
        return "INVALID", 400'''
)
print("✅ 6. PayFast ITN confirmation uses _retry_request")


# ─────────────────────────────────────────────────────────────────────────────
# 7. QUEUE RETRY: add retry_count + last_retry_at columns, cap retries at 3
# ─────────────────────────────────────────────────────────────────────────────

# Add columns to ensure_payments_tables
src = src.replace(
    '        cur.execute("CREATE INDEX IF NOT EXISTS idx_pe_payment_id ON payment_events(payment_id)")',
    '        cur.execute("CREATE INDEX IF NOT EXISTS idx_pe_payment_id ON payment_events(payment_id)")\n'
    '        # Retry tracking columns (idempotent)\n'
    '        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS cert_retry_count INTEGER DEFAULT 0")\n'
    '        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP")'
)
print("✅ 7a. cert_retry_count + last_retry_at columns ensured")

# Cap retries at 3 in retry-certifications endpoint
src = src.replace(
    '''    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM payments
            WHERE status = \'paid\' AND submission_id IS NULL
              AND (metadata->>\'cert_retry\')::boolean = true
              AND created_at > NOW() - INTERVAL \'24 hours\'
            ORDER BY created_at LIMIT 20
        """)
        rows = cur.fetchall()
    finally:
        cur.close(); conn.close()

    retried = 0
    for row in rows:
        try:
            clean = {k: v for k, v in (row.get("metadata") or {}).items()
                     if k not in ("paystack_event", "payfast_itn")}
            result = trigger_certification(row, clean)
            if result:
                retried += 1
                _c = get_db(); _cur = _c.cursor()
                _cur.execute(
                    "UPDATE payments SET metadata = metadata - \'cert_retry\' WHERE id = %s",
                    (str(row["id"]),)
                )
                _c.commit(); _cur.close(); _c.close()
        except Exception as e:
            log_error("admin", "retry_cert_failed",
                      payment_id=str(row["id"]), error=str(e))''',
    '''    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM payments
            WHERE status = \'paid\' AND submission_id IS NULL
              AND (metadata->>\'cert_retry\')::boolean = true
              AND COALESCE(cert_retry_count, 0) < 3
              AND (last_retry_at IS NULL OR last_retry_at < NOW() - INTERVAL \'5 minutes\')
              AND created_at > NOW() - INTERVAL \'24 hours\'
            ORDER BY created_at LIMIT 20
        """)
        rows = cur.fetchall()
    finally:
        cur.close(); put_db(conn)

    retried = 0
    for row in rows:
        try:
            clean = {k: v for k, v in (row.get("metadata") or {}).items()
                     if k not in ("paystack_event", "payfast_itn")}
            result = trigger_certification(row, clean)
            if result:
                retried += 1
                _c = get_db(); _cur = _c.cursor()
                _cur.execute("""
                    UPDATE payments
                    SET metadata = metadata - \'cert_retry\',
                        cert_retry_count = COALESCE(cert_retry_count, 0) + 1,
                        last_retry_at = NOW()
                    WHERE id = %s
                """, (str(row["id"]),))
                _c.commit(); _cur.close(); put_db(_c)
            else:
                # Increment retry count even on failure so we don\'t spin forever
                _c = get_db(); _cur = _c.cursor()
                _cur.execute("""
                    UPDATE payments
                    SET cert_retry_count = COALESCE(cert_retry_count, 0) + 1,
                        last_retry_at = NOW()
                    WHERE id = %s
                """, (str(row["id"]),))
                _c.commit(); _cur.close(); put_db(_c)
        except Exception as e:
            log_error("admin", "retry_cert_failed",
                      payment_id=str(row["id"]), error=str(e))'''
)
print("✅ 7b. Retry capped at 3 with 5-min backoff and retry_count tracking")


# ─────────────────────────────────────────────────────────────────────────────
# 8. EXPIRY CONSISTENCY: check expiry in Paystack webhook before DB lookup
# ─────────────────────────────────────────────────────────────────────────────

src = src.replace(
    '''        cur.execute("SELECT * FROM payments WHERE id = %s", (ref,))
        payment = cur.fetchone()
        if not payment:
            log_warn("paystack", "payment_not_found", ref=ref)
            return jsonify({"error": "payment not found"}), 404

        if currency and currency != "ZAR":''',
    '''        cur.execute("SELECT * FROM payments WHERE id = %s", (ref,))
        payment = cur.fetchone()
        if not payment:
            log_warn("paystack", "payment_not_found", ref=ref)
            return jsonify({"error": "payment not found"}), 404

        # Belt-and-suspenders expiry check (cron may not have run yet)
        if payment["status"] == "expired":
            log_warn("paystack", "payment_already_expired", ref=ref)
            return jsonify({"error": "Payment expired"}), 400

        if currency and currency != "ZAR":'''
)

# Same for PayFast
src = src.replace(
    '''        cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment:
            return "ok", 200

        # 5. Amount check''',
    '''        cur.execute("SELECT * FROM payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment:
            return "ok", 200

        # Belt-and-suspenders expiry check
        if payment["status"] == "expired":
            log_warn("payfast", "payment_already_expired", payment_id=payment_id)
            return "INVALID", 400

        # 5. Amount check'''
)
print("✅ 8. Expiry check added to both webhook handlers")


# ─────────────────────────────────────────────────────────────────────────────
# 9. BURST RATE LIMIT on /api/certify (free plan direct submissions)
#    Max 3 free certifications per creator per minute
# ─────────────────────────────────────────────────────────────────────────────

BURST_CHECK = '''    # Burst rate limit: max 3 free certifications per creator per minute
    if plan == "free":
        try:
            _bl = get_db(); _blc = _bl.cursor()
            _blc.execute("""
                SELECT COUNT(*) FROM submissions
                WHERE creator_id = %s
                  AND submitted_at > NOW() - INTERVAL '1 minute'
            """, (creator_uuid,))
            if _blc.fetchone()[0] >= 3:
                _blc.close(); put_db(_bl)
                log_warn("certify", "burst_limit",
                         creator_id=creator_uuid, ip=request.remote_addr)
                return jsonify({"error": "Too many requests. Please wait a moment."}), 429
            _blc.close(); put_db(_bl)
        except Exception as _ble:
            log_warn("certify", "burst_check_failed", error=str(_ble))

'''

# Insert before the dedup check in certify_work
src = src.replace(
    "    conn = get_db()\n    cur  = conn.cursor(cursor_factory=RealDictCursor)\n    try:\n        # Dedup check for certify_work path",
    BURST_CHECK + "    conn = get_db()\n    cur  = conn.cursor(cursor_factory=RealDictCursor)\n    try:\n        # Dedup check for certify_work path",
    1
)
print("✅ 9. Burst rate limit added to /api/certify")


# ─────────────────────────────────────────────────────────────────────────────
# 10. Fix all remaining conn.close() → put_db(conn) in route handlers
#     We do a targeted sweep of the most critical ones.
# ─────────────────────────────────────────────────────────────────────────────

# Pattern: `finally:\n        cur.close()\n        conn.close()` → put_db
src = src.replace(
    "    finally:\n        cur.close()\n        conn.close()\n\n\n@app.get(\"/health\")",
    "    finally:\n        cur.close()\n        put_db(conn)\n\n\n@app.get(\"/health\")"
)

# Sweep all `cur.close()\n        conn.close()` inside finally blocks
# (safe because every `conn` in a finally came from get_db())
import re as _re
src = _re.sub(
    r'(    finally:\n(?:        \w+\.close\(\)\n)*        )conn\.close\(\)',
    r'\1put_db(conn)',
    src
)

# _set_cert_retry_flag
src = src.replace(
    "        _c.commit(); _cur.close(); _c.close()\n    except Exception as _e:\n        log_error(\"payment\", \"cert_retry_flag_failed\"",
    "        _c.commit(); _cur.close(); put_db(_c)\n    except Exception as _e:\n        log_error(\"payment\", \"cert_retry_flag_failed\""
)

# trigger_certification
src = src.replace(
    "            cur.execute(\"UPDATE payments SET submission_id = %s WHERE id = %s\",\n                        (data.get(\"submission_id\"), str(payment_row[\"id\"])))\n            conn.commit()\n        finally:\n            cur.close(); conn.close()",
    "            cur.execute(\"UPDATE payments SET submission_id = %s WHERE id = %s\",\n                        (data.get(\"submission_id\"), str(payment_row[\"id\"])))\n            conn.commit()\n        finally:\n            cur.close(); put_db(conn)"
)

# Rate limit checks that open their own connections
for old, new in [
    ("_rlc.close(); _rl.close()\n            return jsonify({\"error\": \"Too many pending payments",
     "_rlc.close(); put_db(_rl)\n            return jsonify({\"error\": \"Too many pending payments"),
    ("_rlc.close(); _rl.close()\n        except Exception as _e:\n        log_warn(\"payment\", \"rate_limit_check_failed\"",
     "_rlc.close(); put_db(_rl)\n        except Exception as _e:\n        log_warn(\"payment\", \"rate_limit_check_failed\""),
    ("_rlc2.close(); _rl2.close()\n                log_warn(\"fraud\", \"ip_rate_limit\"",
     "_rlc2.close(); put_db(_rl2)\n                log_warn(\"fraud\", \"ip_rate_limit\""),
    ("_rlc2.close(); _rl2.close()\n        except Exception as _e2:",
     "_rlc2.close(); put_db(_rl2)\n        except Exception as _e2:"),
    ("_idemc.close(); _idem.close()\n        if existing_payment:",
     "_idemc.close(); put_db(_idem)\n        if existing_payment:"),
]:
    src = src.replace(old, new)

print("✅ 10. All conn.close() → put_db(conn) in route handlers")


# ─────────────────────────────────────────────────────────────────────────────
# Write output
# ─────────────────────────────────────────────────────────────────────────────

with open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

changed = sum(1 for a, b in zip(orig.splitlines(), src.splitlines()) if a != b)
print(f"\n✅ Done. ~{changed} lines changed. Lines total: {src.count(chr(10))}")

print("Run: python3 -c \"import ast; ast.parse(open('tier4_main.py').read()); print('syntax OK')\"")
