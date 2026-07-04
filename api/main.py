"""
Single-process API server. Owns the one DuckDB read-write connection —
critical, since DuckDB does not support concurrent multi-process write
access. The "background worker" is an asyncio task inside THIS process,
not a separate process, specifically to keep everything on one connection
and let api_client's module-level rate limiter apply globally across all
concurrent users, not per-user.
"""

import asyncio
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from concurrent.futures import ThreadPoolExecutor

sys.path.append(str(Path(__file__).resolve().parent.parent / "pipeline"))
sys.path.append(str(Path(__file__).resolve().parent))
import store
from tier1_lookup import run_tier1_lookup

app = FastAPI()
job_queue: asyncio.Queue = asyncio.Queue()
executor = ThreadPoolExecutor(max_workers=1)  # one worker — respects shared rate limit
con = None  # set on startup, single connection for the whole process


@app.on_event("startup")
async def startup():
    global con
    con = store.get_connection()
    store.init_schema(con)
    asyncio.create_task(tier2_worker_loop())


def _blocking_tier1(account_id: int) -> dict:
    return run_tier1_lookup(con, account_id)


def _blocking_tier2(account_id: int) -> dict:
    # Placeholder — deep-fetch enrichment goes here once we build it
    # (region arbitrage, trajectory features, etc.)
    return {"status": "tier2_not_yet_implemented"}


async def tier2_worker_loop():
    """
    Single sequential worker. Because this is the only thing making
    deep-fetch calls, and api_client's rate limiter is process-global,
    every user's Tier 2 job is naturally throttled together — no user
    can starve another user's request budget.
    """
    loop = asyncio.get_event_loop()
    while True:
        job_id, account_id = await job_queue.get()
        store.update_job_status(con, job_id, "tier2_processing")
        try:
            result = await loop.run_in_executor(executor, _blocking_tier2, account_id)
            store.update_job_status(con, job_id, "complete", result)
        except Exception as e:
            store.update_job_status(con, job_id, "failed", {"error": str(e)})


@app.post("/lookup/{account_id}")
async def lookup(account_id: int):
    loop = asyncio.get_event_loop()
    try:
        tier1_result = await loop.run_in_executor(executor, _blocking_tier1, account_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tier 1 lookup failed: {e}")

    job_id = store.create_lookup_job(con, account_id, tier1_result)
    await job_queue.put((job_id, account_id))

    return {"job_id": job_id, "tier1": tier1_result, "status": "tier1_complete"}


@app.get("/lookup/status/{job_id}")
async def lookup_status(job_id: str):
    job = store.get_job(con, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job