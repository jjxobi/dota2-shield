"""
role_classification.py
Classifies each match as Core or Support role using a transparent, weighted
composite score rather than a hard rule cascade:

    role_score = 0.35 * hero_prior_signal
               + 0.25 * economy_relative_to_team_signal   (net_worth, falls
                                                            back to gold_per_min)
               + 0.20 * last_hits_relative_to_team_signal
               + 0.20 * level_relative_to_team_signal

Each component signal is in [-1, +1] (support-leaning to core-leaning).
Both the combined role_score AND the individual component signals are
persisted — economy_signal specifically is also consumed standalone by
skill_trajectory.compute_economic_dominance_score(), since a smurf
playing support heroes can show high economic dominance while still
correctly being labeled role='support'. The role label alone would hide
that signal; keeping the raw component exposes it.

All team-relative signals compare an account ONLY against its own team's
other four players, never the enemy team — comparing across teams would
conflate role with who's winning the game, a separate axis.

When real parsed data is available (is_roaming non-null), we use that
directly instead. In practice this is rare for historical matches — Valve
retains replays for ~10 days, so most matches analyzed after the fact
were never deep-parsed (see METHODOLOGY.md) — so the weighted composite
is the primary path, not an edge-case fallback.
"""

import sys
import json
import pandas as pd
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "pipeline"))
import store

HERO_PRIOR_WEIGHT = 0.35
ECONOMY_WEIGHT = 0.25
LAST_HITS_WEIGHT = 0.20
LEVEL_WEIGHT = 0.20


def get_hero_roles(con, hero_id: int) -> list:
    result = con.execute("SELECT roles FROM heroes WHERE hero_id = ?", [hero_id]).fetchone()
    if result is None or result[0] is None:
        return []
    return json.loads(result[0])


def hero_prior_signal(hero_roles: list) -> float:
    """
    +1.0 = Carry-tagged, not Support -> core-leaning
    -1.0 = Support-tagged, not Carry -> support-leaning
     0.0 = both or neither tag present (flex hero) -> team-relative
           signals decide

    Deliberately ignores 'Nuker', 'Disabler', 'Initiator', 'Escape' —
    these describe combat function, not economic role, and are carried
    by supports and cores alike (e.g. Crystal Maiden and Lion are both
    'Nuker'-tagged supports). Confirmed via test output that including
    them misclassified nearly all support heroes as core.
    """
    is_carry = "Carry" in hero_roles
    is_support = "Support" in hero_roles
    if is_carry and not is_support:
        return 1.0
    if is_support and not is_carry:
        return -1.0
    return 0.0


def team_relative_signal(players: list, account_id: int, field: str) -> float:
    """
    This account's rank on `field` relative to its OWN team only.
    Range [-1, +1]: +1 = highest on team, -1 = lowest, 0 = insufficient
    data (missing field, or fewer than 2 valid teammates).
    """
    player = next((p for p in players if p.get("account_id") == account_id), None)
    if player is None or player.get(field) is None:
        return 0.0

    is_radiant = player.get("isRadiant")
    teammates = [p for p in players
                 if p.get("isRadiant") == is_radiant and p.get(field) is not None]

    if len(teammates) < 2:
        return 0.0

    own_value = player.get(field)
    values = sorted(p.get(field) for p in teammates)
    rank = values.index(own_value)
    percentile = rank / (len(values) - 1)
    return (percentile - 0.5) * 2


def classify_match_role(con, account_id: int, match_id: int, hero_id: int,
                          is_roaming, gold_per_min: int) -> dict:
    """
    Returns role label, combined role_score, AND raw component signals.
    """
    if not pd.isna(is_roaming):
        role = "support" if (is_roaming and gold_per_min < 350) else "core"
        return {
            "role": role, "role_source": "parsed", "role_score": None,
            "economy_field_used": None, "economy_signal": None,
            "last_hits_signal": None, "level_signal": None,
        }

    match_data = store.get_cached_match(con, match_id)
    players = match_data.get("players", []) if match_data else []

    hero_roles = get_hero_roles(con, hero_id)
    h_signal = hero_prior_signal(hero_roles)

    economy_field = "net_worth"
    if not players or all(p.get("net_worth") is None for p in players):
        economy_field = "gold_per_min"

    economy_signal = team_relative_signal(players, account_id, economy_field)
    lh_signal = team_relative_signal(players, account_id, "last_hits")
    level_signal = team_relative_signal(players, account_id, "level")

    role_score = (HERO_PRIOR_WEIGHT * h_signal
                  + ECONOMY_WEIGHT * economy_signal
                  + LAST_HITS_WEIGHT * lh_signal
                  + LEVEL_WEIGHT * level_signal)

    role = "core" if role_score >= 0 else "support"
    return {
        "role": role,
        "role_source": "weighted_composite",
        "role_score": round(role_score, 3),
        "economy_field_used": economy_field,
        "economy_signal": round(economy_signal, 3),
        "last_hits_signal": round(lh_signal, 3),
        "level_signal": round(level_signal, 3),
    }


def classify_and_store_account_matches(con, account_id: int) -> int:
    """
    Computes role classification for every match in this account's
    parsed_early_matches, persists to match_role_scores. Rebuildable —
    safe to call repeatedly.
    """
    df = con.execute("""
        SELECT match_id, sequence_order, hero_id, gold_per_min, is_roaming
        FROM parsed_early_matches
        WHERE account_id = ?
        ORDER BY sequence_order
    """, [account_id]).fetchdf()

    rows = []
    for _, row in df.iterrows():
        result = classify_match_role(
            con, account_id, row["match_id"], row["hero_id"],
            row["is_roaming"], row["gold_per_min"]
        )
        rows.append([
            account_id, int(row["match_id"]), int(row["sequence_order"]),
            result["role"], result["role_source"], result["role_score"],
            result["economy_field_used"], result["economy_signal"],
            result["last_hits_signal"], result["level_signal"],
        ])

    store.upsert_match_role_scores(con, rows)
    return len(rows)


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    con = store.get_connection()
    n = classify_and_store_account_matches(con, TEST_ACCOUNT_ID)
    print(f"Classified and stored {n} matches")

    df = store.get_match_role_scores(con, TEST_ACCOUNT_ID)
    print(df.to_string())

    con.close()