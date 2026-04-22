"""
SeekReap Tier-4 — Final stabilization patch.
Fixes 6 targeted issues identified in the last audit.
No wholesale rewrites — only the exact lines that are wrong get changed.

Run from SeekReap-Tier-4-Orchestrator/:
    python3 patch_final.py
    git add tier4_main.py
    git commit -m "fix: stabilization — retry 2xx-only, SKIP LOCKED, indices, health, shutdown, cert retry"
    git push
"""

FILE = "tier4_main.py"
src  = open(FILE, encoding="utf-8").read()
orig = src
applied = []

# ══════════════════════════════════════════════════════════════════════════════
# FIX 1 — _retry_request: accept ONLY 2xx as success (was < 500, passed 4xx through)
# ══════════════════════════════════════════════════════════════════════════════
OLD1 = '''\
            if resp.status_code < 500:
                if gateway:
                    _circuit_record_success(gateway)
                return resp, None'''

NEW1 = '''\
            # FIX: only 2xx is success — 4xx passes through as failure
            if 200 <= resp.status_code < 300:
                if gateway:
                    _circuit_record_success(gateway)
                return resp, None
            last_exc = Exception(f"HTTP {resp.status_code}: non-2xx")'''

if OLD1 in src:
    src = src.replace(OLD1, NEW1, 1)
    applied.append("✅ Fix 1: _retry_request accepts only 2xx responses")
else:
    print("⚠️  Fix 1: marker not found — check _retry_request manually")

# ══════════════════════════════════════════════════════════════════════════════
# FIX 2 — retry-certifications: FOR UPDATE SKIP LOCKED (prevents double-processing)
# ══════════════════════════════════════════════════════════════════════════════
OLD2 = '''\
            ORDER BY created_at LIMIT 20
        """)
        rows = cur.fetchall()

    retried = 0'''

NEW2 = '''\
            ORDER BY created_at LIMIT 20
            FOR UPDATE SKIP LOCKED
        """)
        rows = cur.fetchall()

    retried = 0'''

if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    applied.append("✅ Fix 2: FOR UPDATE SKIP LOCKED on retry-certifications SELECT")
else:
    print("⚠️  Fix 2: retry SELECT marker not found — add FOR UPDATE SKIP LOCKED manually")

# ══════════════════════════════════════════════════════════════════════════════
# FIX 3 — ensure_payments_tables: add missing composite indices
#          (submissions rate-limit scan + payment_events IP scan were full-table)
# ══════════════════════════════════════════════════════════════════════════════
OLD3 = '''\
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS cert_retry_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP")

        conn.commit()'''

NEW3 = '''\
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

        conn.commit()'''

if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    applied.append("✅ Fix 3: composite indices for submissions + payment_events rate-limit queries")
else:
    print("⚠️  Fix 3: table migration marker not found — add indices manually")

# ══════════════════════════════════════════════════════════════════════════════
# FIX 4 — /health: report pool state + open circuits (not just DB ping)
# ══════════════════════════════════════════════════════════════════════════════
OLD4 = '''\
@app.get("/health")
def health():
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT 1")
        return jsonify({"status": "ok", "tier": 4})
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500'''

NEW4 = '''\
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
    return jsonify({"status": "ok", "tier": 4})'''

if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    applied.append("✅ Fix 4: /health reports pool state + open circuit breakers")
else:
    print("⚠️  Fix 4: health endpoint not found")

# ══════════════════════════════════════════════════════════════════════════════
# FIX 5 — Graceful shutdown: close pool on process exit
# ══════════════════════════════════════════════════════════════════════════════
SHUTDOWN_HOOK = '''\

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

'''

if "_shutdown_pool" not in src:
    # Insert just before if __name__ == "__main__"
    src = src.replace(
        '\nif __name__ == "__main__":',
        SHUTDOWN_HOOK + 'if __name__ == "__main__":',
        1
    )
    applied.append("✅ Fix 5: atexit graceful pool shutdown")
else:
    print("⏭  Fix 5: shutdown hook already present")

# ══════════════════════════════════════════════════════════════════════════════
# FIX 6 — trigger_certification: use _retry_request instead of bare requests.post
# ══════════════════════════════════════════════════════════════════════════════
OLD6 = '''\
        r = requests.post(
            TIER4_INTERNAL + "/api/certify",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=30,
        )
        data = r.json()'''

NEW6 = '''\
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
        data = r.json()'''

if OLD6 in src:
    src = src.replace(OLD6, NEW6, 1)
    applied.append("✅ Fix 6: trigger_certification uses _retry_request (2 attempts)")
else:
    print("⚠️  Fix 6: trigger_certification requests.post block not found")

# ══════════════════════════════════════════════════════════════════════════════
# Write + report
# ══════════════════════════════════════════════════════════════════════════════
if src != orig:
    open(FILE, "w", encoding="utf-8").write(src)
    print("\n" + "═" * 62)
    print("APPLIED:")
    for a in applied:
        print("  " + a)
    print("═" * 62)
    print(f"\n{len(applied)}/6 fixes applied.")
    if len(applied) < 6:
        print("⚠️  Some fixes were skipped — see warnings above.")
    print("""
Next:
  git add tier4_main.py
  git commit -m "fix: stabilization — retry 2xx-only, SKIP LOCKED, indices, health, shutdown, cert retry"
  git push

Also run the migration SQL (migrate_final.sql) against your Neon DB.
""")
else:
    print("\n⚠️  No changes written — all markers missed.")
    print("The file may already be patched, or the source has changed.")
