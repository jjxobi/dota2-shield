"""
discover_discontinuity_candidates.py
Source 3 from the original brief, applied at scale: scans every already-
profiled account with match_history loaded for win-rate/KDA discontinuities,
classifying by shift direction into weak Class 2/3 (upward), Class 4
(downward), or Class 5 (persistent low performance, no shift) candidates.

This directly addresses a real gap in today's work: all prior discovery
effort went into Class 1 (rank-tier heuristic) because it was cheap and
already working, while Classes 2-5 were left with a working DETECTOR but
no DISCOVERY mechanism actively searching for accounts matching them.
"""

import sys
from pathlib import Path
import csv
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "features" / "discontinuity"))
import store
from changepoint_detection import analyze_account_discontinuity

LABELS_DIR = Path(__file__).resolve().parent.parent / "data" / "labels"

# Class 5 signal: persistent underperformance, no discontinuity needed.
# Below 45% win rate over meaningful volume is a rough starting threshold,
# not yet empirically calibrated.
CLASS5_MAX_WINRATE = 0.45
CLASS5_MIN_GAMES = 100


def get_accounts_with_history(con) -> list:
    return [row[0] for row in con.execute("""
        SELECT DISTINCT account_id FROM match_history
    """).fetchall()]


def classify_discontinuity_candidate(result: dict) -> str:
    if result.get("insufficient_data"):
        return "insufficient_data"

    wr_changepoints = result.get("win_rate_changepoints", [])
    overall_wr = result.get("overall_win_rate", 0.5)
    n_matches = result.get("n_matches", 0)

    if wr_changepoints:
        # Look at direction of the FIRST detected change point
        shift = result["win_rate_shift_details"][0]["shift"]
        if shift > 0:
            return "class2_3_candidate_upward_shift"
        else:
            return "class4_candidate_downward_shift"

    if overall_wr <= CLASS5_MAX_WINRATE and n_matches >= CLASS5_MIN_GAMES:
        return "class5_candidate_persistent_underperformance"

    return "no_signal"


def scan_for_discontinuity_candidates(con, account_ids: list) -> list:
    results = []
    for i, acc_id in enumerate(account_ids, start=1):
        result = analyze_account_discontinuity(con, acc_id)
        classification = classify_discontinuity_candidate(result)

        if classification not in ("insufficient_data", "no_signal"):
            results.append({
                "account_id": acc_id,
                "classification": classification,
                "n_matches": result.get("n_matches"),
                "overall_win_rate": result.get("overall_win_rate"),
                "changepoints": result.get("win_rate_changepoints"),
                "shift_details": result.get("win_rate_shift_details"),
                "flagged_at": datetime.now(timezone.utc).isoformat(),
            })

        if i % 100 == 0:
            print(f"  [{i}/{len(account_ids)}] {len(results)} candidates found so far")

    return results


if __name__ == "__main__":
    con = store.get_connection()

    account_ids = get_accounts_with_history(con)
    print(f"Scanning {len(account_ids)} accounts with match_history for discontinuity signals "
          f"(no new API calls — this is pure computation on already-cached data)\n")

    results = scan_for_discontinuity_candidates(con, account_ids)
    con.close()

    print(f"\nTotal candidates found: {len(results)}")
    by_class = {}
    for r in results:
        by_class.setdefault(r["classification"], []).append(r)
    for cls, items in by_class.items():
        print(f"  {cls}: {len(items)}")

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LABELS_DIR / "discontinuity_candidates_weak.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["account_id", "classification", "n_matches",
                                                  "overall_win_rate", "changepoints",
                                                  "shift_details", "flagged_at"])
        writer.writeheader()
        writer.writerows(results)
    print(f"\nSaved to {out_path}")