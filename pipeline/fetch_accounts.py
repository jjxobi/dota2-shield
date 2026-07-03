"""
fetch_accounts.py
Fetches core profile + win/loss data for an account and writes directly
into the accounts table. No intermediate JSON files — DuckDB is the
single source of truth.
"""

import store
import api_client


def fetch_and_store_profile(con, account_id: int):
    status = store.get_ingestion_status(con, account_id, "profile")
    if status == "success":
        print(f"  Profile already loaded for {account_id}, skipping (delete ingestion_state row to force refetch)")
        return

    try:
        profile = api_client.get(f"/players/{account_id}")
        wl = api_client.get(f"/players/{account_id}/wl")

        store.upsert_account(
            con, account_id,
            profile.get("profile", {}).get("personaname"),
            profile.get("rank_tier"),
            wl.get("win"),
            wl.get("lose"),
        )
        store.record_ingestion_state(con, account_id, "profile", "success", n_items=1)
        print(f"  Profile loaded for account_id={account_id}")

    except Exception as e:
        store.record_ingestion_state(con, account_id, "profile", "failed", error_message=str(e))
        print(f"  FAILED profile fetch for {account_id}: {e}")
        raise


if __name__ == "__main__":
    TEST_ACCOUNT_ID = 314554951

    con = store.get_connection()
    store.init_schema(con)

    fetch_and_store_profile(con, TEST_ACCOUNT_ID)

    print("\n--- Sanity check ---")
    print(con.execute("SELECT * FROM accounts WHERE account_id = ?", [TEST_ACCOUNT_ID]).fetchdf())
    con.close()