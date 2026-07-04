## Feature Idea Log

### Item Slot Fingerprinting (Family 2 — Discontinuity Detection)

Hypothesis: inventory slot placement for frequently-purchased items (e.g.
always slotting Blink Dagger in position 1) is a motor habit specific to
an individual player, distinct from item choice itself (which reflects
game knowledge and is shared across players of similar skill). A sudden,
sustained shift in slot placement habits — independent of any change in
skill level — is a candidate signal for account handoff (Class 2, 4) or
active boosting (Class 3), since a new person playing the account brings
their own habitual slot placement, not the original owner's.

Planned features:
- item_slot_consistency_score
- item_slot_changepoint_detected
- item_slot_changepoint_permanence
- signature_item_slot_mode

Requires full match history parsing (not just earliest-50), since the
changepoint can occur anywhere in an account's history. Deferred to the
Family 2 discontinuity feature build.

## Data Availability Constraints (discovered during pipeline development)

These constraints fundamentally shaped which features are viable and were
discovered empirically, not assumed in advance.

### The two-tier OpenDota data model
OpenDota exposes two distinct data tiers with very different availability:

- **Lightweight (match list endpoint)**: hero_id, kills, deaths, assists,
  duration, start_time, average_rank, party_size, game_mode, lobby_type,
  player_slot, radiant_win, leaver_status, version. Available for an
  account's ENTIRE lifetime history in a single API call, no per-match
  fetching, no expiration.
- **Deep (match detail endpoint)**: gold_per_min, xp_per_min, last_hits,
  denies, items, lane_role, is_roaming, region, net_worth, level. Only
  populated if the match was parsed by OpenDota's replay parser — which
  requires the replay file to still exist on Valve's servers. Valve
  retains replays for ~10 days by default (per OpenDota's own FAQ).

### Practical consequence
A match gets deep data ONLY if (a) it was parsed (typically triggered by a player in that match visiting
opendota.com), or (b) never — the replay is gone forever, and no amount
of retrying or requesting a parse will recover it. This is not a rate
limit or a fixable pipeline issue; it's a permanent ceiling on what's
knowable about old matches.

Empirically tested: requesting a fresh parse on a 2020 match (via
POST /request/{match_id}) returned a job ID but never actually parsed
the match — confirming the replay was already gone. Conversely, bulk
opportunistic GET-fetching across two test accounts' full histories
found real but low "already parsed by luck" coverage rates: 18.0% for
one account (1,317 total matches, 300 sampled) and 0.7% for another
(596 total, 300 sampled) — showing this varies a lot per account and
cannot be relied on as a primary signal source.

### Design implication: feature families split along this line
- **Family 1 (trajectory: Class 0 vs 1)** inherently needs an account's
  EARLIEST games specifically — no substitute is possible. This means
  Family 1 can only ever be deep-featured for accounts whose early
  history happened to get parsed at the time, or for brand-new accounts
  we choose to monitor going forward. For most historical accounts
  pulled after the fact, Family 1 deep features (last-hit slope, GPM
  slope, item timing) will be sparse or unavailable.
- **Family 2 (discontinuity: Classes 2-5)** has no such requirement — a
  change point can be detected anywhere in an account's full lifetime
  using ONLY lightweight win-rate/KDA data. This makes Family 2 the more
  robust, more universally-computable signal family, and it is fast
  enough to run synchronously in a live lookup (Tier 1), unlike Family 1.
- Deep-field discontinuity enrichments (e.g. region-arbitrage detection,
  item-slot fingerprinting) are best-effort: attempted opportunistically
  wherever cached/parseable data exists, never assumed to be complete.

## Feature Validation Notes

### Last-hit trajectory: raw count vs per-minute
Last-hits-per-minute, slopes for BOTH a normal account and a
smurf candidate collapsed toward near-zero, whereas raw counts had shown a
much clearer (and misleading) contrast. Absolute game-1 value (last hits
per minute in the very first game) held up better as a discriminative
signal than the slope did, at least at n=17-46 games. This suggests the
"flat learning curve" hypothesis may be weaker in practice than the
original IEEE baseline discussion assumed, and/or requires much larger
sample sizes or literally-brand-new accounts (not accounts with thousands
of prior unranked/turbo games) to show cleanly. Flagged for empirical
re-investigation once a larger EDA pass is done (Notebook 01).

### Role classification: hero-tag pitfall
Initial fallback role classifier used hero role tags including "Nuker" as
a core-leaning signal. This was wrong — "Nuker" is a combat-function tag
carried by many pure support heroes (Crystal Maiden, Bane, Lion all carry
it), causing nearly all support heroes to misclassify as core. Fixed by
restricting the hero-tag signal to Carry/Support only, with team-relative
economy (net_worth), last-hits, and level as weighted tiebreakers for
ambiguous/flex heroes. Final weighting: hero_prior 0.35, economy 0.25,
last_hits 0.20, level 0.20. Not yet empirically tuned against labelled
data — chosen by reasoning + spot-checking against heroes with known
real-world role reputations.

### Economic dominance as a role-independent signal
A smurf could deliberately play support heroes to depress the role-based
signal while still dominating economically. To catch this, economy_signal
(team-relative net worth/GPM percentile) is persisted and exposed as a
STANDALONE feature (economic_dominance_avg, economic_dominance_on_support_games),
independent of the binary role label — so a correctly-labeled "support"
game can still surface a high-dominance anomaly rather than having that
signal absorbed/hidden inside the role classification step.

### Change-point detection: rolling-average pitfall
Initial PELT implementation ran on a rolling-window-smoothed win-rate/KDA
series. This produced 20-50 "change points" per account spaced every
10-40 games — clearly noise, not real events. Root cause: rolling windows
overlap, creating strong autocorrelation between consecutive points, which
violates PELT's approximate-independence assumption and destabilizes its
sensitivity. Fixed by running PELT directly on the raw per-game series
(letting PELT's own segment-mean cost model handle smoothing internally),
combined with min_size=40 (minimum realistic segment length, informed by
Class 3's 20-50 game boosting window estimate) and a BIC-style calibrated
penalty (variance * log(n) * 2) instead of a fixed guessed constant.
Result on two test accounts: 0-1 real win-rate change points each,
consistent with genuine rare events rather than noise. Penalty constant
NOT yet validated against any known-true labelled event — this is a
priority for Week 5 hyperparameter tuning once real labels exist.

### Region-arbitrage: scope-limited by deep-data availability
`region` (server/cluster ID) exists only on the deep match endpoint, not
the lightweight one — confirmed by direct API inspection. This means
region-based features are subject to the same parse-coverage limitations
as all Family 1 deep features, NOT computable reliably across full
lifetime history. Deferred to best-effort enrichment status alongside
item-slot fingerprinting.

## Architecture Notes

### Single-process design (live lookup API)
DuckDB permits only one read-write connection per database file. The live
lookup API is therefore designed as a SINGLE process (FastAPI + an
in-process async worker loop), not separate API/worker processes, so
there is exactly one DuckDB connection. This also means api_client.py's
module-level rate limiter applies GLOBALLY across all concurrent users
automatically — no separate distributed rate-limiting infrastructure
needed at this scale.

### Tiered live lookup
A live account lookup cannot synchronously wait for full deep-feature
enrichment — per-match deep fetches are rate-limited to roughly one per
second, and a full history can be hundreds to thousands of matches. Tested:
fetching 300 matches took several minutes; at OpenDota's free-tier ~60
req/min shared limit, 100 concurrent users each needing ~50 calls would
require over an hour of serialized API time if done synchronously.

Fix: Tier 1 (synchronous, ~1-2 seconds) returns immediately using only
lightweight full-history data — currently the change-point discontinuity
signal. Tier 2 (asynchronous, backgrounded via a job queue) handles deep
enrichment and updates the job's result as it completes; the frontend
polls for updates. raw_match_cache's global (not per-user) keying means
match data fetched for one user's Tier 2 job is immediately free for any
other user whose lookup shares that match.