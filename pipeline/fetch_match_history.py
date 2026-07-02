"""
fetch_match_history.py
Pulls full match history (summary-level, not fully parsed) for a given
Dota 2 account_id from the OpenDota API.
"""

import requests
import time
import json
from pathlib import Path

OPENDOTA_BASE = "https://api.opendota.com/api"
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "raw_matches"


def fetch_match_history(account_id: int, limit: int = 500) -> list:
    """
    Fetch match history summaries for one account.
    Each entry includes match_id, hero_id, kills/deaths/assists, gpm, xpm,
    duration, win/loss, and start_time — enough for trajectory features.
    `limit` caps how many recent matches come back (OpenDota default is all
    available, which can be huge for high-hour accounts — 500 is a sane cap).
    """
    url = f"{OPENDOTA_BASE}/players/{account_id}/matches"
    params = {"limit": limit}
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def save_raw(account_id: int, data: list):
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DATA_DIR / f"{account_id}_matches.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved: {out_path} ({len(data)} matches)")


def fetch_and_save_history(account_id: int, limit: int = 500):
    print(f"Fetching match history for account_id={account_id}...")
    matches = fetch_match_history(account_id, limit=limit)
    save_raw(account_id, matches)
    return matches


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951  # test account_id, confirmed working

    matches = fetch_and_save_history(TEST_ACCOUNT_ID)

    print("\n--- Quick sanity check ---")
    print(f"Total matches pulled: {len(matches)}")
    if matches:
        first = matches[0]
        is_radiant = first.get("player_slot", 0) < 128
        won = is_radiant == first.get("radiant_win")
        print("Most recent match sample:", {
            "match_id": first.get("match_id"),
            "hero_id": first.get("hero_id"),
            "kills": first.get("kills"),
            "deaths": first.get("deaths"),
            "assists": first.get("assists"),
            "duration_sec": first.get("duration"),
            "win": won,
        })