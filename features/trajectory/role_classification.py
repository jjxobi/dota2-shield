"""
Classifies each match as Core or Support role using a transparent, weighted
composite score rather than a hard rule cascade:

    role_score = 0.35 * hero_prior_signal
               + 0.25 * economy_relative_to_team_signal   (net_worth, falls back to gold_per_min)
               + 0.20 * last_hits_relative_to_team_signal
               + 0.20 * level_relative_to_team_signal

Each component signal is in [-1, +1] (support-leaning to core-leaning).
role_score is persisted alongside the binary role label so downstream
consumers (evidence panel, model features) can use classification
confidence, not just the binarized outcome — a score of +0.9 and +0.05
both say "core" but represent very different certainty, and that
distinction matters for a tool whose whole design philosophy is
"show evidence, not verdicts."

All team-relative signals compare an account ONLY against its own team's
other four players, never the enemy team — comparing across teams would
conflate role with who's winning the game, which is a separate axis we
don't want bleeding into role classification.

When real parsed data is available (is_roaming non-null), we use that
directly instead, since it reflects this specific match's actual lane
assignment rather than an inferred proxy. In practice this is rare for
historical matches — so the weighted composite is the primary path, not an edge-case fallback.

Note: ward placement (obs_placed/sen_placed) would be a strong support
signal but, like lane_role, only exists on parsed matches — confirmed
absent from the full 54-key field list on unparsed match payloads during
pipeline testing. Not usable as a reliable always-available signal.
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
     0.0 = both or neither tag present (flex hero) -> let team-relative
           signals decide; this component contributes nothing

    Deliberately ignores 'Nuker', 'Disabler', 'Initiator', 'Escape' —
    these describe combat function, not economic role, and are carried
    by supports and cores alike (e.g. Crystal Maiden and Lion are both
    'Nuker'-tagged supports). Using them as core signals was tested and
    found to misclassify nearly all support heroes as core.
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
    Returns this account's rank on `field` relative to their OWN team only.
    Range [-1, +1]: +1 = highest on team, -1 = lowest, 0 = insufficient
    data to compare (missing field, or fewer than 2 valid teammates).
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
    Returns {'role': 'core'|'support', 'role_source': str,
              'role_score': float|None, 'economy_field_used': str|None}
    """
    if not pd.isna(is_roaming):
        role = "support" if (is_roaming and gold_per_min < 350) else "core"
        return {
            "role": role,
            "role_source": "parsed",
            "role_score": None,
            "economy_field_used": None,
        }

    match_data = store.get_cached_match(con, match_id)
    players = match_data.get("players", []) if match_data else []

    hero_roles = get_hero_roles(con, hero_id)
    h_signal = hero_prior_signal(hero_roles)

    # net_worth is more stable than gold_per_min (cumulative vs rate,
    # less noisy in short/stomped games) — prefer it, fall back to GPM
    # if it's ever missing from every player in the match.
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
    }


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    con = store.get_connection()

    df = con.execute("""
        SELECT match_id, hero_id, gold_per_min, last_hits, is_roaming
        FROM parsed_early_matches
        WHERE account_id = ?
        ORDER BY sequence_order
        LIMIT 15
    """, [TEST_ACCOUNT_ID]).fetchdf()

    print("--- Role classification sample (weighted composite) ---")
    for _, row in df.iterrows():
        result = classify_match_role(
            con, TEST_ACCOUNT_ID, row["match_id"], row["hero_id"],
            row["is_roaming"], row["gold_per_min"]
        )
        print({
            "hero_id": row["hero_id"],
            "gpm": row["gold_per_min"],
            "last_hits": row["last_hits"],
            **result,
        })

    con.close()