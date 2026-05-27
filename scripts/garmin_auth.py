#!/usr/bin/env python3
"""
Garmin Connect Authentication — First-time Setup
Run this on Max to generate OAuth tokens for the garmin-direct-sync container.

Usage:
  python garmin_auth.py

After running:
  Copy garmin_tokens.json to \\nas\Container\garmin-direct-sync\tokens\

Re-run whenever tokens expire (typically every few months).

Requirements:
  pip install garminconnect
"""

import os
import json
from garminconnect import Garmin

# ── Configuration ──────────────────────────────────────────────────────────────

EMAIL    = os.environ.get("GARMIN_EMAIL", "")
PASSWORD = os.environ.get("GARMIN_PASS", "")

TOKEN_DIR  = os.path.join(os.path.dirname(__file__), "garmin_tokens")
TOKEN_FILE = os.path.join(TOKEN_DIR, "garmin_tokens.json")

# ── Auth ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Garmin Connect Authentication")
    print("=" * 60)

    if not EMAIL:
        email = input("Garmin email: ").strip()
    else:
        email = EMAIL
        print(f"Using email from GARMIN_EMAIL env var: {email}")

    if not PASSWORD:
        import getpass
        password = getpass.getpass("Garmin password: ")
    else:
        password = PASSWORD
        print("Using password from GARMIN_PASS env var")

    print("\nAuthenticating with Garmin Connect...")
    print("You may be prompted for a 2FA code.\n")

    client = Garmin(email, password)
    client.login()

    os.makedirs(TOKEN_DIR, exist_ok=True)
    client.client.dump(TOKEN_FILE)

    print(f"\nTokens saved to: {TOKEN_FILE}")
    print("\nNext step: copy the tokens folder to the NAS:")
    print(r"  \\nas\Container\garmin-direct-sync\tokens\garmin_tokens.json")
    print("\nVerify the sync is working by checking Container Station logs")
    print("for garmin-direct-sync after restarting the container.")


if __name__ == "__main__":
    main()
