"""
branch_from_recent_matches.py
Expands the candidate pool by pulling each account's 5 MOST RECENT ranked
matches, deep-fetching each (cache-deduplicated globally — overlapping
matches across accounts cost nothing extra), and extracting new account_ids
from teammates/opponents.

Pulls seed accounts from the accounts table directly (every account ever
profiled so far), not a hardcoded list — but caps the number of unique
match_ids actually fetched per run, since branching from hundreds of
seed accounts can produce thousands of match_ids. Prints the real count
and estimated runtime BEFORE fetching anything, given the runaway scope
incident earlier today.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import store
import api_client

MATCHES_PER_ACCOUNT = 3
MAX_MATCHES_TO_FETCH_PER_RUN = 150  # explicit cap — re-run to continue further
SECONDS_PER_CALL_ESTIMATE = 1.3


def get_all_seed_account_ids(con) -> list:
    rows = con.execute("SELECT account_id FROM accounts").fetchall()
    return [r[0] for r in rows]


def get_recent_match_ids(con, account_id: int, n: int) -> list:
    rows = con.execute("""
        SELECT match_id FROM match_history
        WHERE account_id = ?
        ORDER BY start_time DESC
        LIMIT ?
    """, [account_id, n]).fetchall()
    return [r[0] for r in rows]


def branch_from_all_accounts(con, max_matches_to_fetch: int) -> set:
    seed_account_ids = set(get_all_seed_account_ids(con))
    print(f"Found {len(seed_account_ids)} seed accounts in the accounts table")

    all_match_ids = set()
    for acc_id in seed_account_ids:
        all_match_ids.update(get_recent_match_ids(con, acc_id, MATCHES_PER_ACCOUNT))

    print(f"Collected {len(all_match_ids)} total unique recent match_ids")

    already_cached = sum(1 for mid in all_match_ids if store.get_cached_match(con, mid) is not None)
    need_fetching = len(all_match_ids) - already_cached
    print(f"Already cached: {already_cached}, need fetching: {need_fetching}")

    to_process = list(all_match_ids)[:max_matches_to_fetch]
    estimated_new_fetches = min(need_fetching, max_matches_to_fetch)
    print(f"\nCapped to {len(to_process)} match_ids this run "
          f"(~{estimated_new_fetches * SECONDS_PER_CALL_ESTIMATE / 60:.1f} min estimated for new fetches)\n")

    new_accounts = set()
    for i, match_id in enumerate(to_process, start=1):
        cached = store.get_cached_match(con, match_id)
        if cached is None:
            try:
                cached = api_client.get(f"/matches/{match_id}")
                store.cache_raw_match(con, match_id, cached)
            except Exception as e:
                print(f"  [{i}/{len(to_process)}] match_id={match_id} FAILED: {e}")
                continue

        for p in cached.get("players", []):
            acc_id = p.get("account_id")
            if acc_id is not None and acc_id not in seed_account_ids:
                new_accounts.add(acc_id)

        if i % 25 == 0:
            print(f"  [{i}/{len(to_process)}] {len(new_accounts)} new accounts so far")

    return new_accounts, len(all_match_ids) - len(to_process)


if __name__ == "__main__":
    con = store.get_connection()

    new_accounts, remaining = branch_from_all_accounts(con, MAX_MATCHES_TO_FETCH_PER_RUN)

    print(f"\nDone. {len(new_accounts)} new candidate account_ids discovered this run.")
    print(f"{remaining} match_ids remain unprocessed — re-run this script to continue.")
    print(f"Sample: {list(new_accounts)[:20]}")

    con.close()