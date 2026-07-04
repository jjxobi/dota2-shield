"""
discover_random_seed_accounts.py
Two-stage random account discovery, designed to break the skill-bracket
clustering bias of teammate/opponent-based discovery (which only reaches
accounts near our own MMR and social graph).

Stage 1: Pull match_ids from /publicMatches (random concurrent games
across the whole player base — unranked/bot lobby_types only, confirmed
via testing that ranked matches never appear in this feed). Deep-fetch
each match to extract its 10 real account_ids. We discard the match data
itself (it's not ranked, not useful) and keep only the player identities
— these are real, randomly-sampled people across a genuine skill spread,
since /publicMatches samples live concurrent games broadly, not filtered
to any bracket.

Stage 2: For each discovered account_id, check whether THEY separately
have ranked solo history (via the existing, working
/players/{id}/matches?lobby_type=7 endpoint). Many won't — plenty of
players never touch ranked — but this is the actual pivot point into
real ranked data across a genuinely diverse population.

Explicitly capped and time-estimated. Two account_ids-per-API-call
before profile is even considered "cheap" — real cost is in Stage 2's
per-account ranked-history check.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import store
import api_client

STAGE1_MAX_MATCHES = 30          # 30 deep-fetches ≈ 40 seconds, up to 300 raw account_ids
STAGE2_MAX_ACCOUNTS_TO_CHECK = 100  # capped independently — this is the slower, per-account stage
SECONDS_PER_CALL_ESTIMATE = 1.3


def stage1_discover_random_accounts(con, max_matches: int) -> set:
    matches = api_client.get("/publicMatches")
    match_ids = [m["match_id"] for m in matches][:max_matches]

    print(f"Stage 1: deep-fetching {len(match_ids)} random public match_ids "
          f"(~{len(match_ids) * SECONDS_PER_CALL_ESTIMATE / 60:.1f} min estimated)...")

    discovered = set()
    for i, match_id in enumerate(match_ids, start=1):
        cached = store.get_cached_match(con, match_id)
        if cached is None:
            try:
                cached = api_client.get(f"/matches/{match_id}")
                store.cache_raw_match(con, match_id, cached)
            except Exception as e:
                print(f"  [{i}/{len(match_ids)}] match_id={match_id} FAILED: {e}")
                continue

        for p in cached.get("players", []):
            acc_id = p.get("account_id")
            if acc_id is not None:
                discovered.add(acc_id)

        if i % 10 == 0:
            print(f"  [{i}/{len(match_ids)}] {len(discovered)} unique accounts so far")

    print(f"Stage 1 complete: {len(discovered)} random account_ids discovered\n")
    return discovered


def stage2_check_ranked_history(con, account_ids: set, max_to_check: int) -> dict:
    to_check = list(account_ids)[:max_to_check]
    print(f"Stage 2: checking ranked solo history for {len(to_check)} accounts "
          f"(~{len(to_check) * SECONDS_PER_CALL_ESTIMATE / 60:.1f} min estimated)...")

    results = {"has_ranked": [], "no_ranked": [], "failed": []}

    for i, acc_id in enumerate(to_check, start=1):
        try:
            matches = api_client.get(f"/players/{acc_id}/matches", params={"lobby_type": 7})
            if matches:
                store.upsert_match_history(con, acc_id, matches)
                results["has_ranked"].append(acc_id)
            else:
                results["no_ranked"].append(acc_id)
        except Exception as e:
            print(f"  [{i}/{len(to_check)}] account_id={acc_id} FAILED: {e}")
            results["failed"].append(acc_id)

        if i % 10 == 0:
            print(f"  [{i}/{len(to_check)}] {len(results['has_ranked'])} with ranked history so far")

    return results


if __name__ == "__main__":
    con = store.get_connection()
    store.init_schema(con)

    random_accounts = stage1_discover_random_accounts(con, STAGE1_MAX_MATCHES)
    results = stage2_check_ranked_history(con, random_accounts, STAGE2_MAX_ACCOUNTS_TO_CHECK)

    print(f"\n--- Summary ---")
    print(f"Random accounts discovered: {len(random_accounts)}")
    print(f"Checked for ranked history: {len(results['has_ranked']) + len(results['no_ranked']) + len(results['failed'])}")
    print(f"Have ranked solo history: {len(results['has_ranked'])}")
    print(f"No ranked history: {len(results['no_ranked'])}")
    print(f"Failed: {len(results['failed'])}")
    print(f"\nAccounts with ranked history (ready for profile fetch + qualification): {results['has_ranked'][:20]}...")

    con.close()