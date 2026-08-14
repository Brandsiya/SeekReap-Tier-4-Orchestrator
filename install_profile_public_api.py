#!/usr/bin/env python3
"""
install_profile_public_api.py

Appends the public-profile endpoints to tier4_main.py:
  GET  /api/profile/<profile_id>          — public profile view (any creator,
                                             including self), logs a real view
                                             row in user_profile_views, returns
                                             real stats via get_profile_statistics()
  POST /api/profile/<profile_id>/share    — records a real share_events row

Depends on the Profile Domain API installed earlier (install_profile_api.py) —
reuses _profile_row_to_json, _valid_uuid, db_cursor, RealDictCursor, etc.
Also depends on the Phase 2 SQL migration (user_profile_views reuse, share_events,
get_profile_statistics()) already applied in Supabase.

Usage:
    cd ~/SeekReap-Tier-4-Orchestrator
    python3 install_profile_public_api.py
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("tier4_main.py")
MARKER = "# ══ PROFILE_PUBLIC_API_V1 ══"

PUBLIC_API_CODE = '''

# ══ PROFILE_PUBLIC_API_V1 ══
# ══════════════════════════════════════════════════════════════════
# PUBLIC PROFILE API v1 — cross-creator profile viewing + real stats
# Uses the Phase 2 tables: user_profile_views (reused), share_events (new),
# and the get_profile_statistics() SQL function.
# ══════════════════════════════════════════════════════════════════

def _optional_actor_id(req):
    """Like _require_auth but returns None instead of erroring when there's
    no/invalid token — this endpoint is public and works for anonymous
    visitors, but still identifies the viewer when they're logged in."""
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    claims = _verify_supabase_jwt(auth_header[7:].strip())
    if not claims:
        return None
    sub = claims.get("sub", "")
    return sub if _valid_uuid(sub) else None


# Fields never returned from the public endpoint, regardless of visibility
# settings — these are private-contact/identity fields, not profile display
# fields, even when the profile itself is public.
PUBLIC_PROFILE_EXCLUDE = {
    'identity_number_encrypted', 'identity_number_hashed', 'identity_number_last4',
    'passport_number_encrypted', 'passport_number_hashed', 'passport_number_last4',
    'recovery_email', 'primary_phone', 'secondary_phone', 'contact_preference',
    'postal_address_line1', 'postal_address_line2', 'postal_city', 'postal_province',
    'postal_country', 'postal_postal_code',
    'physical_address_line1', 'physical_address_line2', 'physical_city',
    'physical_province', 'physical_country', 'physical_postal_code',
    'user_notifications_preference', 'marketing_opt_in',
    'onboarding_step', 'onboarding_completed', 'onboarding_completed_at',
    'account_status', 'search_vector', 'user_invitations_preference',
    'profile_language_preference', 'profile_timezone_preference', 'searchable',
}


def _sanitize_public_profile(row):
    p = _profile_row_to_json(row)
    for k in list(p.keys()):
        if k in PUBLIC_PROFILE_EXCLUDE:
            p.pop(k, None)
    # Respect the creator's own visibility toggles for location fields
    if not p.get('show_country'):
        p.pop('country_of_residence', None)
        p.pop('nationality_code', None)
    if not p.get('show_city'):
        p.pop('city', None)
    if not p.get('show_location'):
        p.pop('province', None)
    return p


@app.get("/api/profile/<profile_id>")
def get_public_profile(profile_id):
    """Public profile view — works for any creator's profile, including your
    own. Logs a real view (skipping self-views) and returns real stats from
    get_profile_statistics()."""
    if not _valid_uuid(profile_id):
        return jsonify({"error": "invalid profile id"}), 400

    viewer_id = _optional_actor_id(request)

    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT p.*, ut.code AS user_type_code, ut.display_name AS user_type_label,
                   ut.icon AS user_type_icon, ut.color AS user_type_color,
                   vs.code AS verification_code, vs.display_name AS verification_label,
                   vs.badge_color AS verification_badge_color
            FROM user_profiles p
            LEFT JOIN user_types ut ON ut.id = p.user_type_id
            LEFT JOIN verification_statuses vs ON vs.id = p.verification_status_id
            WHERE p.id = %s AND p.deleted_at IS NULL
        """, (profile_id,))
        profile = cur.fetchone()
        if not profile:
            return jsonify({"error": "not found"}), 404

        is_owner = (viewer_id == profile_id)
        if profile.get('profile_visibility') == 'private' and not is_owner:
            return jsonify({"error": "not found"}), 404

        cur.execute("""
            SELECT ucr.id, ucr.featured, ucr.display_order,
                   cr.code, cr.name, cr.category, cr.icon, cr.color
            FROM user_creative_roles ucr
            JOIN creative_roles cr ON cr.id = ucr.creative_role_id
            WHERE ucr.user_id = %s AND ucr.deleted_at IS NULL
            ORDER BY ucr.display_order
        """, (profile_id,))
        roles = cur.fetchall()

        cur.execute("""
            SELECT * FROM portfolio_sections WHERE user_id = %s ORDER BY display_order
        """, (profile_id,))
        sections = cur.fetchall()

        if is_owner:
            cur.execute("""
                SELECT * FROM creative_works
                WHERE creator_id = %s AND is_deleted IS NOT TRUE
                ORDER BY created_at DESC LIMIT 100
            """, (profile_id,))
        else:
            cur.execute("""
                SELECT * FROM creative_works
                WHERE creator_id = %s AND is_deleted IS NOT TRUE
                  AND (visibility IS NULL OR visibility != 'private')
                ORDER BY created_at DESC LIMIT 100
            """, (profile_id,))
        works = cur.fetchall()

        stats = {}
        try:
            cur.execute("SELECT * FROM get_profile_statistics(%s)", (profile_id,))
            stats_row = cur.fetchone()
            if stats_row:
                stats = _profile_row_to_json(stats_row)
        except Exception as e:
            conn.rollback()
            log_warn("profile", "get_profile_statistics_failed",
                     profile_id=profile_id, error=str(e))

        # Log the view — skip self-views so checking your own profile
        # doesn't inflate your own count.
        if not is_owner:
            try:
                client_ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")
                             .split(",")[0].strip()) or None
                cur.execute("""
                    INSERT INTO user_profile_views
                        (user_id, viewer_id, ip_address, referrer, user_agent)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    profile_id, viewer_id, client_ip,
                    (request.headers.get("Referer", "") or "")[:2048] or None,
                    (request.headers.get("User-Agent", "") or "")[:512] or None,
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                log_warn("profile", "view_log_failed", profile_id=profile_id, error=str(e))

    return jsonify({
        "profile":        _sanitize_public_profile(profile),
        "creative_roles": [_profile_row_to_json(r) for r in roles],
        "portfolio": {
            "sections": [_profile_row_to_json(r) for r in sections],
            "works":    [_profile_row_to_json(r) for r in works],
        },
        "stats":    stats,
        "is_owner": is_owner,
    })


VALID_SHARE_CHANNELS = {
    'link', 'copy_link', 'twitter', 'x', 'facebook', 'whatsapp',
    'linkedin', 'email', 'sms', 'other',
}


@app.post("/api/profile/<profile_id>/share")
def share_profile(profile_id):
    """Records a real share event. The profile id is taken from the URL
    (server-side), never trusted from the request body."""
    if not _valid_uuid(profile_id):
        return jsonify({"error": "invalid profile id"}), 400

    body = request.get_json(force=True) or {}
    channel = _clamp_str(body.get("share_channel") or body.get("channel"), 32).strip().lower()
    if channel not in VALID_SHARE_CHANNELS:
        return jsonify({
            "error": f"invalid share_channel; must be one of {sorted(VALID_SHARE_CHANNELS)}"
        }), 400

    sharer_id = _optional_actor_id(request)

    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("SELECT id FROM user_profiles WHERE id = %s AND deleted_at IS NULL",
                    (profile_id,))
        if not cur.fetchone():
            return jsonify({"error": "profile not found"}), 404

        try:
            client_ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")
                         .split(",")[0].strip()) or None
            cur.execute("""
                INSERT INTO share_events
                    (entity_type, entity_id, sharer_user_id, share_channel,
                     source_url, referrer, user_agent, ip_address)
                VALUES ('profile', %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
                profile_id, sharer_id, channel,
                _clamp_str(body.get("source_url"), 2048) or None,
                (request.headers.get("Referer", "") or "")[:2048] or None,
                (request.headers.get("User-Agent", "") or "")[:512] or None,
                client_ip,
            ))
            row = cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_error("profile", "share_event_failed", profile_id=profile_id, error=str(e))
            return jsonify({"error": "failed to record share"}), 500

    return jsonify({
        "status":        "recorded",
        "share_channel": channel,
        "id":            str(row["id"]),
        "created_at":    row["created_at"].isoformat(),
    }), 201
'''


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from ~/SeekReap-Tier-4-Orchestrator")
        sys.exit(1)

    original = TARGET.read_text()

    if MARKER in original:
        print("Public Profile API already installed (marker found). Nothing to do.")
        sys.exit(0)

    if "# ══ PROFILE_DOMAIN_API_V1 ══" not in original:
        print("ERROR: install_profile_api.py doesn't appear to have been run yet.")
        print("This installer depends on helpers it defines (_profile_row_to_json, etc).")
        print("Run install_profile_api.py first.")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET.with_name(f"tier4_main.py.bak_{ts}")
    shutil.copy2(TARGET, backup_path)
    print(f"Backed up original to {backup_path}")

    new_content = original + PUBLIC_API_CODE
    TARGET.write_text(new_content)
    print(f"Appended Public Profile API to {TARGET} ({len(PUBLIC_API_CODE)} bytes added)")

    print("Validating syntax with py_compile...")
    result = subprocess.run([sys.executable, "-m", "py_compile", str(TARGET)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print("SYNTAX ERROR — restoring backup.")
        print(result.stderr)
        shutil.copy2(backup_path, TARGET)
        sys.exit(1)

    print("✅ Syntax OK. Public Profile API installed successfully.")
    print()
    print("New endpoints:")
    print("  GET  /api/profile/<profile_id>          — public profile + real stats")
    print("  POST /api/profile/<profile_id>/share     — records a real share event")
    print()
    print("Next: git add -A && git commit -m 'Add public profile view + share API' && git push")


if __name__ == "__main__":
    main()
