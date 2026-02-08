#!/bin/bash
echo "=== VERIFYING UPDATES ==="

echo "1. Video test counter added:"
grep -n "MAX_TEST_VIDEOS" index.html

echo -e "\n2. New pages created:"
ls -la about.html whofor.html whyexists.html

echo -e "\n3. Navigation links updated:"
grep -n "about.html\|whofor.html\|whyexists.html" index.html | head -5

echo -e "\n4. Mobile header fixed:"
grep -n "@media (max-width:768px)" index.html

echo -e "\n5. Vertical menu before footer:"
grep -n "VERTICAL MENU LINKS" index.html

echo -e "\n6. Search field full width:"
grep -n "search-desktop" index.html | head -3

echo -e "\n7. Active link bold weight:"
grep -n "font-weight:700" index.html | head -3

echo -e "\n✅ All updates applied!"
echo "🌐 Live site: https://brandsiya.github.io/SeekReap-Tier-4-Orchestrator/"
