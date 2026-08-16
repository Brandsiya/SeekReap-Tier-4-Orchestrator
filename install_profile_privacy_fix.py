#!/usr/bin/env python3
"""
install_profile_privacy_fix.py

GET /api/profile/<profile_id> currently returns legal_full_name, title,
gender, and date_of_birth to ANY viewer, including anonymous requests.
There's no visibility toggle for these (unlike location, which has
show_country/show_city/show_location). This patch makes them owner-only.

Usage:
    cd ~/SeekReap-Tier-4-Orchestrator
    python3 install_profile_privacy_fix.py
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("tier4_main.py")
MARKER = "OWNER_ONLY_PROFILE_FIELDS"

OLD_FUNC = '''def _sanitize_public_profile(row):
    p = _profile_row_to_json(row)
    for k in list(p.keys()):
        if k in PUBLIC_PROFILE_EXCLUDE:
            p.pop(k, None)
    # Respect the creator's own visibility toggles for location fields
    if not p.get('show_country'):
        p.pop('country_of_residence', None)
        p.pop('nationality_code', None)
    if not p.get('show_city'):
        p.pop('city', None)
    if not p.get('show_location'):
        p.pop('province', None)
    return p'''

NEW_FUNC = '''# Legal identity fields — no visibility toggle exists for these (unlike
# location), so they are always owner-only regardless of profile_visibility.
OWNER_ONLY_PROFILE_FIELDS = {
    'legal_full_name', 'first_legal_name', 'middle_legal_name', 'last_legal_name',
    'title', 'gender', 'date_of_birth',
}


def _sanitize_public_profile(row, is_owner=False):
    p = _profile_row_to_json(row)
    for k in list(p.keys()):
        if k in PUBLIC_PROFILE_EXCLUDE:
            p.pop(k, None)
    if not is_owner:
        for k in list(p.keys()):
            if k in OWNER_ONLY_PROFILE_FIELDS:
                p.pop(k, None)
    # Respect the creator's own visibility toggles for location fields
    if not p.get('show_country'):
        p.pop('country_of_residence', None)
        p.pop('nationality_code', None)
    if not p.get('show_city'):
        p.pop('city', None)
    if not p.get('show_location'):
        p.pop('province', None)
    return p'''

OLD_CALL = '"profile":        _sanitize_public_profile(profile),'
NEW_CALL = '"profile":        _sanitize_public_profile(profile, is_owner),'


def replace_once(content, old, new, label):
    count = content.count(old)
    if count != 1:
        print(f"❌ FAILED on step '{label}': expected exactly 1 match, found {count}.")
        print("   No changes were made — your file may differ from what this patch expects.")
        sys.exit(1)
    return content.replace(old, new)


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from ~/SeekReap-Tier-4-Orchestrator")
        sys.exit(1)

    original = TARGET.read_text()

    if MARKER in original:
        print("Privacy fix already installed (marker found). Nothing to do.")
        sys.exit(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET.with_name(f"tier4_main.py.bak_{ts}")
    shutil.copy2(TARGET, backup_path)
    print(f"Backed up original to {backup_path}")

    c = original
    c = replace_once(c, OLD_FUNC, NEW_FUNC, "_sanitize_public_profile definition")
    c = replace_once(c, OLD_CALL, NEW_CALL, "_sanitize_public_profile call site")

    TARGET.write_text(c)
    print("Patched: legal_full_name/title/gender/date_of_birth now owner-only")

    print("Validating syntax with py_compile...")
    result = subprocess.run([sys.executable, "-m", "py_compile", str(TARGET)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print("SYNTAX ERROR — restoring backup.")
        print(result.stderr)
        shutil.copy2(backup_path, TARGET)
        sys.exit(1)

    print("✅ Syntax OK. Privacy fix installed successfully.")
    print()
    print("Next: git add -A && git commit -m 'Restrict legal identity fields to owner-only' && git push")


if __name__ == "__main__":
    main()
