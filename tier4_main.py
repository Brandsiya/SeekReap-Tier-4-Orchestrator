from flask import Flask, request, jsonify
import uuid
import os
from google.cloud import pubsub_v1
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Initialize Pub/Sub publisher
publisher = pubsub_v1.PublisherClient()
PROJECT_ID = os.environ.get('PROJECT_ID', 'seekreap-production')
topic_path = publisher.topic_path(PROJECT_ID, 'seekreap-jobs')

def get_db():
    return psycopg2.connect(os.environ.get('DATABASE_URL'))

def insert_submission(data):
    submission_id = str(uuid.uuid4())
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO submissions (id, content_hash, content_type, status)
            VALUES (%s, %s, %s, 'QUEUED')
            RETURNING id
        """, (submission_id, data.get('content_hash', 'test'), data.get('content_type', 'unknown')))
        conn.commit()
    except Exception as e:
        print(f"DB insert error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    return submission_id

def publish_job(submission_id):
    try:
        # Publish message to Pub/Sub
        data = submission_id.encode('utf-8')
        future = publisher.publish(topic_path, data)
        message_id = future.result()
        print(f"Published job {submission_id} with message ID {message_id}")
    except Exception as e:
        print(f"Pub/Sub publish error: {e}")

@app.post("/api/submit")
def submit():
    data = request.get_json()
    submission_id = insert_submission(data)
    publish_job(submission_id)
    return jsonify({"submission_id": submission_id, "status": "QUEUED"})

@app.post("/api/finalize")
def finalize():
    data = request.get_json()
    submission_id = data["submission_id"]
    analysis = data["analysis"]

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE submissions
            SET status = 'COMPLETED',
                risk_score = %s,
                risk_level = %s
            WHERE id = %s
            RETURNING id
        """, (analysis.get('risk_score'), analysis.get('risk_level'), submission_id))
        conn.commit()
        updated = cur.fetchone() is not None
    except Exception as e:
        print(f"DB update error: {e}")
        conn.rollback()
        updated = False
    finally:
        cur.close()
        conn.close()

    if updated:
        return jsonify({"status": "updated"})
    else:
        return jsonify({"error": "submission not found"}), 404

@app.get("/api/status/<submission_id>")
def status(submission_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT id, status, risk_score, risk_level FROM submissions WHERE id = %s", (submission_id,))
        result = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    
    if result:
        return jsonify(dict(result))
    return jsonify({"error": "not found"}), 404

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
