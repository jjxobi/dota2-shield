"""
tier1_lookup.py
Fast-path lookup: one API call to fetch full match history, then
win-rate/KDA changepoint detection over full lifetime data. No per-match
deep fetching — this is what makes it fast enough for a live request.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent / "pipeline"))
import store
import api_client


def run_tier1_lookup(con, account_id: int) -> dict:
    profile = api_client.get(f"/players/{account_id}")
    matches = api_client.get(f"/players/{account_id}/matches", params={"lobby_type": 7})

    store.upsert_account(
        con, account_id,
        profile.get("profile", {}).get("personaname"),
        profile.get("rank_tier"), None, None,
    )
    n = store.upsert_match_history(con, account_id, matches)

    # Placeholder for the changepoint detector we haven't built yet —
    # this is the next real piece of work.
    changepoint_result = {"status": "changepoint_detector_not_yet_built", "n_matches": n}

    return {
        "account_id": account_id,
        "personaname": profile.get("profile", {}).get("personaname"),
        "rank_tier": profile.get("rank_tier"),
        "n_ranked_matches": n,
        "discontinuity_signal": changepoint_result,
    }