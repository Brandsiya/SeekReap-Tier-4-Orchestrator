# Add QR code endpoint to tier4_main.py
with open('tier4_main.py', 'r') as f:
    lines = f.readlines()

# Find the position to insert (before if __name__)
insert_pos = None
for i, line in enumerate(lines):
    if line.startswith('if __name__ == "__main__":'):
        insert_pos = i
        break

if insert_pos:
    qr_endpoint = '''
@app.get("/api/qrcode/<submission_id>")
def generate_qr_code(submission_id):
    """Generate QR code for submission verification"""
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
    lines.insert(insert_pos, qr_endpoint)
    
    with open('tier4_main.py', 'w') as f:
        f.writelines(lines)
    print("✅ QR code endpoint added successfully")
else:
    print("❌ Could not find insertion point")
