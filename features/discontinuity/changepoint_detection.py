"""
changepoint_detection.py
PELT change-point detection on an account's full lifetime win/loss and
KDA series, run on the RAW per-game series (not pre-smoothed) — PELT's
cost model handles the within-segment averaging internally. Pre-smoothing
with a rolling window before detection creates artificial autocorrelation
that breaks PELT's sensitivity calibration, causing it to flag noise as
change points (this was tested and confirmed: the pre-smoothed version
produced 20-50 "change points" per account, spaced every 10-40 games —
clearly noise, not real events).

min_size enforces a minimum realistic segment length: a genuine boosting
stint or account handoff should persist for at least ~40 games to be a
meaningful, distinguishable event (see Class 3's 20-50 game boosting
window in METHODOLOGY.md) — this alone prevents most spurious detections.
"""

import sys
from pathlib import Path
import numpy as np
import ruptures as rpt

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "pipeline"))
import store

MIN_SEGMENT_SIZE = 40  # minimum games between change points to be considered real


def get_full_history_series(con, account_id: int):
    df = con.execute("""
        SELECT match_id, start_time, kills, deaths, assists, win
        FROM match_history
        WHERE account_id = ?
        ORDER BY start_time ASC
    """, [account_id]).fetchdf()
    return df


def calibrated_penalty(series: np.ndarray) -> float:
    """
    BIC-style penalty: variance * log(n). This scales penalty
    appropriately with both the noisiness of the series and its length,
    rather than using a fixed guessed constant.
    """
    n = len(series)
    variance = np.var(series)
    return variance * np.log(n) * 2  # factor of 2 as a conservative multiplier, tunable


def detect_changepoints(series: np.ndarray, min_size: int = MIN_SEGMENT_SIZE) -> list:
    """
    Runs PELT directly on the raw series (no pre-smoothing).
    """
    clean = series[~np.isnan(series)] if np.issubdtype(series.dtype, np.floating) else series
    if len(clean) < min_size * 2:
        return []

    penalty = calibrated_penalty(clean)
    algo = rpt.Pelt(model="l2", min_size=min_size).fit(clean)
    breakpoints = algo.predict(pen=penalty)
    return breakpoints[:-1]  # drop the trailing series-length marker


def analyze_account_discontinuity(con, account_id: int) -> dict:
    df = get_full_history_series(con, account_id)

    if len(df) < MIN_SEGMENT_SIZE * 2:
        return {
            "n_matches": len(df),
            "insufficient_data": True,
            "reason": f"need at least {MIN_SEGMENT_SIZE * 2} matches for reliable change-point detection",
        }

    win_series = df["win"].astype(float).to_numpy()
    kda_series = ((df["kills"] + df["assists"]) / df["deaths"].replace(0, 1)).to_numpy()

    wr_changepoints = detect_changepoints(win_series)
    kda_changepoints = detect_changepoints(kda_series)

    result = {
        "n_matches": len(df),
        "insufficient_data": False,
        "win_rate_changepoints": wr_changepoints,
        "kda_changepoints": kda_changepoints,
        "overall_win_rate": round(float(df["win"].mean()), 3),
    }

    descriptions = []
    for cp in wr_changepoints:
        before = win_series[max(0, cp - MIN_SEGMENT_SIZE):cp].mean()
        after = win_series[cp:cp + MIN_SEGMENT_SIZE].mean()
        descriptions.append({
            "match_index": int(cp),
            "win_rate_before": round(float(before), 3),
            "win_rate_after": round(float(after), 3),
            "shift": round(float(after - before), 3),
        })
    result["win_rate_shift_details"] = descriptions

    return result


if __name__ == "__main__":
    con = store.get_connection()

    for account_id in [314554951, 202904162]:
        print(f"\n--- Account {account_id} ---")
        result = analyze_account_discontinuity(con, account_id)
        for k, v in result.items():
            print(f"  {k}: {v}")

    con.close()