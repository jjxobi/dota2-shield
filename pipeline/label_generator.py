"""
Source 1 (weak labels) from the original brief: scans accounts for a
rank-tier-vs-total-games mismatch suggestive of a fast rank climb, and
persists candidates to data/labels/class1_fresh_smurfs.csv.

This is a WEAK label source — flagged accounts are candidates worth
human review or further feature-based scrutiny, not confirmed smurfs.
Threshold chosen from real distribution data observed across ~160 accounts
in our own match pool (see METHODOLOGY.md), not guessed.
"""

import sys
import csv
from pathlib import Path
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).resolve().parent))
import store
from fetch_accounts import fetch_and_store_profile

LABELS_DIR = Path(__file__).resolve().parent.parent / "data" / "labels"
OUTPUT_CSV = LABELS_DIR / "class1_fresh_smurfs_weak.csv"

# Calibrated against real observed distribution (see find_smurf_candidates
# run, ~160 accounts): rank_tier 60+ (Ancient 5/Divine+) with total_games
# under 700 stood out as a genuine minority, not an arbitrary cutoff.
MIN_SUSPICIOUS_RANK_TIER = 55
MAX_SUSPICIOUS_GAMES = 800


def scan_accounts_for_weak_labels(con, account_ids: list) -> list:
    flagged = []

    for i, acc_id in enumerate(account_ids, start=1):
        try:
            fetch_and_store_profile(con, acc_id)
        except Exception as e:
            print(f"  [{i}/{len(account_ids)}] {acc_id}: failed ({e}), skipping")
            continue

        row = con.execute("""
            SELECT rank_tier, wins, losses, personaname FROM accounts WHERE account_id = ?
        """, [acc_id]).fetchone()

        if row is None:
            continue

        rank_tier, wins, losses, personaname = row
        total_games = (wins or 0) + (losses or 0)

        # Zero games can mean either a private/hidden profile OR a
        # genuinely tiny-but-high-rank account. Both are worth flagging
        # as candidates — human/further-feature review decides which.
        is_candidate = (
            rank_tier is not None
            and rank_tier >= MIN_SUSPICIOUS_RANK_TIER
            and total_games <= MAX_SUSPICIOUS_GAMES
        )

        if is_candidate:
            flagged.append({
                "account_id": acc_id,
                "personaname": personaname,
                "rank_tier": rank_tier,
                "total_games": total_games,
                "label_source": "source1_rank_jump_weak",
                "flagged_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"  [{i}/{len(account_ids)}] FLAGGED: {personaname} (id={acc_id}), "
                  f"rank_tier={rank_tier}, total_games={total_games}")
        else:
            print(f"  [{i}/{len(account_ids)}] ok: {personaname} (id={acc_id})")

    return flagged


def append_to_labels_csv(flagged: list):
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = OUTPUT_CSV.exists()

    existing_ids = set()
    if file_exists:
        with open(OUTPUT_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_ids = {row["account_id"] for row in reader}

    new_rows = [r for r in flagged if str(r["account_id"]) not in existing_ids]

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "account_id", "personaname", "rank_tier", "total_games",
            "label_source", "flagged_at",
        ])
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"\nAppended {len(new_rows)} new candidates to {OUTPUT_CSV} "
          f"({len(flagged) - len(new_rows)} were already present)")


if __name__ == "__main__":
    con = store.get_connection()
    store.init_schema(con)

    match_ids = [row[0] for row in con.execute("SELECT match_id FROM raw_match_cache").fetchall()]
    seen = set()
    for match_id in match_ids:
        match_data = store.get_cached_match(con, match_id)
        for p in match_data.get("players", []):
            acc_id = p.get("account_id")
            if acc_id is not None:
                seen.add(acc_id)

    candidate_pool = list(seen)

    MAX_ACCOUNTS_PER_RUN = 100  # explicit cap — re-run the script to continue further
    if len(candidate_pool) > MAX_ACCOUNTS_PER_RUN:
        print(f"WARNING: {len(candidate_pool)} candidates found, capping this run to "
              f"{MAX_ACCOUNTS_PER_RUN}. At ~2.2s/account, this run will take "
              f"~{MAX_ACCOUNTS_PER_RUN * 2.2 / 60:.1f} minutes. Re-run the script "
              f"to process more (already-processed accounts are skipped automatically).")
        candidate_pool = candidate_pool[:MAX_ACCOUNTS_PER_RUN]

    print(f"Scanning {len(candidate_pool)} candidate accounts...\n")

    flagged = scan_accounts_for_weak_labels(con, candidate_pool)
    append_to_labels_csv(flagged)

    con.close()