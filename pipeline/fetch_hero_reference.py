"""
fetch_hero_reference.py
One-time pull of hero_id -> hero metadata, including the roles array
(e.g. ["Carry", "Support", "Nuker"]) used as a fallback role signal for
matches where OpenDota's per-match lane_role wasn't parsed (most historical matches fall into this category).
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
import store
import api_client

if __name__ == "__main__":
    con = store.get_connection()
    store.init_schema(con)

    heroes = api_client.get("/heroes")
    n = store.upsert_heroes(con, heroes)

    print(f"Loaded {n} heroes into the heroes table")
    print(con.execute("SELECT hero_id, localized_name, roles FROM heroes LIMIT 5").fetchdf())
    con.close()