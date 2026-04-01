with open('tier4_main.py', 'r') as f:
    content = f.read()

rich_qr_endpoint = '''
@app.get("/api/qrcode-rich/<submission_id>")
def generate_rich_qr_code(submission_id):
    """Generate QR code with embedded submission metadata"""
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

# Insert after the first QR endpoint
if 'def generate_qr_code' in content:
    # Find position after the first QR endpoint
    pos = content.find('def generate_qr_code')
    pos = content.find('@app.get("/api/qrcode/', pos)
    pos = content.find('def generate_rich_qr_code') - 1
    if pos < 0:
        pos = content.rfind('def generate_qr_code')
        pos = content.find('\n\n', pos) + 2
    
    content = content[:pos] + rich_qr_endpoint + content[pos:]
    
    with open('tier4_main.py', 'w') as f:
        f.write(content)
    print("✅ Rich QR code endpoint added")
else:
    print("⚠️ QR endpoint not found, adding at end")
    # Add at the end before main
    with open('tier4_main.py', 'a') as f:
        f.write(rich_qr_endpoint)
