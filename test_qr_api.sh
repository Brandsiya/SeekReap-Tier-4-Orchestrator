#!/bin/bash

SUBMISSION_ID="4965b415-2ed2-4218-b348-e20f0ab69147"

echo "=== Testing QR Code Endpoints ==="
echo ""

echo "1. Testing basic QR code endpoint..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://seekreap-tier-4-dev.fly.dev/api/qrcode/$SUBMISSION_ID")
if [ "$HTTP_CODE" == "200" ]; then
    echo "   ✅ QR code endpoint working (HTTP $HTTP_CODE)"
    curl -s -o test_qr.png "https://seekreap-tier-4-dev.fly.dev/api/qrcode/$SUBMISSION_ID"
    SIZE=$(stat -c%s test_qr.png 2>/dev/null || stat -f%z test_qr.png 2>/dev/null)
    echo "   📁 QR code size: $SIZE bytes"
else
    echo "   ❌ QR code endpoint failed (HTTP $HTTP_CODE)"
fi

echo ""
echo "2. Testing rich QR code endpoint..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://seekreap-tier-4-dev.fly.dev/api/qrcode-rich/$SUBMISSION_ID")
if [ "$HTTP_CODE" == "200" ]; then
    echo "   ✅ Rich QR code endpoint working (HTTP $HTTP_CODE)"
    curl -s -o test_rich_qr.png "https://seekreap-tier-4-dev.fly.dev/api/qrcode-rich/$SUBMISSION_ID"
    SIZE=$(stat -c%s test_rich_qr.png 2>/dev/null || stat -f%z test_rich_qr.png 2>/dev/null)
    echo "   📁 Rich QR code size: $SIZE bytes"
else
    echo "   ❌ Rich QR code endpoint failed (HTTP $HTTP_CODE)"
fi

echo ""
echo "3. Testing invalid submission..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://seekreap-tier-4-dev.fly.dev/api/qrcode/invalid-id-123")
if [ "$HTTP_CODE" == "404" ]; then
    echo "   ✅ Correctly returns 404 for invalid submission"
else
    echo "   ⚠️  Unexpected response: $HTTP_CODE"
fi

echo ""
echo "=== Test Complete ==="
rm -f test_qr.png test_rich_qr.png
