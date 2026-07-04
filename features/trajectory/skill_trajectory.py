"""
Computes trajectory features from an account's earliest solo ranked
matches, split by role (core vs support), plus a role-independent
economic dominance signal that specifically catches a case the role-split
approach alone would miss: a smurf playing support heroes who still
dominates their team economically. That account should stay labeled
'support' (correct — that's genuinely the role being played) while still
surfacing a strong smurf signal via economy_signal, independent of the
role label.

All three functions here read from parsed_early_matches (chronological,
sequence_order-ordered) joined with match_role_scores (role labels +
raw component signals). Nothing here calls the API — everything is
computed from already-cached, already-classified data.
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "pipeline"))
import store


def _linear_slope(x: np.ndarray, y: np.ndarray):
    if len(x) < 2 or np.all(x == x[0]):
        return None
    slope, _ = np.polyfit(x, y, 1)
    return round(float(slope), 4)


def _get_joined_matches(con, account_id: int) -> pd.DataFrame:
    """
    parsed_early_matches joined with match_role_scores on (account_id, match_id).
    """
    df = con.execute("""
        SELECT
            p.sequence_order, p.hero_id, p.gold_per_min, p.xp_per_min,
            p.last_hits, p.denies, p.duration,
            r.role, r.role_score, r.economy_signal, r.last_hits_signal, r.level_signal
        FROM parsed_early_matches p
        JOIN match_role_scores r
          ON p.account_id = r.account_id AND p.match_id = r.match_id
        WHERE p.account_id = ?
        ORDER BY p.sequence_order
    """, [account_id]).fetchdf()
    return df


def compute_core_trajectory(con, account_id: int, min_games: int = 5) -> dict:
    """
    Last-hit-per-minute and GPM improvement trajectory, computed ONLY on
    games classified as 'core'. Last hits are normalized by game duration
    (last_hits / (duration / 60)) since raw last-hit count conflates
    mechanical skill with game length — a 25-minute stomp and a 55-minute
    grind aren't comparable on raw count alone.
    """
    df = _get_joined_matches(con, account_id)
    core_df = df[df["role"] == "core"].reset_index(drop=True)

    if len(core_df) < min_games:
        return {
            "core_n_games": len(core_df),
            "core_lhm_slope": None, "core_gpm_slope": None,
            "core_lhm_game1": None, "core_lhm_consistency": None,
            "insufficient_data": True,
        }

    duration_min = core_df["duration"].to_numpy(dtype=float) / 60.0
    lh_per_min = core_df["last_hits"].to_numpy(dtype=float) / duration_min

    x = np.arange(len(core_df), dtype=float)
    gpm = core_df["gold_per_min"].to_numpy(dtype=float)

    return {
        "core_n_games": len(core_df),
        "core_lhm_slope": _linear_slope(x, lh_per_min),
        "core_gpm_slope": _linear_slope(x, gpm),
        "core_lhm_game1": round(float(lh_per_min[0]), 2),
        "core_lhm_consistency": round(float(lh_per_min.std()), 2),
        "insufficient_data": False,
    }


def compute_support_trajectory(con, account_id: int, min_games: int = 5) -> dict:
    """
    Trajectory features for games classified as 'support'. Farm-based
    metrics (last hits, GPM) are not the relevant skill signal here —
    instead we track hero pool diversity (a smurf typically shows
    complex/varied support picks immediately, a legit new support player
    starts narrow) and XP-per-min trajectory as a rough proxy for
    positioning/survivability skill, which supports still need even
    without farm priority.

    NOTE: assist-participation-relative-to-team and true hero mechanical
    complexity are flagged as future enhancements (see METHODOLOGY.md
    feature log) — both need either additional raw_match_cache lookups
    per game or a hand-built hero complexity reference we haven't built
    yet. Kept out of v1 to avoid overclaiming precision we don't have.
    """
    df = _get_joined_matches(con, account_id)
    support_df = df[df["role"] == "support"].reset_index(drop=True)

    if len(support_df) < min_games:
        return {
            "support_n_games": len(support_df),
            "support_xpm_slope": None,
            "support_unique_heroes": None,
            "support_hero_pool_concentration": None,
            "insufficient_data": True,
        }

    x = np.arange(len(support_df), dtype=float)
    xpm = support_df["xp_per_min"].to_numpy(dtype=float)

    unique_heroes = support_df["hero_id"].nunique()
    # concentration: 1.0 = always the same hero, closer to 0 = evenly spread
    hero_counts = support_df["hero_id"].value_counts(normalize=True)
    concentration = round(float((hero_counts ** 2).sum()), 3)  # Herfindahl index

    return {
        "support_n_games": len(support_df),
        "support_xpm_slope": _linear_slope(x, xpm),
        "support_unique_heroes": int(unique_heroes),
        "support_hero_pool_concentration": concentration,
        "insufficient_data": False,
    }


def compute_economic_dominance_score(con, account_id: int, min_games: int = 5) -> dict:
    """
    Role-INDEPENDENT signal: average team-relative economic dominance
    across ALL early games regardless of role label. This exists
    specifically to catch a smurf sandbagging on support heroes — the
    role label may correctly stay 'support' (that's genuinely the role
    played), but a persistently high economy_signal even on support
    picks is itself a strong, independent anomaly signal that a
    role-gated trajectory feature alone would miss entirely.
    """
    df = _get_joined_matches(con, account_id)

    if len(df) < min_games:
        return {
            "economic_dominance_avg": None,
            "economic_dominance_on_support_games": None,
            "insufficient_data": True,
        }

    overall_avg = round(float(df["economy_signal"].dropna().mean()), 3) \
        if df["economy_signal"].notna().any() else None

    support_only = df[df["role"] == "support"]
    support_avg = round(float(support_only["economy_signal"].dropna().mean()), 3) \
        if support_only["economy_signal"].notna().any() else None

    return {
        "economic_dominance_avg": overall_avg,
        "economic_dominance_on_support_games": support_avg,
        "insufficient_data": False,
    }


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    con = store.get_connection()
    store.init_schema(con)

    # Ensure role scores are populated before computing trajectories
    import role_classification as rc_module  # noqa: not needed if already imported elsewhere
    sys.path.append(str(Path(__file__).resolve().parent))
    from role_classification import classify_and_store_account_matches
    n = classify_and_store_account_matches(con, TEST_ACCOUNT_ID)
    print(f"Classified {n} matches")

    print("\n--- Core trajectory ---")
    print(compute_core_trajectory(con, TEST_ACCOUNT_ID))

    print("\n--- Support trajectory ---")
    print(compute_support_trajectory(con, TEST_ACCOUNT_ID))

    print("\n--- Economic dominance (role-independent) ---")
    print(compute_economic_dominance_score(con, TEST_ACCOUNT_ID))

    con.close()