"""
fetch_hero_reference.py
One-time pull of the hero_id -> hero name mapping, stored in the heroes
table. Static reference data — refresh occasionally, not per-account.
"""

import store
import api_client

if __name__ == "__main__":
    con = store.get_connection()
    store.init_schema(con)

    heroes = api_client.get("/heroes")
    n = store.upsert_heroes(con, heroes)

    print(f"Loaded {n} heroes into the heroes table")
    print(con.execute("SELECT * FROM heroes LIMIT 5").fetchdf())
    con.close()