"""
fetch_early_matches.py
Selects each account's earliest N solo ranked matches, fetches full parse
detail for any not already in raw_match_cache (deduplicated globally across
all accounts — a match already fetched for another account is reused, not
refetched), then rebuilds the derived parsed_early_matches table.
"""

import store
import api_client


def fetch_early_matches_for_account(con, account_id: int, n_earliest: int = 50):
    status = store.get_ingestion_status(con, account_id, "early_matches")
    if status == "success":
        print(f"  Early matches already loaded for {account_id}, skipping")
        return

    match_ids = store.select_earliest_solo_ranked_matches(con, account_id, n_earliest)
    print(f"  Selected {len(match_ids)} earliest solo ranked matches for account_id={account_id}")

    fetched, cached_hits, failed = 0, 0, 0
    for i, match_id in enumerate(match_ids, start=1):
        if store.get_cached_match(con, match_id) is not None:
            cached_hits += 1
            continue  # already have this match's data — from this account or another

        try:
            match_data = api_client.get(f"/matches/{match_id}")
            store.cache_raw_match(con, match_id, match_data)
            fetched += 1
            print(f"  [{i}/{len(match_ids)}] match_id={match_id} fetched")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(match_ids)}] match_id={match_id} FAILED: {e}")

    n_loaded = store.rebuild_parsed_early_matches(con, account_id)

    status = "success" if n_loaded > 0 else "failed"
    store.record_ingestion_state(con, account_id, "early_matches", status, n_items=n_loaded)

    print(f"  Done: {fetched} newly fetched, {cached_hits} cache hits (deduped), "
          f"{failed} failed, {n_loaded} rows in parsed_early_matches")


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    con = store.get_connection()
    store.init_schema(con)

    fetch_early_matches_for_account(con, TEST_ACCOUNT_ID, n_earliest=50)

    print("\n--- Sanity check ---")
    print(con.execute("""
        SELECT * FROM parsed_early_matches
        WHERE account_id = ?
        ORDER BY sequence_order
        LIMIT 5
    """, [TEST_ACCOUNT_ID]).fetchdf())
    print(con.execute("SELECT COUNT(*) AS n FROM parsed_early_matches WHERE account_id = ?", [TEST_ACCOUNT_ID]).fetchdf())
    con.close()