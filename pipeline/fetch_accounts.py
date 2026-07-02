"""
fetch_accounts.py
Pulls core profile data for a given Dota 2 account_id from the OpenDota API.
Free tier, no API key required at low request volume.
"""

import requests
import time
import json
from pathlib import Path

OPENDOTA_BASE = "https://api.opendota.com/api"
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "raw_profiles"


def fetch_profile(account_id: int) -> dict:
    """Fetch basic profile + rank data for one account."""
    url = f"{OPENDOTA_BASE}/players/{account_id}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_win_loss(account_id: int) -> dict:
    """Fetch aggregate win/loss totals for one account."""
    url = f"{OPENDOTA_BASE}/players/{account_id}/wl"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def save_raw(account_id: int, data: dict, suffix: str):
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DATA_DIR / f"{account_id}_{suffix}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved: {out_path}")


def fetch_account_bundle(account_id: int):
    """Fetch and save profile + win/loss for one account, respecting rate limits."""
    print(f"Fetching profile for account_id={account_id}...")
    profile = fetch_profile(account_id)
    save_raw(account_id, profile, "profile")

    time.sleep(1)  # be polite to the free API — no key means shared rate limit

    print(f"Fetching win/loss for account_id={account_id}...")
    wl = fetch_win_loss(account_id)
    save_raw(account_id, wl, "wl")

    return profile, wl


if __name__ == "__main__":
    # --- TEST RUN: replace with a real account_id ---
    TEST_ACCOUNT_ID = 314554951  # test ID

    profile, wl = fetch_account_bundle(TEST_ACCOUNT_ID)

    print("\n--- Quick sanity check ---")
    print("Name:", profile.get("profile", {}).get("personaname"))
    print("Rank tier:", profile.get("rank_tier"))
    print("Win/Loss:", wl)