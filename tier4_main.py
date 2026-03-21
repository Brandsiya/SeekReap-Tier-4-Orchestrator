# Build cache bust: 2026-03-16T21:44:18.528814
from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid, os, json, subprocess, psycopg2, re, requests
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

def get_db():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def normalize_youtube_url(url):
    """Convert youtu.be and shorts URLs to standard watch URLs."""
    if not url:
        return url
    # youtu.be/VIDEO_ID
    m = re.match(r'https?://youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    # youtube.com/shorts/VIDEO_ID
    m = re.match(r'https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    return url

def extract_youtube_metadata(url):
    """Fetch YouTube metadata via oEmbed (no API key needed)."""
    url = normalize_youtube_url(url)
    if not url or 'youtube' not in url:
        return {}
    try:
        m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
        if not m:
            return {}
        video_id = m.group(1)
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(oembed_url, timeout=10)
        if resp.status_code != 200:
            print(f"oEmbed status: {resp.status_code}")
            return {}
        data = resp.json()
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        return {
            'title': data.get('title', ''),
            'channel': data.get('author_name', ''),
            'duration': None,
            'upload_date': '',
            'thumbnail_url': thumbnail_url,
            'youtube_id': video_id,
            'description': '',
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
    submission_id = str(uuid.uuid4())
    content_url = data.get("content_url")
    content_hash = data.get("content_hash", "unknown")
    content_type = data.get("content_type", "video")

    # Extract YouTube metadata
    yt_meta = extract_youtube_metadata(content_url)
    title = yt_meta.get("title") or data.get("title") or content_hash
    channel = yt_meta.get("channel", "")
    thumbnail_url = yt_meta.get("thumbnail_url", "")
    metadata = {**yt_meta, **(data.get("metadata") or {})}

    conn = get_db()
    cur = conn.cursor()
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
        print(f"Created submission {submission_id} title={title!r}")
        cur2 = conn.cursor()
        cur2.execute("INSERT INTO job_queue (submission_id, creator_id, content_id, job_type, status, attempts) VALUES (%s, %s, %s, %s, %s, 0) ON CONFLICT DO NOTHING", (submission_id, creator_uuid, content_url, "fingerprint", "pending"))
        conn.commit()
        cur2.close()
    except Exception as e:
        conn.rollback()
        print(f"DB insert error: {e}")
        raise
    finally:
        cur.close()
        conn.close()
    return submission_id, title, channel, thumbnail_url

@app.post("/api/submit")
def submit():
    try:
        data = request.get_json()
        firebase_uid = data.get("creator_id", "")
        conn = get_db()
        try:
            creator_uuid = get_or_create_creator(conn, firebase_uid,
                                                  data.get("email"), data.get("name"))
        finally:
            conn.close()

        submission_id, title, channel, thumbnail_url = insert_submission(data, creator_uuid)
        return jsonify({
            "submission_id": submission_id,
            "status": "pending",
            "creator_uuid": creator_uuid,
            "title": title,
            "channel": channel,
            "thumbnail_url": thumbnail_url,
        })
    except Exception as e:
        print(f"Submit error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/finalize")
def finalize():
    data = request.get_json()
    submission_id = data["submission_id"]
    analysis = data["analysis"]
    conn = get_db()
    cur = conn.cursor()
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

@app.get("/api/status/<submission_id>")
def status(submission_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
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

        # Fetch matches
        cur.execute("""
            SELECT cm.matched_submission_id, cm.similarity_score,
                   cm.match_type, cm.fingerprint_version, cm.detected_at,
                   ms.title as matched_title, ms.content_url as matched_url
            FROM content_matches cm
            JOIN submissions ms ON ms.id = cm.matched_submission_id
            WHERE cm.submission_id = %s
            ORDER BY cm.similarity_score DESC
            LIMIT 10
        """, (submission_id,))
        matches = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    result = dict(row)
    # Flatten metadata fields to top level for easy frontend access
    meta = result.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    result["yt_title"] = meta.get("title", "") or result.get("title", "")
    result["yt_channel"] = meta.get("channel", "")
    result["yt_duration"] = meta.get("duration")
    result["yt_upload_date"] = meta.get("upload_date", "")
    result["yt_thumbnail"] = meta.get("thumbnail_url", "") or result.get("content_preview_url", "")
    result["yt_id"] = meta.get("youtube_id", "")

    # Add match data
    result["matches"] = [
        {
            "matched_submission_id": str(m["matched_submission_id"]),
            "similarity_score": float(m["similarity_score"]),
            "match_type": m["match_type"],
            "fingerprint_version": m["fingerprint_version"],
            "detected_at": m["detected_at"].isoformat() if m["detected_at"] else None,
            "matched_title": m["matched_title"] or "",
            "matched_url": m["matched_url"] or "",
        }
        for m in matches
    ]
    result["has_match"] = len(result["matches"]) > 0
    return jsonify(result)

@app.get("/health")
def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "db": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)

@app.get("/debug/env")
def debug_env():
    return {
        "has_youtube_key": bool(os.environ.get("YOUTUBE_API_KEY")),
        "has_db": bool(os.environ.get("DATABASE_URL")),
        "key_prefix": os.environ.get("YOUTUBE_API_KEY", "")[:8]
    }
