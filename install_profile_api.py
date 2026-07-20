#!/usr/bin/env python3
"""
install_profile_api.py

Appends the SeekReap Profile Domain API (Core Identity, Reference,
Resume, Portfolio — 27 tables) to tier4_main.py.

Usage:
    cd ~/SeekReap-Tier-4-Orchestrator
    python3 install_profile_api.py

Safe to re-run: it checks for the marker and refuses to double-install.
Creates a timestamped backup before touching anything.
"""
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("tier4_main.py")
MARKER = "# ══ PROFILE_DOMAIN_API_V1 ══"

PROFILE_API_CODE = '''

# ══ PROFILE_DOMAIN_API_V1 ══
# ══════════════════════════════════════════════════════════════════
# PROFILE DOMAIN API v1 — Core Identity + Reference + Resume + Portfolio
# Covers all 27 tables of the Profile Domain.
# Auth: Supabase JWT sub is used directly as the row-owning UUID
# (this schema is auth.uid() = user_id / = id everywhere, so no
# firebase-style uuid5 hashing is needed here, unlike /api/certify).
# ══════════════════════════════════════════════════════════════════

from decimal import Decimal as _Decimal


def _profile_actor_id(claims):
    sub = claims.get("sub", "")
    return sub if _valid_uuid(sub) else None


def _profile_row_to_json(row):
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, _Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


# ── Generic Resume/Identity section CRUD config ──────────────────────────────
RESUME_SECTIONS = {
    "identifiers": {
        "table": "user_identifiers",
        "writable": ["identifier_type", "identifier_value", "status",
                     "issuing_authority", "issued_at", "expires_at",
                     "is_public", "metadata"],
    },
    "linked-accounts": {
        "table": "user_linked_accounts",
        "writable": ["platform", "external_id", "username", "display_name",
                     "profile_url", "avatar"],
    },
    "creative-roles": {
        "table": "user_creative_roles",
        "writable": ["creative_role_id", "featured", "display_order", "metadata"],
    },
    "languages": {
        "table": "user_languages",
        "writable": ["language_code", "language_name", "proficiency",
                     "is_primary", "can_create_content", "can_offer_services",
                     "display_order"],
    },
    "skills": {
        "table": "user_skills",
        "writable": ["skill_name", "proficiency_level", "years_experience",
                     "featured", "display_order", "metadata"],
    },
    "certifications": {
        "table": "user_certifications",
        "writable": ["certification_name", "issuing_organization", "credential_id",
                     "credential_url", "issue_date", "expiry_date", "never_expires",
                     "evidence_url", "featured", "display_order", "metadata",
                     "certificate_file_id"],
    },
    "publications": {
        "table": "user_publications",
        "writable": ["title", "publication_type", "publisher", "publication_date",
                     "isbn", "doi", "journal", "volume", "issue", "pages",
                     "publication_url", "description", "authors", "featured",
                     "visibility", "display_order", "metadata"],
    },
    "projects": {
        "table": "user_projects",
        "writable": ["title", "project_type", "role", "organization_name",
                     "description", "start_date", "end_date", "is_current",
                     "project_url", "cover_image_url", "featured", "visibility",
                     "display_order", "metadata"],
    },
    "education": {
        "table": "user_education",
        "writable": ["institution_name", "institution_type", "degree",
                     "field_of_study", "specialization", "start_date", "end_date",
                     "currently_studying", "grade", "honors", "description",
                     "certificate_url", "country_code", "city", "featured",
                     "visibility", "display_order", "metadata"],
    },
    "employment": {
        "table": "user_employment_history",
        "writable": ["organization_name", "organization_type", "relationship_type",
                     "job_title", "employment_type", "start_date", "end_date",
                     "is_current", "description", "website", "logo_url",
                     "is_public", "display_order"],
    },
    "achievements": {
        "table": "user_achievements",
        "writable": ["category", "title", "subtitle", "issuer", "organization",
                     "description", "achievement_date", "country_code", "city",
                     "evidence_url", "evidence_file_id", "achievement_level",
                     "visibility", "featured", "display_order", "metadata"],
    },
    "portfolio-sections": {
        "table": "portfolio_sections",
        "writable": ["name", "slug", "description", "display_order", "is_visible"],
    },
    "creative-works": {
        "table": "creative_works",
        "user_col": "creator_id",
        "writable": ["title", "subtitle", "artistic_name", "work_type", "description",
                     "language", "creation_date", "completion_date",
                     "first_publication_date", "visibility", "is_collaborative",
                     "portfolio_featured", "allow_indexing", "thumbnail_url",
                     "cover_image_url", "genre", "category", "tags",
                     "portfolio_visibility", "allow_search", "allow_recommendations",
                     "available_for_licensing", "available_for_sale",
                     "available_for_commission", "allow_ai_training",
                     "allow_ai_reference"],
    },
}


def _resume_list_rows(section, actor_id):
    cfg = RESUME_SECTIONS[section]
    table = cfg["table"]
    user_col = cfg.get("user_col", "user_id")
    with db_cursor(RealDictCursor) as (conn, cur):
        if table == "portfolio_sections":
            cur.execute(f"""
                SELECT * FROM {table} WHERE {user_col} = %s
                ORDER BY display_order
            """, (actor_id,))
        else:
            cur.execute(f"""
                SELECT * FROM {table}
                WHERE {user_col} = %s AND deleted_at IS NULL
                ORDER BY COALESCE(display_order, 0), created_at
            """, (actor_id,))
        return cur.fetchall()


@app.get("/api/profile/resume/<section>")
def get_resume_section(section):
    if section not in RESUME_SECTIONS:
        return jsonify({"error": "unknown section"}), 404
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    try:
        rows = _resume_list_rows(section, actor_id)
        return jsonify({"section": section,
                        "items": [_profile_row_to_json(r) for r in rows]})
    except Exception as e:
        log_error("profile", "resume_list_failed", section=section, error=str(e))
        return jsonify({"error": str(e)}), 500


@app.post("/api/profile/resume/<section>")
def create_resume_item(section):
    if section not in RESUME_SECTIONS:
        return jsonify({"error": "unknown section"}), 404
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401

    cfg = RESUME_SECTIONS[section]
    table = cfg["table"]
    user_col = cfg.get("user_col", "user_id")
    body = request.get_json(force=True) or {}

    cols, vals = [], []
    for c in cfg["writable"]:
        if c in body:
            v = body[c]
            cols.append(c)
            vals.append(Json(v) if isinstance(v, (dict, list)) else v)
    if not cols:
        return jsonify({"error": "no writable fields provided"}), 400

    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join(cols)
    try:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute(f"""
                INSERT INTO {table} ({user_col}, {col_list})
                VALUES (%s, {placeholders})
                RETURNING *
            """, [actor_id] + vals)
            row = cur.fetchone()
            conn.commit()
        return jsonify(_profile_row_to_json(row)), 201
    except Exception as e:
        log_error("profile", "resume_create_failed", section=section, error=str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/resume/<section>/<item_id>", methods=["PUT", "PATCH"])
def update_resume_item(section, item_id):
    if section not in RESUME_SECTIONS:
        return jsonify({"error": "unknown section"}), 404
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    if not _valid_uuid(item_id):
        return jsonify({"error": "invalid id"}), 400

    cfg = RESUME_SECTIONS[section]
    table = cfg["table"]
    user_col = cfg.get("user_col", "user_id")
    body = request.get_json(force=True) or {}

    sets, vals = [], []
    for c in cfg["writable"]:
        if c in body:
            v = body[c]
            sets.append(f"{c} = %s")
            vals.append(Json(v) if isinstance(v, (dict, list)) else v)
    if not sets:
        return jsonify({"error": "no writable fields provided"}), 400

    vals += [item_id, actor_id]
    try:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute(f"""
                UPDATE {table} SET {', '.join(sets)}
                WHERE id = %s AND {user_col} = %s
                RETURNING *
            """, vals)
            row = cur.fetchone()
            conn.commit()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(_profile_row_to_json(row))
    except Exception as e:
        log_error("profile", "resume_update_failed", section=section, error=str(e))
        return jsonify({"error": str(e)}), 500


@app.delete("/api/profile/resume/<section>/<item_id>")
def delete_resume_item(section, item_id):
    if section not in RESUME_SECTIONS:
        return jsonify({"error": "unknown section"}), 404
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    if not _valid_uuid(item_id):
        return jsonify({"error": "invalid id"}), 400

    cfg = RESUME_SECTIONS[section]
    table = cfg["table"]
    user_col = cfg.get("user_col", "user_id")
    try:
        with db_cursor() as (conn, cur):
            if table == "portfolio_sections":
                cur.execute(f"""
                    DELETE FROM {table} WHERE id = %s AND {user_col} = %s
                    RETURNING id
                """, (item_id, actor_id))
            elif table == "creative_works":
                cur.execute(f"""
                    UPDATE {table} SET is_deleted = TRUE, deleted_at = NOW()
                    WHERE id = %s AND {user_col} = %s AND (is_deleted IS NOT TRUE)
                    RETURNING id
                """, (item_id, actor_id))
            else:
                cur.execute(f"""
                    UPDATE {table} SET deleted_at = NOW()
                    WHERE id = %s AND {user_col} = %s AND deleted_at IS NULL
                    RETURNING id
                """, (item_id, actor_id))
            row = cur.fetchone()
            conn.commit()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"status": "deleted", "id": item_id})
    except Exception as e:
        log_error("profile", "resume_delete_failed", section=section, error=str(e))
        return jsonify({"error": str(e)}), 500


# ── Core Identity: user_profiles (single row per user, upsert on save) ───────
USER_PROFILE_WRITABLE = [
    "first_legal_name", "middle_legal_name", "last_legal_name", "legal_full_name",
    "display_name", "title", "gender", "date_of_birth", "artistic_slug",
    "country_code", "province_code", "recovery_email", "primary_phone",
    "secondary_phone", "contact_preference",
    "postal_address_line1", "postal_address_line2", "postal_city",
    "postal_province", "postal_country", "postal_postal_code",
    "physical_address_line1", "physical_address_line2", "physical_city",
    "physical_province", "physical_country", "physical_postal_code",
    "artistic_name", "banner_photo_url", "profile_photo_url", "biography",
    "website_urls", "social_links", "country_of_residence", "province", "city",
    "nationality_code", "show_location", "show_country", "show_city",
    "searchable", "profile_language_preference", "profile_timezone_preference",
    "profile_visibility", "user_notifications_preference", "marketing_opt_in",
    "onboarding_step", "onboarding_completed", "user_invitations_preference",
]


@app.get("/api/profile/me")
def get_profile_me():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
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
        """, (actor_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"exists": False, "id": actor_id})
    return jsonify({"exists": True, **_profile_row_to_json(row)})


@app.patch("/api/profile/me")
def update_profile_me():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401

    body = request.get_json(force=True) or {}
    cols, vals = [], []
    for c in USER_PROFILE_WRITABLE:
        if c in body:
            v = body[c]
            cols.append(c)
            vals.append(Json(v) if isinstance(v, (dict, list)) else v)
    if not cols:
        return jsonify({"error": "no writable fields provided"}), 400

    insert_cols = ["id"] + cols
    insert_vals = [actor_id] + vals
    insert_placeholders = ", ".join(["%s"] * len(insert_vals))
    update_sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    try:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute(f"""
                INSERT INTO user_profiles ({", ".join(insert_cols)}, created_at, updated_at)
                VALUES ({insert_placeholders}, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET {update_sets}, updated_at = NOW()
                RETURNING *
            """, insert_vals)
            row = cur.fetchone()
            conn.commit()
        return jsonify(_profile_row_to_json(row))
    except Exception as e:
        log_error("profile", "update_me_failed", error=str(e))
        return jsonify({"error": str(e)}), 500


# ── Core Identity: user_preferences (single row per user) ────────────────────
USER_PREF_WRITABLE = ["theme", "language", "timezone", "dashboard_layout",
                       "notification_preferences", "privacy_preferences",
                       "ai_preferences", "accessibility_preferences", "metadata"]


@app.get("/api/profile/preferences")
def get_profile_preferences():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("SELECT * FROM user_preferences WHERE user_id = %s", (actor_id,))
        row = cur.fetchone()
    return jsonify(_profile_row_to_json(row) or {})


@app.patch("/api/profile/preferences")
def update_profile_preferences():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401

    body = request.get_json(force=True) or {}
    cols, vals = [], []
    for c in USER_PREF_WRITABLE:
        if c in body:
            v = body[c]
            cols.append(c)
            vals.append(Json(v) if isinstance(v, (dict, list)) else v)
    if not cols:
        return jsonify({"error": "no writable fields provided"}), 400

    insert_cols = ["user_id"] + cols
    insert_vals = [actor_id] + vals
    insert_placeholders = ", ".join(["%s"] * len(insert_vals))
    update_sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    try:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute(f"""
                INSERT INTO user_preferences ({", ".join(insert_cols)}, created_at, updated_at)
                VALUES ({insert_placeholders}, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE SET {update_sets}, updated_at = NOW()
                RETURNING *
            """, insert_vals)
            row = cur.fetchone()
            conn.commit()
        return jsonify(_profile_row_to_json(row))
    except Exception as e:
        log_error("profile", "update_prefs_failed", error=str(e))
        return jsonify({"error": str(e)}), 500


# ── Core Identity: read-only (roles, billing, badges) ─────────────────────────
@app.get("/api/profile/roles")
def get_profile_roles():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT id, role, assigned_at, active, expires_at
            FROM user_roles WHERE user_id = %s ORDER BY assigned_at
        """, (actor_id,))
        rows = cur.fetchall()
    return jsonify({"roles": [_profile_row_to_json(r) for r in rows]})


@app.get("/api/profile/billing")
def get_profile_billing():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT b.*, mp.name AS plan_name, mp.plan_code, mp.monthly_credits,
                   mp.priority_processing, mp.coownership, mp.pdf_certificate,
                   mp.verification_level
            FROM user_membership_billings b
            LEFT JOIN membership_plans mp ON mp.id = b.plan_id
            WHERE b.user_id = %s
            ORDER BY b.created_at DESC LIMIT 1
        """, (actor_id,))
        row = cur.fetchone()
    return jsonify(_profile_row_to_json(row) or {})


@app.get("/api/profile/badges")
def get_profile_badges():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("""
            SELECT * FROM user_badges
            WHERE user_id = %s AND deleted_at IS NULL
            ORDER BY awarded_at DESC
        """, (actor_id,))
        rows = cur.fetchall()
    return jsonify({"badges": [_profile_row_to_json(r) for r in rows]})


# ── Reference lookups (public, no auth) ───────────────────────────────────────
@app.get("/api/profile/reference")
def get_profile_reference():
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("SELECT * FROM user_types WHERE is_active IS NOT FALSE ORDER BY sort_order")
        user_types = cur.fetchall()
        cur.execute("SELECT * FROM verification_statuses WHERE is_active IS NOT FALSE ORDER BY priority")
        verification_statuses = cur.fetchall()
        cur.execute("SELECT * FROM membership_plans WHERE active IS NOT FALSE ORDER BY sort_order")
        membership_plans = cur.fetchall()
        cur.execute("SELECT * FROM creative_roles WHERE active IS NOT FALSE ORDER BY display_order")
        creative_roles = cur.fetchall()
    return jsonify({
        "user_types":             [_profile_row_to_json(r) for r in user_types],
        "verification_statuses":  [_profile_row_to_json(r) for r in verification_statuses],
        "membership_plans":       [_profile_row_to_json(r) for r in membership_plans],
        "creative_roles":         [_profile_row_to_json(r) for r in creative_roles],
    })


# ── Portfolio: aggregate + portfolio_items (owned indirectly via section) ─────
@app.get("/api/profile/portfolio")
def get_profile_portfolio():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    with db_cursor(RealDictCursor) as (conn, cur):
        cur.execute("SELECT * FROM portfolio_sections WHERE user_id = %s ORDER BY display_order",
                    (actor_id,))
        sections = cur.fetchall()
        cur.execute("""
            SELECT * FROM creative_works
            WHERE creator_id = %s AND is_deleted IS NOT TRUE
            ORDER BY created_at DESC
        """, (actor_id,))
        works = cur.fetchall()
        cur.execute("""
            SELECT pi.* FROM portfolio_items pi
            JOIN portfolio_sections ps ON ps.id = pi.section_id
            WHERE ps.user_id = %s
            ORDER BY pi.display_order
        """, (actor_id,))
        items = cur.fetchall()
    return jsonify({
        "sections": [_profile_row_to_json(r) for r in sections],
        "works":    [_profile_row_to_json(r) for r in works],
        "items":    [_profile_row_to_json(r) for r in items],
    })


@app.post("/api/profile/portfolio/items")
def create_portfolio_item():
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    body = request.get_json(force=True) or {}
    section_id = body.get("section_id")
    creative_work_id = body.get("creative_work_id")
    if not (_valid_uuid(section_id) and _valid_uuid(creative_work_id)):
        return jsonify({"error": "valid section_id and creative_work_id required"}), 400
    try:
        with db_cursor(RealDictCursor) as (conn, cur):
            cur.execute("SELECT id FROM portfolio_sections WHERE id = %s AND user_id = %s",
                        (section_id, actor_id))
            if not cur.fetchone():
                return jsonify({"error": "section not found or not owned by you"}), 404
            cur.execute("SELECT id FROM creative_works WHERE id = %s AND creator_id = %s",
                        (creative_work_id, actor_id))
            if not cur.fetchone():
                return jsonify({"error": "creative work not found or not owned by you"}), 404
            cur.execute("""
                INSERT INTO portfolio_items
                    (section_id, creative_work_id, display_order, is_featured, is_hidden,
                     added_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING *
            """, (section_id, creative_work_id,
                  body.get("display_order", 0), body.get("is_featured", False),
                  body.get("is_hidden", False)))
            row = cur.fetchone()
            conn.commit()
        return jsonify(_profile_row_to_json(row)), 201
    except Exception as e:
        log_error("profile", "portfolio_item_create_failed", error=str(e))
        return jsonify({"error": str(e)}), 500


@app.delete("/api/profile/portfolio/items/<item_id>")
def delete_portfolio_item(item_id):
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401
    if not _valid_uuid(item_id):
        return jsonify({"error": "invalid id"}), 400
    try:
        with db_cursor() as (conn, cur):
            cur.execute("""
                DELETE FROM portfolio_items pi
                USING portfolio_sections ps
                WHERE pi.id = %s AND pi.section_id = ps.id AND ps.user_id = %s
                RETURNING pi.id
            """, (item_id, actor_id))
            row = cur.fetchone()
            conn.commit()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"status": "deleted", "id": item_id})
    except Exception as e:
        log_error("profile", "portfolio_item_delete_failed", error=str(e))
        return jsonify({"error": str(e)}), 500


# ── The big one: single-call aggregate for profile.html ──────────────────────
@app.get("/api/profile/full")
def get_profile_full():
    """One round trip for the whole profile page — Core + Reference-joined +
    Resume (10 sections) + Portfolio summary. Replaces N sequential fetches."""
    claims, err = _require_auth(request)
    if err:
        return err
    actor_id = _profile_actor_id(claims)
    if not actor_id:
        return jsonify({"error": "Invalid session"}), 401

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
        """, (actor_id,))
        profile = cur.fetchone()

        if not profile:
            return jsonify({"exists": False, "id": actor_id})

        cur.execute("""
            SELECT b.*, mp.name AS plan_name, mp.plan_code
            FROM user_membership_billings b
            LEFT JOIN membership_plans mp ON mp.id = b.plan_id
            WHERE b.user_id = %s ORDER BY b.created_at DESC LIMIT 1
        """, (actor_id,))
        billing = cur.fetchone()

        cur.execute("""
            SELECT ucr.id, ucr.featured, ucr.display_order,
                   cr.code, cr.name, cr.category, cr.icon, cr.color
            FROM user_creative_roles ucr
            JOIN creative_roles cr ON cr.id = ucr.creative_role_id
            WHERE ucr.user_id = %s AND ucr.deleted_at IS NULL
            ORDER BY ucr.display_order
        """, (actor_id,))
        creative_roles = cur.fetchall()

        resume_data = {}
        for slug, cfg in RESUME_SECTIONS.items():
            if slug in ("portfolio-sections", "creative-works", "creative-roles"):
                continue
            table = cfg["table"]
            cur.execute(f"""
                SELECT * FROM {table}
                WHERE user_id = %s AND deleted_at IS NULL
                ORDER BY COALESCE(display_order, 0), created_at
            """, (actor_id,))
            resume_data[slug] = cur.fetchall()

        cur.execute("SELECT * FROM portfolio_sections WHERE user_id = %s ORDER BY display_order",
                    (actor_id,))
        portfolio_sections = cur.fetchall()
        cur.execute("""
            SELECT * FROM creative_works WHERE creator_id = %s AND is_deleted IS NOT TRUE
            ORDER BY created_at DESC LIMIT 100
        """, (actor_id,))
        creative_works = cur.fetchall()

    return jsonify({
        "exists":  True,
        "profile": _profile_row_to_json(profile),
        "billing": _profile_row_to_json(billing) if billing else None,
        "creative_roles":  [_profile_row_to_json(r) for r in creative_roles],
        "identifiers":     [_profile_row_to_json(r) for r in resume_data.get("identifiers", [])],
        "linked_accounts": [_profile_row_to_json(r) for r in resume_data.get("linked-accounts", [])],
        "languages":       [_profile_row_to_json(r) for r in resume_data.get("languages", [])],
        "skills":          [_profile_row_to_json(r) for r in resume_data.get("skills", [])],
        "certifications":  [_profile_row_to_json(r) for r in resume_data.get("certifications", [])],
        "publications":    [_profile_row_to_json(r) for r in resume_data.get("publications", [])],
        "projects":        [_profile_row_to_json(r) for r in resume_data.get("projects", [])],
        "education":       [_profile_row_to_json(r) for r in resume_data.get("education", [])],
        "employment":      [_profile_row_to_json(r) for r in resume_data.get("employment", [])],
        "achievements":    [_profile_row_to_json(r) for r in resume_data.get("achievements", [])],
        "portfolio": {
            "sections": [_profile_row_to_json(r) for r in portfolio_sections],
            "works":    [_profile_row_to_json(r) for r in creative_works],
        },
    })
'''


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from ~/SeekReap-Tier-4-Orchestrator")
        sys.exit(1)

    original = TARGET.read_text()

    if MARKER in original:
        print("Profile Domain API already installed (marker found). Nothing to do.")
        print("If you want to reinstall, restore a backup first.")
        sys.exit(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET.with_name(f"tier4_main.py.bak_{ts}")
    shutil.copy2(TARGET, backup_path)
    print(f"Backed up original to {backup_path}")

    new_content = original + PROFILE_API_CODE
    TARGET.write_text(new_content)
    print(f"Appended Profile Domain API to {TARGET} ({len(PROFILE_API_CODE)} bytes added)")

    print("Validating syntax with py_compile...")
    result = subprocess.run([sys.executable, "-m", "py_compile", str(TARGET)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print("SYNTAX ERROR — restoring backup.")
        print(result.stderr)
        shutil.copy2(backup_path, TARGET)
        sys.exit(1)

    print("✅ Syntax OK. Profile Domain API installed successfully.")
    print()
    print("New endpoints:")
    print("  GET/PATCH  /api/profile/me")
    print("  GET/PATCH  /api/profile/preferences")
    print("  GET        /api/profile/roles")
    print("  GET        /api/profile/billing")
    print("  GET        /api/profile/badges")
    print("  GET        /api/profile/reference        (public)")
    print("  GET        /api/profile/full              <- single-call aggregate")
    print("  GET        /api/profile/portfolio")
    print("  POST       /api/profile/portfolio/items")
    print("  DELETE     /api/profile/portfolio/items/<id>")
    print("  GET/POST   /api/profile/resume/<section>")
    print("  PUT/PATCH  /api/profile/resume/<section>/<id>")
    print("  DELETE     /api/profile/resume/<section>/<id>")
    print("  sections: identifiers, linked-accounts, creative-roles, languages,")
    print("            skills, certifications, publications, projects, education,")
    print("            employment, achievements, portfolio-sections, creative-works")
    print()
    print("Next: git add -A && git commit -m 'Add Profile Domain API' && git push")
    print("Then: fly deploy -a seekreap-tier-4-dev  (or your Fly app name)")


if __name__ == "__main__":
    main()
