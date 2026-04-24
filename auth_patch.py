# ══════════════════════════════════════════════════════════════════════════════
# JWT / Supabase token verification  — ES256 + JWKS (replaces HS256 block)
# ══════════════════════════════════════════════════════════════════════════════

import threading
from jose import jwt as _jose_jwt, exceptions as _jose_exc

SUPABASE_URL        = os.environ.get("SUPABASE_URL", "")          # e.g. https://xxxx.supabase.co
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")   # kept for HS256 fallback only

# ── JWKS cache ────────────────────────────────────────────────────────────────
# Keys are cached in-process and refreshed every 6 hours, or on cache miss
# (kid not found). This avoids a network call on every request while still
# handling key rotation gracefully.
_jwks_cache:      list   = []          # list of JWK dicts
_jwks_fetched_at: float  = 0.0
_jwks_lock               = threading.Lock()
_JWKS_TTL_SECONDS        = 6 * 3600   # 6 hours


def _jwks_url() -> str:
    base = SUPABASE_URL.rstrip("/")
    return f"{base}/auth/v1/keys"


def _fetch_jwks(force: bool = False) -> list:
    """
    Returns the cached JWKS key list, refreshing when stale or forced.
    Thread-safe. Returns [] on error so callers can fall back gracefully.
    """
    global _jwks_cache, _jwks_fetched_at
    now = _time.time()
    with _jwks_lock:
        if not force and _jwks_cache and (now - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
            return _jwks_cache
        try:
            resp = requests.get(_jwks_url(), timeout=5)
            if resp.status_code == 200:
                keys = resp.json().get("keys", [])
                _jwks_cache      = keys
                _jwks_fetched_at = now
                log_info("auth", "jwks_refreshed", key_count=len(keys))
                return keys
            else:
                log_warn("auth", "jwks_fetch_non200", status=resp.status_code)
        except Exception as e:
            log_warn("auth", "jwks_fetch_error", error=str(e))
        return _jwks_cache   # return stale cache on error rather than []


def _verify_supabase_jwt(token: str) -> dict | None:
    """
    Verifies a Supabase JWT.

    Strategy (in order):
      1. Inspect header to determine algorithm.
      2. ES256  → verify via JWKS (kid lookup, auto-refresh on miss).
      3. HS256  → verify via SUPABASE_JWT_SECRET (legacy / local dev).
      4. Secret missing & algo unknown → decode without verify (dev only, warns loudly).

    Returns the claims dict on success, None on any failure.
    """
    if not token:
        return None

    # ── Step 1: read header (no verification yet) ────────────────────────────
    try:
        header = _jose_jwt.get_unverified_header(token)
    except Exception as e:
        log_warn("auth", "jwt_bad_header", error=str(e))
        return None

    alg = header.get("alg", "")
    kid = header.get("kid", "")

    # ── Step 2: ES256 path (Supabase default) ────────────────────────────────
    if alg == "ES256":
        for force_refresh in (False, True):        # retry once with fresh keys
            keys = _fetch_jwks(force=force_refresh)
            matched = [k for k in keys if k.get("kid") == kid] if kid else keys
            if not matched:
                if force_refresh:
                    log_warn("auth", "jwks_kid_not_found", kid=kid)
                    return None
                continue   # trigger forced refresh
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

    # ── Step 3: HS256 path (legacy / local dev with JWT secret) ─────────────
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

    # ── Step 4: Unknown algorithm — hard reject ──────────────────────────────
    log_warn("auth", "jwt_unknown_alg", alg=alg)
    return None
