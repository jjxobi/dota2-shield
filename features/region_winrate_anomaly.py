"""
region_winrate_anomaly.py
Detects accounts with a sharp win-rate disparity across regions.

IMPORTANT SCOPE NOTE: `region` only exists on OpenDota's deep-parsed match
detail endpoint, not the lightweight match list endpoint — confirmed by
direct API inspection. This means region data is only available for
matches already fetched into raw_match_cache (currently: an account's
earliest 50 solo ranked matches), NOT full lifetime history. This feature
is therefore scoped to "did this account switch regions with a win-rate
jump within their earliest games" — a real but narrower question than
originally envisioned. Detecting a LATER region switch (e.g. an
established account suddenly farming a weaker region) would require
deep-parsing additional historical matches beyond the earliest window,
which runs into the same 10-day replay retention ceiling documented
elsewhere in METHODOLOGY.md.
"""

import sys
import json
from pathlib import Path
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent / "pipeline"))
import store

MIN_GAMES_PER_REGION = 5
MIN_WINRATE_GAP = 0.30
MIN_HIGH_WR_THRESHOLD = 0.70


def compute_region_winrate_profile(con, account_id: int) -> pd.DataFrame:
    """
    Builds region/win-rate profile from raw_match_cache, restricted to
    match_ids in this account's account_early_match_selection (the only
    matches we have deep-parsed detail for).
    """
    match_ids = con.execute("""
        SELECT match_id FROM account_early_match_selection WHERE account_id = ?
    """, [account_id]).fetchall()

    rows = []
    for (match_id,) in match_ids:
        match_data = store.get_cached_match(con, match_id)
        if match_data is None:
            continue

        player = next((p for p in match_data.get("players", [])
                        if p.get("account_id") == account_id), None)
        if player is None:
            continue

        region = match_data.get("region")
        is_radiant = player.get("isRadiant")
        won = (is_radiant == match_data.get("radiant_win"))

        rows.append({
            "region": region,
            "win": won,
            "start_time": match_data.get("start_time"),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    grouped = df.groupby("region").agg(
        n_games=("win", "count"),
        win_rate=("win", "mean"),
        first_played=("start_time", "min"),
        last_played=("start_time", "max"),
    ).reset_index()

    return grouped.sort_values("n_games", ascending=False)


def detect_region_arbitrage(con, account_id: int) -> dict:
    profile = compute_region_winrate_profile(con, account_id)
    if profile.empty:
        return {"flagged": False, "reason": "no region data available", "region_profile": []}

    eligible = profile[profile["n_games"] >= MIN_GAMES_PER_REGION]

    if len(eligible) < 2:
        return {
            "flagged": False,
            "reason": "fewer than 2 regions with sufficient games",
            "region_profile": profile.to_dict("records"),
        }

    max_wr_row = eligible.loc[eligible["win_rate"].idxmax()]
    min_wr_row = eligible.loc[eligible["win_rate"].idxmin()]
    gap = max_wr_row["win_rate"] - min_wr_row["win_rate"]

    flagged = (gap >= MIN_WINRATE_GAP) and (max_wr_row["win_rate"] >= MIN_HIGH_WR_THRESHOLD)

    return {
        "flagged": bool(flagged),
        "high_wr_region": int(max_wr_row["region"]) if pd.notna(max_wr_row["region"]) else None,
        "high_wr_value": round(float(max_wr_row["win_rate"]), 3),
        "high_wr_n_games": int(max_wr_row["n_games"]),
        "low_wr_region": int(min_wr_row["region"]) if pd.notna(min_wr_row["region"]) else None,
        "low_wr_value": round(float(min_wr_row["win_rate"]), 3),
        "low_wr_n_games": int(min_wr_row["n_games"]),
        "winrate_gap": round(float(gap), 3),
        "region_profile": profile.to_dict("records"),
    }


if __name__ == "__main__":
    for account_id in [314554951, 202904162]:
        con = store.get_connection()
        print(f"\n--- Account {account_id} ---")
        result = detect_region_arbitrage(con, account_id)
        for k, v in result.items():
            print(f"  {k}: {v}")
        con.close()