import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv('DATABASE_URL')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "tier": 4}), 200

@app.route('/api/job-update', methods=['POST'])
def job_update():
    data = request.json
    job_id = data.get('job_id')
    status = data.get('status')
    
    if not job_id or not status:
        return jsonify({"error": "Missing job_id or status"}), 400
        
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "UPDATE submissions SET status = %s, completed_at = NOW() WHERE job_id = %s",
            (status, job_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Updated Job {job_id} to {status}")
        return jsonify({"message": "Job updated successfully"}), 200
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
