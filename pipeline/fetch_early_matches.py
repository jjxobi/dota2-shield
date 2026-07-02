"""
fetch_early_matches.py
Pulls fully parsed match detail (GPM, XPM, last hits, denies, items) for a
specific list of match_ids — used to build trajectory features from an
account's earliest games.

OpenDota rate limit note: this hits one request per match, so for 50 matches
we sleep between calls to stay well within the free anonymous tier.
"""

import requests
import time
import json
from pathlib import Path

OPENDOTA_BASE = "https://api.opendota.com/api"
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "raw_matches" / "parsed"
MATCH_HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "raw_matches"

SLEEP_BETWEEN_CALLS = 1.2  # seconds — polite pacing for the free anonymous tier


def load_match_ids_for_account(account_id: int, n_earliest: int = 50) -> list:
    """
    Load the account's already-fetched match history and return the
    n_earliest match_ids (oldest first) — these are what trajectory
    features get built from.
    """
    history_path = MATCH_HISTORY_DIR / f"{account_id}_matches.json"
    with open(history_path, "r", encoding="utf-8") as f:
        matches = json.load(f)

    # OpenDota returns most-recent-first; sort ascending by start_time to get earliest games
    matches_sorted = sorted(matches, key=lambda m: m.get("start_time", 0))
    earliest = matches_sorted[:n_earliest]
    return [m["match_id"] for m in earliest]


def fetch_parsed_match(match_id: int) -> dict:
    url = f"{OPENDOTA_BASE}/matches/{match_id}"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def save_parsed_match(match_id: int, data: dict):
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DATA_DIR / f"{match_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def fetch_early_matches_for_account(account_id: int, n_earliest: int = 50):
    match_ids = load_match_ids_for_account(account_id, n_earliest)
    print(f"Fetching {len(match_ids)} earliest parsed matches for account_id={account_id}...")

    results = []
    for i, match_id in enumerate(match_ids, start=1):
        try:
            data = fetch_parsed_match(match_id)
            save_parsed_match(match_id, data)
            results.append(data)
            print(f"  [{i}/{len(match_ids)}] match_id={match_id} saved")
        except requests.exceptions.HTTPError as e:
            print(f"  [{i}/{len(match_ids)}] match_id={match_id} FAILED: {e}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    return results


def find_player_in_match(match_data: dict, account_id: int) -> dict:
    """
    Given a fully parsed match, return just the player-record dict
    matching this account_id. Returns None if not found (can happen
    if the player had a private profile in that specific match).
    """
    for player in match_data.get("players", []):
        if player.get("account_id") == account_id:
            return player
    return None


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    parsed = fetch_early_matches_for_account(TEST_ACCOUNT_ID, n_earliest=50)  

    print("\n--- Quick sanity check ---")
    found_count = 0
    for match_data in parsed:
        player = find_player_in_match(match_data, TEST_ACCOUNT_ID)
        if player:
            found_count += 1
            print({
                "match_id": match_data.get("match_id"),
                "hero_id": player.get("hero_id"),
                "gpm": player.get("gold_per_min"),
                "xpm": player.get("xp_per_min"),
                "last_hits": player.get("last_hits"),
                "denies": player.get("denies"),
            })
        else:
            print(f"  WARNING: account_id not found in match {match_data.get('match_id')} (likely private profile in that match)")

    print(f"\nFound player record in {found_count}/{len(parsed)} matches")