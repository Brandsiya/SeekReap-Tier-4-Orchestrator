#!/usr/bin/env python3
"""
remove_social_links.py

Removes social_links from the backend:
1. Remove from PUBLIC_PROFILE_EXCLUDE allowlist
2. No migration needed (0 records exist)

Usage:
    cd ~/SeekReap-Tier-4-Orchestrator
    python3 remove_social_links.py
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("tier4_main.py")
MARKER = "# ══ SOCIAL_LINKS_REMOVED_V1 ══"

OLD_LINE = '"website_urls", "social_links", "country_of_residence", "province", "city",'
NEW_LINE = '"website_urls", "country_of_residence", "province", "city",'

def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from ~/SeekReap-Tier-4-Orchestrator")
        sys.exit(1)

    original = TARGET.read_text()

    if MARKER in original:
        print("social_links already removed (marker found). Nothing to do.")
        sys.exit(0)

    if original.count(OLD_LINE) != 1:
        found = original.count(OLD_LINE)
        print(f"❌ Could not find the exact line to change (found {found} matches, need exactly 1).")
        print("   The line may have already been changed.")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET.with_name(f"tier4_main.py.bak_{ts}")
    shutil.copy2(TARGET, backup_path)
    print(f"Backed up original to {backup_path}")

    patched = original.replace(OLD_LINE, NEW_LINE)
    TARGET.write_text(patched)
    print("Removed social_links from PUBLIC_PROFILE_EXCLUDE allowlist")

    print("Validating syntax with py_compile...")
    result = subprocess.run([sys.executable, "-m", "py_compile", str(TARGET)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print("SYNTAX ERROR — restoring backup.")
        print(result.stderr)
        shutil.copy2(backup_path, TARGET)
        sys.exit(1)

    print("✅ Syntax OK. social_links removed from code.")

    # Add marker to prevent re-running
    with open(TARGET, 'a') as f:
        f.write(f"\n# {MARKER}\n")

    print()
    print("Next: git add -A && git commit -m 'Remove social_links from backend allowlist' && git push")
    print()
    print("After deployment, run:")
    print("  ALTER TABLE public.user_profiles DROP COLUMN IF EXISTS social_links;")

if __name__ == "__main__":
    main()
