import re

with open('tier4_main.py', 'r') as f:
    content = f.read()

# Add get_submission function after the other helper functions
get_submission_function = '''
def get_submission(cur, submission_id):
    """Helper to fetch submission data"""
    cur.execute("""
        SELECT id, creator_id, content_url, content_hash, submitted_at, status
        FROM submissions
        WHERE id = %s
    """, (submission_id,))
    row = cur.fetchone()
    if row:
        return dict(row)
    return None
'''

# Find where to insert (after other helper functions, before routes)
# Look for the first route definition
insert_pos = content.find('@app.get("/health")')
if insert_pos == -1:
    insert_pos = content.find('@app.get("/api/status"')

# Insert get_submission function
if insert_pos != -1:
    content = content[:insert_pos] + get_submission_function + '\n\n' + content[insert_pos:]

# Now fix the verify endpoint to always return JSON
fixed_verify = '''
@app.get("/verify/<submission_id>")
def verify(submission_id):
    """Verify a submission by ID"""
    # Validate UUID format first
    try:
        uuid.UUID(submission_id)
    except ValueError:
        return jsonify({"verified": False, "error": "Invalid submission ID format"}), 400
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        row = get_submission(cur, submission_id)
        
        if not row:
            return jsonify({"verified": False, "error": "Submission not found"}), 404
        
        return jsonify({
            "verified": True,
            "submission_id": submission_id,
            "creator_id": row["creator_id"],
            "content_url": row["content_url"],
            "content_hash": row["content_hash"],
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
            "status": row.get("status", "unknown")
        })
    finally:
        cur.close()
        conn.close()
'''

# Replace the existing verify endpoint (find and replace)
import re
pattern = r'@app\.get\("/verify/<submission_id>"\).*?(?=@app\.get\("/api/qrcode|if __name__|$\))'
content = re.sub(pattern, fixed_verify, content, flags=re.DOTALL)

with open('tier4_main.py', 'w') as f:
    f.write(content)

print("✅ Fixed /verify endpoint with proper JSON responses")
