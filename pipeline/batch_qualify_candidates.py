"""
batch_qualify_candidates.py
Runs the full feature pipeline (trajectory, economic dominance,
discontinuity) against every candidate in a labels CSV, and sorts them
into 'usable' (at least one feature family returned real data) vs
'insufficient_data' (every feature family reported insufficient_data —
not enough games to say anything, regardless of rank/game-count heuristic
that flagged them in the first place).

This exists because Source 1 (rank-tier/game-count heuristic) can flag
accounts that turn out to have too little match history for ANY feature
to compute reliably — discovered by manually testing one such candidate
(164055715: Divine rank, only 21 ranked solo games ever, every feature
returned insufficient_data). Rather than silently keep such accounts in
a labels file implying they're usable training examples, this script
makes the qualification explicit and auditable.
"""

import sys
import csv
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "features" / "trajectory"))
sys.path.append(str(Path(__file__).resolve().parent.parent / "features" / "discontinuity"))

import store
from fetch_match_history import fetch_and_store_match_history
from fetch_early_matches import fetch_early_matches_for_account
from role_classification import classify_and_store_account_matches
from skill_trajectory import compute_core_trajectory, compute_support_trajectory, compute_economic_dominance_score
from changepoint_detection import analyze_account_discontinuity

LABELS_DIR = Path(__file__).resolve().parent.parent / "data" / "labels"


def qualify_account(con, account_id: int) -> dict:
    """
    Runs every feature function for one account, returns a summary dict
    including whether ANY feature family produced usable (non-insufficient)
    output.
    """
    try:
        fetch_and_store_match_history(con, account_id)
    except Exception as e:
        return {"account_id": account_id, "error": str(e), "usable": False}

    try:
        fetch_early_matches_for_account(con, account_id, n_earliest=50)
        classify_and_store_account_matches(con, account_id)
    except Exception as e:
        print(f"  Warning: early-match pipeline failed for {account_id}: {e}")

    core = compute_core_trajectory(con, account_id)
    support = compute_support_trajectory(con, account_id)
    dominance = compute_economic_dominance_score(con, account_id)
    discontinuity = analyze_account_discontinuity(con, account_id)

    usable = not all([
        core.get("insufficient_data", True),
        support.get("insufficient_data", True),
        dominance.get("insufficient_data", True),
        discontinuity.get("insufficient_data", True),
    ])

    return {
        "account_id": account_id,
        "usable": usable,
        "core_n_games": core.get("core_n_games"),
        "core_lhm_slope": core.get("core_lhm_slope"),
        "core_gpm_slope": core.get("core_gpm_slope"),
        "core_lhm_game1": core.get("core_lhm_game1"),
        "support_n_games": support.get("support_n_games"),
        "economic_dominance_avg": dominance.get("economic_dominance_avg"),
        "discontinuity_n_matches": discontinuity.get("n_matches"),
        "discontinuity_win_rate_changepoints": discontinuity.get("win_rate_changepoints"),
    }


def batch_qualify(con, account_ids: list) -> list:
    results = []
    for i, acc_id in enumerate(account_ids, start=1):
        print(f"[{i}/{len(account_ids)}] Qualifying account_id={acc_id}...")
        result = qualify_account(con, acc_id)
        results.append(result)
        print(f"  usable={result.get('usable')}")
    return results


def write_qualified_csvs(results: list):
    usable = [r for r in results if r.get("usable")]
    insufficient = [r for r in results if not r.get("usable")]

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["account_id", "usable", "core_n_games", "core_lhm_slope",
                  "core_gpm_slope", "core_lhm_game1", "support_n_games",
                  "economic_dominance_avg", "discontinuity_n_matches",
                  "discontinuity_win_rate_changepoints"]

    def write(path, rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {path}")

    write(LABELS_DIR / "class1_candidates_qualified_usable.csv", usable)
    write(LABELS_DIR / "class1_candidates_insufficient_data.csv", insufficient)


if __name__ == "__main__":
    con = store.get_connection()
    store.init_schema(con)

    with open(LABELS_DIR / "class1_candidates_low_games_stronger.csv", newline="", encoding="utf-8") as f:
        candidates = [int(row["account_id"]) for row in csv.DictReader(f)]

    print(f"Batch qualifying {len(candidates)} candidates...\n")
    results = batch_qualify(con, candidates)
    write_qualified_csvs(results)

    con.close()