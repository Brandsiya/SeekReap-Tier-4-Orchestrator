#!/usr/bin/env python3
"""
install_profile_trust_api.py

Adds real trust wiring:
  - Patches the existing GET /api/profile/<profile_id> to include a
    "trust": {trusted_by_count, trusted_count, is_trusted_by_viewer} object,
    computed from the real user_trusts table.
  - Adds POST   /api/profile/<profile_id>/trust    (trust this profile)
  - Adds DELETE /api/profile/<profile_id>/trust    (remove your trust)

No fabricated numbers — trust counts come from user_trusts rows only.

Usage:
    cd ~/SeekReap-Tier-4-Orchestrator
    python3 install_profile_trust_api.py
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("tier4_main.py")
MARKER = "# ══ PROFILE_TRUST_API_V1 ══"
DEP_MARKER = "# ══ PROFILE_PUBLIC_API_V1 ══"

OLD_RETURN_BLOCK = '''        if not is_owner:
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
    })'''

NEW_RETURN_BLOCK = '''        if not is_owner:
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

        # ── PROFILE_TRUST_API_V1: real trust summary from user_trusts ───────
        trust = {"trusted_by_count": 0, "trusted_count": 0, "is_trusted_by_viewer": False}
        try:
            cur.execute("SELECT COUNT(*) AS n FROM user_trusts WHERE trusted_user_id = %s",
                        (profile_id,))
            trust["trusted_by_count"] = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM user_trusts WHERE trustor_user_id = %s",
                        (profile_id,))
            trust["trusted_count"] = cur.fetchone()["n"]
            if viewer_id:
                cur.execute("""
                    SELECT 1 FROM user_trusts
                    WHERE trustor_user_id = %s AND trusted_user_id = %s
                """, (viewer_id, profile_id))
                trust["is_trusted_by_viewer"] = cur.fetchone() is not None
        except Exception as e:
            conn.rollback()
            log_warn("profile", "trust_summary_failed", profile_id=profile_id, error=str(e))

    return jsonify({
        "profile":        _sanitize_public_profile(profile),
        "creative_roles": [_profile_row_to_json(r) for r in roles],
        "portfolio": {
            "sections": [_profile_row_to_json(r) for r in sections],
            "works":    [_profile_row_to_json(r) for r in works],
        },
        "stats":    stats,
        "trust":    trust,
        "is_owner": is_owner,
    })'''

NEW_ROUTES_CODE = '''

# ══ PROFILE_TRUST_API_V1 ══
# ══════════════════════════════════════════════════════════════════
# TRUST API v1 — real trust relationships via user_trusts.
# trusted_by_count = how many other users trust this profile
# trusted_count    = how many other users this user trusts
# ══════════════════════════════════════════════════════════════════

@app.post("/api/profile/<profile_id>/trust")
def trust_profile(profile_id):
    if not _valid_uuid(profile_id):
        return jsonify({"error": "invalid profile id"}), 400
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    if actor_id == profile_id:
        return jsonify({"error": "cannot trust your own profile"}), 400

    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("SELECT id FROM user_profiles WHERE id = %s AND deleted_at IS NULL",
                    (profile_id,))
        if not cur.fetchone():
            return jsonify({"error": "profile not found"}), 404
        try:
            cur.execute("""
                SELECT id FROM user_trusts
                WHERE trustor_user_id = %s AND trusted_user_id = %s
            """, (actor_id, profile_id))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO user_trusts (trustor_user_id, trusted_user_id)
                    VALUES (%s, %s)
                """, (actor_id, profile_id))
                conn.commit()
            cur.execute("SELECT COUNT(*) AS n FROM user_trusts WHERE trusted_user_id = %s",
                        (profile_id,))
            trusted_by_count = cur.fetchone()["n"]
        except Exception as e:
            conn.rollback()
            log_error("profile", "trust_failed", profile_id=profile_id, error=str(e))
            return jsonify({"error": "failed to record trust"}), 500

    return jsonify({
        "status":               "trusted",
        "trusted_by_count":     trusted_by_count,
        "is_trusted_by_viewer": True,
    })


@app.delete("/api/profile/<profile_id>/trust")
def untrust_profile(profile_id):
    if not _valid_uuid(profile_id):
        return jsonify({"error": "invalid profile id"}), 400
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401

    with db_cursor(RealDictCursor) as (conn, cur):
        try:
            cur.execute("""
                DELETE FROM user_trusts
                WHERE trustor_user_id = %s AND trusted_user_id = %s
            """, (actor_id, profile_id))
            conn.commit()
            cur.execute("SELECT COUNT(*) AS n FROM user_trusts WHERE trusted_user_id = %s",
                        (profile_id,))
            trusted_by_count = cur.fetchone()["n"]
        except Exception as e:
            conn.rollback()
            log_error("profile", "untrust_failed", profile_id=profile_id, error=str(e))
            return jsonify({"error": "failed to remove trust"}), 500

    return jsonify({
        "status":               "untrusted",
        "trusted_by_count":     trusted_by_count,
        "is_trusted_by_viewer": False,
    })
'''


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from ~/SeekReap-Tier-4-Orchestrator")
        sys.exit(1)

    original = TARGET.read_text()

    if MARKER in original:
        print("Trust API already installed (marker found). Nothing to do.")
        sys.exit(0)

    if DEP_MARKER not in original:
        print("ERROR: install_profile_public_api.py doesn't appear to have been run yet.")
        print("Run install_profile_api.py then install_profile_public_api.py first.")
        sys.exit(1)

    if original.count(OLD_RETURN_BLOCK) != 1:
        found = original.count(OLD_RETURN_BLOCK)
        print(f"❌ Could not find the expected get_public_profile return block "
              f"(found {found} exact matches, need exactly 1).")
        print("   Your tier4_main.py may have been edited since install_profile_public_api.py")
        print("   ran, so this patch can't safely apply. No changes were made.")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET.with_name(f"tier4_main.py.bak_{ts}")
    shutil.copy2(TARGET, backup_path)
    print(f"Backed up original to {backup_path}")

    patched = original.replace(OLD_RETURN_BLOCK, NEW_RETURN_BLOCK)
    patched = patched + NEW_ROUTES_CODE
    TARGET.write_text(patched)
    print("Patched get_public_profile() to include real trust summary")
    print(f"Appended Trust API routes ({len(NEW_ROUTES_CODE)} bytes added)")

    print("Validating syntax with py_compile...")
    result = subprocess.run([sys.executable, "-m", "py_compile", str(TARGET)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print("SYNTAX ERROR — restoring backup.")
        print(result.stderr)
        shutil.copy2(backup_path, TARGET)
        sys.exit(1)

    print("✅ Syntax OK. Trust API installed successfully.")
    print()
    print("GET /api/profile/<profile_id> now includes:")
    print('  "trust": {"trusted_by_count": N, "trusted_count": N, "is_trusted_by_viewer": bool}')
    print()
    print("New endpoints:")
    print("  POST   /api/profile/<profile_id>/trust   — trust this profile")
    print("  DELETE /api/profile/<profile_id>/trust   — remove your trust")
    print()
    print("Next: git add -A && git commit -m 'Add trust API (user_trusts)' && git push")


if __name__ == "__main__":
    main()
