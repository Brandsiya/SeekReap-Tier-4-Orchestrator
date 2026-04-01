import re

with open('tier4_main.py', 'r') as f:
    content = f.read()

# Fixed QR endpoint with proper UUID validation
fixed_basic_qr = '''@app.get("/api/qrcode/<submission_id>")
def generate_qr_code(submission_id):
    """Generate QR code for submission verification"""
    # Validate UUID format first (before database query)
    try:
        uuid.UUID(submission_id)
    except ValueError:
        return jsonify({"error": "Invalid submission ID format"}), 400
    
    try:
        from flask import send_file

        # Get submission details
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT id, creator_id, content_hash, submitted_at, status
            FROM submissions
            WHERE id = %s
        """, (submission_id,))

        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Submission not found"}), 404

        # Create verification URL
        verify_url = f"https://seekreap-tier-4-dev.fly.dev/verify/{submission_id}"

        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(verify_url)
        qr.make(fit=True)

        # Create image
        img = qr.make_image(fill_color="black", back_color="white")

        # Save to bytes buffer
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)

        # Return as image
        return send_file(
            img_buffer,
            mimetype='image/png',
            as_attachment=False,
            download_name=f'qrcode_{submission_id}.png'
        )

    except Exception as e:
        print(f"QR code generation error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
'''

# Fixed Rich QR endpoint with proper UUID validation
fixed_rich_qr = '''@app.get("/api/qrcode-rich/<submission_id>")
def generate_rich_qr_code(submission_id):
    """Generate QR code with embedded submission metadata"""
    # Validate UUID format first (before database query)
    try:
        uuid.UUID(submission_id)
    except ValueError:
        return jsonify({"error": "Invalid submission ID format"}), 400
    
    try:
        from flask import send_file

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT s.id, s.creator_id, s.content_hash, s.submitted_at, s.status,
                   s.overall_risk_score, s.risk_level,
                   c.email as creator_email
            FROM submissions s
            LEFT JOIN creators c ON c.id = s.creator_id
            WHERE s.id = %s
        """, (submission_id,))

        submission = cur.fetchone()
        cur.close()
        conn.close()

        if not submission:
            return jsonify({"error": "Submission not found"}), 404

        # Create rich data payload
        qr_data = {
            "type": "seekreap_verification",
            "version": "1.0",
            "submission_id": submission['id'],
            "verification_url": f"https://seekreap-tier-4-dev.fly.dev/verify/{submission_id}",
            "certificate_url": f"https://seekreap-tier-4-dev.fly.dev/certificate/{submission_id}",
            "submitted_at": submission['submitted_at'].isoformat() if submission['submitted_at'] else None,
            "risk_level": submission['risk_level'] or "pending",
            "status": submission['status'],
            "creator_id": str(submission['creator_id'])[:8] if submission['creator_id'] else "unknown"
        }

        # Add risk score if available
        if submission.get('overall_risk_score'):
            qr_data["risk_score"] = float(submission['overall_risk_score'])

        # Generate QR code with more data capacity
        qr = qrcode.QRCode(
            version=2,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=8,
            border=4,
        )
        qr.add_data(json.dumps(qr_data))
        qr.make(fit=True)

        # Create image
        img = qr.make_image(fill_color="black", back_color="white")

        # Save to bytes buffer
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)

        return send_file(
            img_buffer,
            mimetype='image/png',
            as_attachment=False,
            download_name=f'rich_qrcode_{submission_id}.png'
        )

    except Exception as e:
        print(f"Rich QR code generation error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
'''

# Replace the QR endpoints in the file
import re

# Pattern to match the basic QR endpoint (from start to the next @app or if __name__)
pattern1 = r'@app\.get\("/api/qrcode/<submission_id>"\).*?(?=@app\.get\("/api/qrcode-rich|if __name__)'
pattern2 = r'@app\.get\("/api/qrcode-rich/<submission_id>"\).*?(?=if __name__)'

content = re.sub(pattern1, fixed_basic_qr, content, flags=re.DOTALL)
content = re.sub(pattern2, fixed_rich_qr, content, flags=re.DOTALL)

# Also fix duplicate return statements in other endpoints
content = re.sub(r'return jsonify\([^)]+\), \d+\n\s+return jsonify\([^)]+\), \d+\n', r'return jsonify({"error": "Submission not found"}), 404\n', content)

with open('tier4_main.py', 'w') as f:
    f.write(content)

print("✅ QR endpoints fixed with proper UUID validation")
