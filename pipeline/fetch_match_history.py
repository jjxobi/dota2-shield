"""
fetch_match_history.py
Fetches ranked match history (lobby_type=7, server-side filtered) for an
account and writes directly into the match_history table.
"""

import store
import api_client


def fetch_and_store_match_history(con, account_id: int, limit: int = 200):
    status = store.get_ingestion_status(con, account_id, "match_history")
    if status == "success":
        print(f"  Match history already loaded for {account_id}, skipping")
        return

    try:
        matches = api_client.get(
            f"/players/{account_id}/matches",
            params={"limit": limit, "lobby_type": 7},
        )
        n = store.upsert_match_history(con, account_id, matches)
        store.record_ingestion_state(con, account_id, "match_history", "success", n_items=n)
        print(f"  Loaded {n} ranked match_history rows for account_id={account_id}")

    except Exception as e:
        store.record_ingestion_state(con, account_id, "match_history", "failed", error_message=str(e))
        print(f"  FAILED match history fetch for {account_id}: {e}")
        raise


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    con = store.get_connection()
    store.init_schema(con)

    fetch_and_store_match_history(con, TEST_ACCOUNT_ID)

    print("\n--- Sanity check ---")
    print(con.execute("SELECT COUNT(*) AS total FROM match_history WHERE account_id = ?", [TEST_ACCOUNT_ID]).fetchdf())
    print(con.execute("SELECT COUNT(*) AS solo_ranked FROM ranked_solo_matches WHERE account_id = ?", [TEST_ACCOUNT_ID]).fetchdf())
    con.close()