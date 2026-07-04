"""
store.py
Single source of truth for all DOTA-SHIELD data: schema definition,
connection management, and read/write helpers.

Design principles:
  - Raw facts (accounts, match_history, raw_match_cache) are the durable
    log. Derived tables (parsed_early_matches, match_role_scores) are
    rebuildable from raw facts at any time.
  - raw_match_cache is keyed by match_id globally, not per-account — a
    match played by two labelled accounts is only ever fetched/stored once.
  - ingestion_state is the resumability ledger: before fetching anything,
    callers check this table so re-running a script after a partial
    failure doesn't redo completed work or burn API budget.

NOTE ON SCHEMA CHANGES: tables are created with CREATE TABLE IF NOT EXISTS,
which means changing a table definition here does NOT alter an existing
database file — the old schema silently persists. When adding/changing
columns on a table that already exists in your local dota_shield.duckdb,
drop it first: con.execute("DROP TABLE IF EXISTS <table_name>") before
calling init_schema(), or delete the .duckdb file entirely for a clean
rebuild. This has bitten us twice already (parsed_early_matches, heroes).
"""

import duckdb
import json
from pathlib import Path
from datetime import datetime, timezone
import uuid

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
DB_PATH = DATA_DIR / "dota_shield.duckdb"

def create_lookup_job(con, account_id: int, tier1_result: dict) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    con.execute("""
        INSERT INTO lookup_jobs (job_id, account_id, status, tier1_result, tier2_result, created_at, updated_at)
        VALUES (?, ?, 'tier1_complete', ?, NULL, ?, ?)
    """, [job_id, account_id, json.dumps(tier1_result), now, now])
    return job_id


def update_job_status(con, job_id: str, status: str, tier2_result: dict = None):
    con.execute("""
        UPDATE lookup_jobs
        SET status = ?, tier2_result = ?, updated_at = ?
        WHERE job_id = ?
    """, [status, json.dumps(tier2_result) if tier2_result else None,
          datetime.now(timezone.utc), job_id])


def get_job(con, job_id: str):
    row = con.execute("SELECT * FROM lookup_jobs WHERE job_id = ?", [job_id]).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in con.description]
    result = dict(zip(cols, row))
    result["tier1_result"] = json.loads(result["tier1_result"]) if result["tier1_result"] else None
    result["tier2_result"] = json.loads(result["tier2_result"]) if result["tier2_result"] else None
    return result


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def init_schema(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id BIGINT PRIMARY KEY,
            personaname VARCHAR,
            rank_tier INTEGER,
            wins INTEGER,
            losses INTEGER,
            last_updated TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS match_history (
            account_id BIGINT,
            match_id BIGINT,
            hero_id INTEGER,
            kills INTEGER,
            deaths INTEGER,
            assists INTEGER,
            duration INTEGER,
            start_time BIGINT,
            average_rank INTEGER,
            party_size INTEGER,
            game_mode INTEGER,
            lobby_type INTEGER,
            region INTEGER,
            win BOOLEAN,
            PRIMARY KEY (account_id, match_id)
        )
    """)

    con.execute("""
        CREATE OR REPLACE VIEW ranked_solo_matches AS
        SELECT * FROM match_history
        WHERE lobby_type = 7 AND party_size = 1
    """)

    # Global cache of fully parsed match payloads, keyed by match_id only.
    # This is the deduplication layer — shared across every account that
    # appears in a given match.
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw_match_cache (
            match_id BIGINT PRIMARY KEY,
            raw_json VARCHAR,
            fetched_at TIMESTAMP
        )
    """)

    # Records which match_ids constitute each account's "earliest N solo
    # ranked matches" selection for trajectory features. Persisting this
    # (rather than recomputing ad hoc) means the selection is stable even
    # if match_history later gets more rows added for that account.
    con.execute("""
        CREATE TABLE IF NOT EXISTS account_early_match_selection (
            account_id BIGINT,
            match_id BIGINT,
            sequence_order INTEGER,
            PRIMARY KEY (account_id, match_id)
        )
    """)

    # Derived table — rebuildable at any time from raw_match_cache +
    # account_early_match_selection via rebuild_parsed_early_matches().
    # item slots: motor-habit fingerprinting (see METHODOLOGY.md feature log)
    # lane_role / is_roaming: per-MATCH role context from OpenDota's replay
    # parser. Only populated when a match was deep-parsed, which requires
    # the replay to still exist on Valve's servers (~10 day retention) —
    # in practice this is null for most historical matches. See
    # role_classification.py for the fallback used when these are null.
    con.execute("""
        CREATE TABLE IF NOT EXISTS parsed_early_matches (
            account_id BIGINT,
            match_id BIGINT,
            sequence_order INTEGER,
            hero_id INTEGER,
            gold_per_min INTEGER,
            xp_per_min INTEGER,
            last_hits INTEGER,
            denies INTEGER,
            duration INTEGER,
            lane_role INTEGER,
            is_roaming BOOLEAN,
            item_0 INTEGER,
            item_1 INTEGER,
            item_2 INTEGER,
            item_3 INTEGER,
            item_4 INTEGER,
            item_5 INTEGER,
            backpack_0 INTEGER,
            backpack_1 INTEGER,
            backpack_2 INTEGER,
            start_time BIGINT,
            PRIMARY KEY (account_id, match_id)
        )
    """)

    # Derived, rebuildable via role_classification.classify_and_store_account_matches().
    # Raw component signals are persisted (not just role_score) so
    # economy_signal can be used standalone — e.g. a smurf playing a
    # support hero can still show a high economy_signal even while
    # correctly being labeled role='support'. See
    # skill_trajectory.compute_economic_dominance_score().
    con.execute("""
        CREATE TABLE IF NOT EXISTS match_role_scores (
            account_id BIGINT,
            match_id BIGINT,
            sequence_order INTEGER,
            role VARCHAR,
            role_source VARCHAR,
            role_score DOUBLE,
            economy_field_used VARCHAR,
            economy_signal DOUBLE,
            last_hits_signal DOUBLE,
            level_signal DOUBLE,
            PRIMARY KEY (account_id, match_id)
        )
    """)

    # Resumability ledger. stage: 'profile' | 'match_history' | 'early_matches'
    # status: 'success' | 'failed' | 'in_progress'
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_state (
            account_id BIGINT,
            stage VARCHAR,
            status VARCHAR,
            n_items INTEGER,
            last_updated TIMESTAMP,
            error_message VARCHAR,
            PRIMARY KEY (account_id, stage)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS heroes (
            hero_id INTEGER PRIMARY KEY,
            name VARCHAR,
            localized_name VARCHAR,
            primary_attr VARCHAR,
            attack_type VARCHAR,
            roles VARCHAR  -- JSON-encoded array, e.g. '["Carry","Nuker"]'
        )
    """)
    
    con.execute("""
        CREATE TABLE IF NOT EXISTS lookup_jobs (
            job_id VARCHAR PRIMARY KEY,
            account_id BIGINT,
            status VARCHAR,
            tier1_result VARCHAR,
            tier2_result VARCHAR,        
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)


# ---------------------------------------------------------------------
# Ingestion state ledger
# ---------------------------------------------------------------------

def record_ingestion_state(con, account_id: int, stage: str, status: str,
                             n_items: int = None, error_message: str = None):
    con.execute("""
        INSERT OR REPLACE INTO ingestion_state
        (account_id, stage, status, n_items, last_updated, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [account_id, stage, status, n_items, datetime.now(timezone.utc), error_message])


def get_ingestion_status(con, account_id: int, stage: str):
    result = con.execute("""
        SELECT status FROM ingestion_state WHERE account_id = ? AND stage = ?
    """, [account_id, stage]).fetchone()
    return result[0] if result else None


# ---------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------

def upsert_account(con, account_id: int, personaname: str, rank_tier: int,
                    wins: int, losses: int):
    con.execute("""
        INSERT OR REPLACE INTO accounts
        (account_id, personaname, rank_tier, wins, losses, last_updated)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [account_id, personaname, rank_tier, wins, losses, datetime.now(timezone.utc)])


# ---------------------------------------------------------------------
# match_history
# ---------------------------------------------------------------------

def upsert_match_history(con, account_id: int, matches: list):
    """
    `matches` is the raw list of match summary dicts from OpenDota's
    /players/{account_id}/matches endpoint. Returns 0 without error if
    the account has no matches matching the fetch filter (e.g. an account
    that never plays ranked solo queue).
    """
    rows = []
    for m in matches:
        is_radiant = m.get("player_slot", 0) < 128
        won = is_radiant == m.get("radiant_win")
        rows.append([
            account_id, m.get("match_id"), m.get("hero_id"), m.get("kills"),
            m.get("deaths"), m.get("assists"), m.get("duration"),
            m.get("start_time"), m.get("average_rank"), m.get("party_size"),
            m.get("game_mode"), m.get("lobby_type"), m.get("region"), won,
        ])

    if not rows:
        return 0

    con.executemany("""
        INSERT OR REPLACE INTO match_history
        (account_id, match_id, hero_id, kills, deaths, assists, duration,
         start_time, average_rank, party_size, game_mode, lobby_type, region, win)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    return len(rows)


# ---------------------------------------------------------------------
# raw_match_cache — the deduplicated global match store
# ---------------------------------------------------------------------

def get_cached_match(con, match_id: int):
    result = con.execute("""
        SELECT raw_json FROM raw_match_cache WHERE match_id = ?
    """, [match_id]).fetchone()
    return json.loads(result[0]) if result else None


def cache_raw_match(con, match_id: int, match_data: dict):
    con.execute("""
        INSERT OR REPLACE INTO raw_match_cache (match_id, raw_json, fetched_at)
        VALUES (?, ?, ?)
    """, [match_id, json.dumps(match_data), datetime.now(timezone.utc)])


# ---------------------------------------------------------------------
# account_early_match_selection + parsed_early_matches (derived)
# ---------------------------------------------------------------------

def select_earliest_solo_ranked_matches(con, account_id: int, n_earliest: int = 50) -> list:
    """
    Computes and persists which match_ids are this account's earliest N
    solo ranked matches, based on whatever is currently in match_history.
    Returns the list of match_ids in chronological order.
    """
    rows = con.execute("""
        SELECT match_id, start_time FROM match_history
        WHERE account_id = ? AND lobby_type = 7 AND party_size = 1
        ORDER BY start_time ASC
        LIMIT ?
    """, [account_id, n_earliest]).fetchall()

    con.execute("DELETE FROM account_early_match_selection WHERE account_id = ?", [account_id])
    insert_rows = [[account_id, match_id, i] for i, (match_id, _) in enumerate(rows)]
    if insert_rows:
        con.executemany("""
            INSERT INTO account_early_match_selection (account_id, match_id, sequence_order)
            VALUES (?, ?, ?)
        """, insert_rows)

    return [match_id for match_id, _ in rows]


def rebuild_parsed_early_matches(con, account_id: int) -> int:
    """
    Derived-table rebuild: given account_early_match_selection (which
    match_ids matter) and raw_match_cache (the actual parsed payloads),
    deterministically reconstructs parsed_early_matches for this account.

    Safe to call repeatedly — this table is never the source of truth,
    it's always recomputable from the two tables above. This means adding
    a new field we forgot to capture (e.g. lane_role) never requires
    refetching from the API — the raw payload is already cached, we just
    re-run this function to pull the new field out of it.
    """
    con.execute("DELETE FROM parsed_early_matches WHERE account_id = ?", [account_id])

    selection = con.execute("""
        SELECT match_id, sequence_order FROM account_early_match_selection
        WHERE account_id = ?
        ORDER BY sequence_order ASC
    """, [account_id]).fetchall()

    rows = []
    missing = 0
    for match_id, seq in selection:
        match_data = get_cached_match(con, match_id)
        if match_data is None:
            missing += 1
            continue

        player = next((p for p in match_data.get("players", [])
                        if p.get("account_id") == account_id), None)
        if player is None:
            continue

        rows.append([
            account_id, match_id, seq, player.get("hero_id"),
            player.get("gold_per_min"), player.get("xp_per_min"),
            player.get("last_hits"), player.get("denies"),
            player.get("duration"), player.get("lane_role"),
            player.get("is_roaming"),
            player.get("item_0"), player.get("item_1"), player.get("item_2"),
            player.get("item_3"), player.get("item_4"), player.get("item_5"),
            player.get("backpack_0"), player.get("backpack_1"), player.get("backpack_2"),
            match_data.get("start_time"),
        ])

    if rows:
        con.executemany("""
            INSERT INTO parsed_early_matches
            (account_id, match_id, sequence_order, hero_id, gold_per_min,
             xp_per_min, last_hits, denies, duration, lane_role, is_roaming,
             item_0, item_1, item_2, item_3, item_4, item_5,
             backpack_0, backpack_1, backpack_2, start_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    if missing:
        print(f"  Note: {missing} selected match_ids not yet in raw_match_cache "
              f"(not fetched yet, or fetch failed)")

    return len(rows)


# ---------------------------------------------------------------------
# match_role_scores (derived — via role_classification.py)
# ---------------------------------------------------------------------

def upsert_match_role_scores(con, rows: list):
    """
    `rows` is a list of lists matching the match_role_scores column order:
    [account_id, match_id, sequence_order, role, role_source, role_score,
     economy_field_used, economy_signal, last_hits_signal, level_signal]

    Clears this account's existing rows first, since role classification
    is fully rebuildable and should never accumulate stale rows from a
    prior run with a different n_earliest or classification logic.
    """
    if not rows:
        return

    account_id = rows[0][0]
    con.execute("DELETE FROM match_role_scores WHERE account_id = ?", [account_id])
    con.executemany("""
        INSERT INTO match_role_scores
        (account_id, match_id, sequence_order, role, role_source, role_score,
         economy_field_used, economy_signal, last_hits_signal, level_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)


def get_match_role_scores(con, account_id: int):
    return con.execute("""
        SELECT * FROM match_role_scores WHERE account_id = ? ORDER BY sequence_order
    """, [account_id]).fetchdf()


# ---------------------------------------------------------------------
# heroes reference
# ---------------------------------------------------------------------

def upsert_heroes(con, heroes: list):
    rows = [[h["id"], h.get("name"), h.get("localized_name"),
             h.get("primary_attr"), h.get("attack_type"),
             json.dumps(h.get("roles", []))] for h in heroes]
    con.executemany("""
        INSERT OR REPLACE INTO heroes (hero_id, name, localized_name, primary_attr, attack_type, roles)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    return len(rows)


if __name__ == "__main__":
    con = get_connection()
    init_schema(con)

    print("--- Table row counts ---")
    for table in ["accounts", "match_history", "raw_match_cache",
                  "account_early_match_selection", "parsed_early_matches",
                  "match_role_scores", "ingestion_state", "heroes"]:
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count}")

    con.close()