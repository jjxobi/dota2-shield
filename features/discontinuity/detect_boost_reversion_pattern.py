"""
Extends discontinuity classification to detect the specific spike-then-
revert pattern that distinguishes Class 3 (active boosting) and Class 4
(post-boost) from Class 2 (permanent handoff, no reversion).

A single change point only tells us "performance shifted somewhere" —
it can't distinguish a permanent shift (Class 2: bought account, kept)
from a temporary one (Class 3/4: boosted, then reverted). This requires
looking at PAIRS of consecutive change points and checking whether the
second one brings performance back close to where it started.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "pipeline"))
import store
from changepoint_detection import get_full_history_series, detect_changepoints

REVERSION_TOLERANCE = 0.08  # win-rate must return within 8pp of pre-spike baseline
MIN_STINT_LENGTH = 15       # spike must last at least this many games to count as a "stint"


def analyze_boost_reversion(con, account_id: int) -> dict:
    df = get_full_history_series(con, account_id)
    if len(df) < 80:
        return {"account_id": account_id, "pattern": "insufficient_data"}

    win_series = df["win"].astype(float).to_numpy()
    changepoints = detect_changepoints(win_series)

    if len(changepoints) < 2:
        return {"account_id": account_id, "pattern": "no_pair_to_evaluate",
                "n_changepoints": len(changepoints)}

    # Check each consecutive pair of change points for a spike-and-revert shape
    segments = []
    boundaries = [0] + changepoints + [len(win_series)]
    for i in range(len(boundaries) - 1):
        seg = win_series[boundaries[i]:boundaries[i + 1]]
        if len(seg) > 0:
            segments.append({"start": boundaries[i], "end": boundaries[i + 1],
                              "mean_wr": seg.mean(), "length": len(seg)})

    reversion_found = False
    reversion_details = None

    for i in range(len(segments) - 2):
        before, spike, after = segments[i], segments[i + 1], segments[i + 2]
        spike_magnitude = spike["mean_wr"] - before["mean_wr"]
        reverted = abs(after["mean_wr"] - before["mean_wr"]) <= REVERSION_TOLERANCE
        long_enough = spike["length"] >= MIN_STINT_LENGTH

        if abs(spike_magnitude) > REVERSION_TOLERANCE and reverted and long_enough:
            reversion_found = True
            reversion_details = {
                "before_wr": round(before["mean_wr"], 3),
                "spike_wr": round(spike["mean_wr"], 3),
                "after_wr": round(after["mean_wr"], 3),
                "spike_length_games": spike["length"],
                "spike_start_index": spike["start"],
                "spike_end_index": spike["end"],
                "direction": "upward_then_reverted" if spike_magnitude > 0 else "downward_then_reverted",
            }
            break

    return {
        "account_id": account_id,
        "pattern": "spike_and_revert" if reversion_found else "no_reversion_pattern",
        "n_changepoints": len(changepoints),
        "n_segments": len(segments),
        "reversion_details": reversion_details,
    }


if __name__ == "__main__":
    con = store.get_connection()

    account_ids = [row[0] for row in con.execute("SELECT DISTINCT account_id FROM match_history").fetchall()]
    print(f"Scanning {len(account_ids)} accounts for spike-and-revert patterns...\n")

    found = []
    for i, acc_id in enumerate(account_ids, start=1):
        result = analyze_boost_reversion(con, acc_id)
        if result.get("pattern") == "spike_and_revert":
            found.append(result)
            print(f"  FOUND: {acc_id} — {result['reversion_details']}")
        if i % 200 == 0:
            print(f"  [{i}/{len(account_ids)}] scanned, {len(found)} reversion patterns found so far")

    print(f"\nTotal spike-and-revert patterns found: {len(found)}")
    con.close()