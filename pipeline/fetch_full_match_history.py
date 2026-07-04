"""
Opportunistically fetches full match detail (plain GET, no parse-request)
for every match_id in an account's FULL match_history — not just the
earliest N. Some fraction will already have been parsed by OpenDota (if
any of the 10 players in that match ever visited opendota.com while the
replay was still live), and that data is retrievable forever regardless
of match age. Others will return version=None permanently (never parsed,
replay long expired) — nothing we can do about those specific matches.

This measures the REAL parsed-coverage rate across full history, rather
than assuming a fixed early-games-only ceiling.

Caps at max_matches per run to keep API budget predictable — increase
once we know the actual coverage rate and cost is worth it.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "pipeline"))
import store
import api_client


def fetch_full_history_details(con, account_id: int, max_matches: int = 300):
    match_ids = [row[0] for row in con.execute("""
        SELECT match_id FROM match_history WHERE account_id = ?
        ORDER BY start_time ASC
    """, [account_id]).fetchall()]

    total = len(match_ids)
    to_process = match_ids[:max_matches]
    print(f"Account {account_id}: {total} total ranked matches, processing up to {len(to_process)}")

    already_cached, newly_fetched, parsed_count, unparsed_count = 0, 0, 0, 0

    for i, match_id in enumerate(to_process, start=1):
        cached = store.get_cached_match(con, match_id)
        if cached is not None:
            already_cached += 1
        else:
            try:
                cached = api_client.get(f"/matches/{match_id}")
                store.cache_raw_match(con, match_id, cached)
                newly_fetched += 1
            except Exception as e:
                print(f"  [{i}/{len(to_process)}] match_id={match_id} FAILED: {e}")
                continue

        if cached.get("version") is not None:
            parsed_count += 1
        else:
            unparsed_count += 1

        if i % 25 == 0:
            print(f"  [{i}/{len(to_process)}] processed so far...")

    coverage_rate = parsed_count / len(to_process) if to_process else 0

    print(f"\n--- Coverage report for account {account_id} ---")
    print(f"  Total ranked matches in history: {total}")
    print(f"  Matches processed this run: {len(to_process)}")
    print(f"  Already cached from before: {already_cached}")
    print(f"  Newly fetched this run: {newly_fetched}")
    print(f"  PARSED (deep data available): {parsed_count}")
    print(f"  Unparsed (replay expired/never parsed): {unparsed_count}")
    print(f"  Parsed coverage rate: {coverage_rate:.1%}")

    return {
        "total_matches": total,
        "processed": len(to_process),
        "parsed_count": parsed_count,
        "unparsed_count": unparsed_count,
        "coverage_rate": coverage_rate,
    }


if __name__ == "__main__":
    con = store.get_connection()

    for account_id in [314554951, 202904162]:
        fetch_full_history_details(con, account_id, max_matches=300)
        print()

    con.close()