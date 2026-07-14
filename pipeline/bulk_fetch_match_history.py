"""
bulk_fetch_match_history.py
Loads match_history for every profiled account that doesn't have it yet —
this is what enables discontinuity discovery at scale. Cheap: one API
call per account, same cost class as profile fetching.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
import store
import api_client
from fetch_match_history import fetch_and_store_match_history

MAX_PER_RUN = 800
SAFETY_MARGIN = 100

con = store.get_connection()
store.init_schema(con)

profiled = set(row[0] for row in con.execute("SELECT account_id FROM accounts").fetchall())
have_history = set(row[0] for row in con.execute("SELECT DISTINCT account_id FROM match_history").fetchall())
missing_history = list(profiled - have_history)

print(f"{len(profiled)} profiled, {len(have_history)} have match_history, "
      f"{len(missing_history)} missing it")

to_process = missing_history[:MAX_PER_RUN]
print(f"Processing up to {len(to_process)} this run\n")

processed, failed = 0, 0
for i, acc_id in enumerate(to_process, start=1):
    remaining = api_client.get_daily_remaining()
    if remaining is not None and int(remaining) < SAFETY_MARGIN:
        print(f"Stopping: {remaining} remaining")
        break
    try:
        fetch_and_store_match_history(con, acc_id)
        processed += 1
    except Exception:
        failed += 1
    if i % 50 == 0:
        print(f"  [{i}/{len(to_process)}] processed={processed}, remaining={api_client.get_daily_remaining()}")

print(f"\nDone: {processed} processed, {failed} failed")
print(f"{len(missing_history) - processed} still missing history — re-run to continue")
con.close()