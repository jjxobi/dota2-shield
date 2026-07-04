"""
fetch_match_history.py
Fetches an account's full ranked match history (lobby_type=7, server-side
filtered) — no artificial limit, since discontinuity detection (Family 2)
needs full lifetime history, not just an early window. Writes directly
into the match_history table.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import store
import api_client


def fetch_and_store_match_history(con, account_id: int, limit: int = None):
    """
    limit=None fetches OpenDota's full available history for this account
    (subject to whatever cap OpenDota itself applies server-side — in
    practice this has been sufficient for full career history on the
    accounts tested so far).
    """
    status = store.get_ingestion_status(con, account_id, "match_history")
    if status == "success":
        print(f"  Match history already loaded for {account_id}, skipping "
              f"(clear ingestion_state row to force refetch)")
        return

    params = {"lobby_type": 7}
    if limit is not None:
        params["limit"] = limit

    try:
        matches = api_client.get(f"/players/{account_id}/matches", params=params)
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
    con.close()