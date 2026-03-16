from flask import Flask, request, jsonify
from flask_cors import CORS
import uuid, os, json, subprocess, psycopg2
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
    import re
    # youtu.be/VIDEO_ID
    m = re.match(r'https?://youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    # youtube.com/shorts/VIDEO_ID
    m = re.match(r'https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})', url)
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    return url

def normalize_youtube_url(url):
    """Convert youtu.be and shorts URLs to standard watch URLs."""
    if not url:
        return url
    import re
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
    """Use yt-dlp --dump-json to get video metadata without downloading."""
    url = normalize_youtube_url(url)
    if not url or "youtube" not in url and "youtu.be" not in url:
        return {}
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", "--skip-download",
             "--no-write-auto-subs", "--no-write-subs", url],
            capture_output=True, text=True, timeout=45
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                "title": data.get("title", ""),
                "channel": data.get("uploader") or data.get("channel", ""),
                "duration": data.get("duration"),
                "upload_date": data.get("upload_date", ""),
                "thumbnail_url": data.get("thumbnail", ""),
                "view_count": data.get("view_count"),
                "youtube_id": data.get("id", ""),
                "description": (data.get("description") or "")[:500],
            }
    except Exception as e:
        print(f"yt-dlp error: {e}")
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
