"""
find_smurf_candidates.py
Rough, weak-label smurf candidate scan: takes the pool of account_ids
seen in already-cached matches, fetches each one's profile + lifetime
win/loss totals, and flags accounts with a high current rank_tier but
an unusually low total game count.

This is a crude proxy for the brief's "Source 1 — rank jump detection"
(which properly requires calibration-MMR-vs-current-MMR over a time
window, not available directly from OpenDota) — good enough to surface
a plausible test candidate, not rigorous enough to be a real label
source or to accuse any specific account of anything.
"""

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "pipeline"))
import store
import api_client
from fetch_accounts import fetch_and_store_profile


def find_candidate_account_ids(con, exclude_account_id: int = None) -> list:
    match_ids = [row[0] for row in con.execute("SELECT match_id FROM raw_match_cache").fetchall()]

    seen = set()
    for match_id in match_ids:
        match_data = store.get_cached_match(con, match_id)
        for p in match_data.get("players", []):
            acc_id = p.get("account_id")
            if acc_id is not None and acc_id != exclude_account_id:
                seen.add(acc_id)

    return list(seen)


def scan_for_smurf_candidates(con, account_ids: list):
    results = []

    for i, acc_id in enumerate(account_ids, start=1):
        try:
            fetch_and_store_profile(con, acc_id)
        except Exception as e:
            print(f"  [{i}/{len(account_ids)}] {acc_id}: profile fetch failed ({e}), skipping")
            continue

        row = con.execute("""
            SELECT rank_tier, wins, losses, personaname
            FROM accounts WHERE account_id = ?
        """, [acc_id]).fetchone()

        if row is None:
            continue

        rank_tier, wins, losses, personaname = row
        total_games = (wins or 0) + (losses or 0)

        if rank_tier is None or total_games == 0:
            continue

        # Rough heuristic: rank_tier is encoded as (rank * 10 + star),
        # e.g. 74 = Ancient 4. Rank 60+ (Divine/Immortal territory) with
        # a suspiciously low lifetime game count is our weak-label flag.
        # Threshold chosen loosely for exploration, not calibrated.
        suspicious = rank_tier >= 60 and total_games < 500

        results.append({
            "account_id": acc_id,
            "personaname": personaname,
            "rank_tier": rank_tier,
            "total_games": total_games,
            "flagged": suspicious,
        })

        print(f"  [{i}/{len(account_ids)}] {personaname} (id={acc_id}): "
              f"rank_tier={rank_tier}, total_games={total_games}, flagged={suspicious}")

    return results


if __name__ == "__main__":
    MY_ACCOUNT_ID = 314554951

    con = store.get_connection()
    store.init_schema(con)

    candidates = find_candidate_account_ids(con, exclude_account_id=MY_ACCOUNT_ID)
    print(f"Found {len(candidates)} candidate account_ids from cached matches\n")

    results = scan_for_smurf_candidates(con, candidates)
    con.close()

    flagged = [r for r in results if r["flagged"]]
    print(f"\n--- {len(flagged)} flagged candidates (high rank_tier, low total games) ---")
    for r in sorted(flagged, key=lambda x: -x["rank_tier"]):
        print(r)