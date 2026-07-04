"""
tier1_lookup.py
Fast-path lookup: fetches full match history (one API call) and runs
win-rate/KDA change-point detection over full lifetime data. No per-match
deep fetching — this is what keeps it fast enough for a live request
(seconds, not minutes).
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "pipeline"))
sys.path.append(str(Path(__file__).resolve().parent.parent / "features" / "discontinuity"))
import store
import api_client
from changepoint_detection import analyze_account_discontinuity


def run_tier1_lookup(con, account_id: int) -> dict:
    profile = api_client.get(f"/players/{account_id}")
    matches = api_client.get(f"/players/{account_id}/matches", params={"lobby_type": 7})

    store.upsert_account(
        con, account_id,
        profile.get("profile", {}).get("personaname"),
        profile.get("rank_tier"), None, None,
    )
    store.upsert_match_history(con, account_id, matches)

    discontinuity_result = analyze_account_discontinuity(con, account_id)

    return {
        "account_id": account_id,
        "personaname": profile.get("profile", {}).get("personaname"),
        "rank_tier": profile.get("rank_tier"),
        "n_ranked_matches": discontinuity_result.get("n_matches"),
        "discontinuity_signal": discontinuity_result,
    }