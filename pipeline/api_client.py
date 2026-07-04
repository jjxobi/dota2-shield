"""
api_client.py
Shared, rate-limited, retrying HTTP client for all OpenDota API calls.

Every fetch script should route requests through this module rather than
calling requests.get() directly. This centralizes:
  - exponential backoff on rate limits (429) and transient server errors
  - a soft daily call budget tracker (OpenDota free tier: 50k calls/month,
    60 requests/minute)
  - consistent timeout and error handling

This is intentionally simple (no external queue/broker) since at our
current scale a single-process client with disciplined pacing is
sufficient. If we ever run this across multiple parallel workers, this
module is where a shared distributed rate limiter would need to replace
the in-process one.
"""

import requests
import time
from datetime import datetime

OPENDOTA_BASE = "https://api.opendota.com/api"

# Free anonymous tier: 60 requests/minute. We stay comfortably under this.
MIN_SECONDS_BETWEEN_CALLS = 1.1
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2  # 2, 4, 8, 16, 32 seconds on successive retries

_last_call_time = 0.0
_call_count = 0
_daily_remaining = None


def _respect_rate_limit():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)
    _last_call_time = time.time()

def get_daily_remaining():
    """Returns the last known daily quota remaining, or None if unknown yet."""
    return _daily_remaining

def get(path: str, params: dict = None) -> dict:
    """
    Make a rate-limited, retrying GET request to the OpenDota API.
    `path` should start with '/', e.g. '/players/12345'.
    Raises requests.exceptions.HTTPError if all retries are exhausted.
    """
    global _call_count
    url = f"{OPENDOTA_BASE}{path}"

    for attempt in range(1, MAX_RETRIES + 1):
        _respect_rate_limit()
        _call_count += 1

        try:
            response = requests.get(url, params=params, timeout=15)

            if response.status_code == 429:
                wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                print(f"  [rate limited] attempt {attempt}/{MAX_RETRIES}, backing off {wait}s...")
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                print(f"  [server error {response.status_code}] attempt {attempt}/{MAX_RETRIES}, backing off {wait}s...")
                time.sleep(wait)
                continue

            response.raise_for_status()
            global _daily_remaining
            _daily_remaining = response.headers.get("X-Rate-Limit-Remaining-Day")
            return response.json()

        except requests.exceptions.Timeout:
            wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            print(f"  [timeout] attempt {attempt}/{MAX_RETRIES}, backing off {wait}s...")
            time.sleep(wait)

    raise requests.exceptions.HTTPError(f"Failed after {MAX_RETRIES} retries: {url}")


def get_call_count() -> int:
    """Total calls made in this process — useful for budget monitoring."""
    return _call_count