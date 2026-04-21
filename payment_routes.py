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
