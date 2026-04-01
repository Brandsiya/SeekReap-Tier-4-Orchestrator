#!/bin/bash

SUBMISSION_ID="4965b415-2ed2-4218-b348-e20f0ab69147"
echo "=== QR Code API Test Suite ==="
echo ""

# Test 1: Basic QR code
echo "1. Basic QR Code:"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://seekreap-tier-4-dev.fly.dev/api/qrcode/$SUBMISSION_ID")
if [ "$HTTP_CODE" == "200" ]; then
    curl -s -o test_qr.png "https://seekreap-tier-4-dev.fly.dev/api/qrcode/$SUBMISSION_ID"
    SIZE=$(stat -c%s test_qr.png 2>/dev/null || stat -f%z test_qr.png 2>/dev/null)
    echo "   ✅ HTTP $HTTP_CODE | Size: $SIZE bytes"
    file test_qr.png
else
    echo "   ❌ Failed: HTTP $HTTP_CODE"
fi

# Test 2: Rich QR code
echo -e "\n2. Rich QR Code:"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://seekreap-tier-4-dev.fly.dev/api/qrcode-rich/$SUBMISSION_ID")
if [ "$HTTP_CODE" == "200" ]; then
    curl -s -o test_rich_qr.png "https://seekreap-tier-4-dev.fly.dev/api/qrcode-rich/$SUBMISSION_ID"
    SIZE=$(stat -c%s test_rich_qr.png 2>/dev/null || stat -f%z test_rich_qr.png 2>/dev/null)
    echo "   ✅ HTTP $HTTP_CODE | Size: $SIZE bytes"
    file test_rich_qr.png
else
    echo "   ❌ Failed: HTTP $HTTP_CODE"
fi

# Test 3: Invalid submission (should be 404)
echo -e "\n3. Invalid Submission:"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://seekreap-tier-4-dev.fly.dev/api/qrcode/invalid-id-123")
if [ "$HTTP_CODE" == "404" ]; then
    echo "   ✅ Correctly returns 404"
else
    echo "   ⚠️  Returns $HTTP_CODE (expected 404)"
fi

# Test 4: Download as attachment
echo -e "\n4. Download as Attachment:"
curl -s -J -O "https://seekreap-tier-4-dev.fly.dev/api/qrcode/$SUBMISSION_ID" 2>&1 | grep -i "filename" || echo "   Check manually"

# Clean up
rm -f test_qr.png test_rich_qr.png
echo -e "\n=== Test Complete ==="
