"""
pull_batter_counting_stats.py — MLB Stats API → batter R/RBI per PA.

For every batter in the `players` table where is_pitcher=false, fetch
season and L14 (rolling 14-day) totals for:
  - PA, R, RBI

We don't get these from Savant because statcast events don't carry "runs
scored" or "RBI" as PA-level fields — you'd have to reconstruct from
base-state changes (post_bat_score - bat_score), which is fragile.
MLB Stats API has these as authoritative aggregates.

These rates are inputs to the H+R+RBI (HRR) projection model in
compute_projections.py. They UPDATE existing batter_stats rows (one per
window_type), filling the r_per_pa and rbi_per_pa columns added by
migration 15.

MLB Stats API endpoints used:
  GET /api/v1/people/{mlbam_id}/stats?stats=season&group=hitting&season=YYYY
  GET /api/v1/people/{mlbam_id}/stats?stats=byDateRange&group=hitting
      &startDate=YYYY-MM-DD&endDate=YYYY-MM-DD

Runs daily (overnight).
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta, date

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

CURRENT_SEASON = 2026
MLB_API = "https://statsapi.mlb.com/api/v1"
REQUEST_DELAY = 0.5      # base delay between requests (was 0.25 — too aggressive)
MAX_RETRIES = 3           # retry on rate limit / 5xx
RETRY_BACKOFF = 2.0       # exponential backoff multiplier
L14_DAYS = 14

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def get_batters() -> list[dict]:
    """All known batters (non-pitchers). Paginated because Supabase
    silently caps `.select()` at 1000 rows by default."""
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        res = sb.table("players").select("id, mlbam_id, name") \
            .eq("is_pitcher", False) \
            .range(offset, offset + page_size - 1) \
            .execute()
        page = res.data or []
        all_rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return [r for r in all_rows if r.get("mlbam_id")]


# ──────────────────────────────────────────────────────────────
# MLB Stats API fetchers
# ──────────────────────────────────────────────────────────────

def fetch_season(mlbam_id: int) -> dict | None:
    """Season hitting totals."""
    url = f"{MLB_API}/people/{mlbam_id}/stats"
    params = {
        "stats": "season",
        "group": "hitting",
        "season": CURRENT_SEASON,
    }
    return _fetch_stat(url, params, mlbam_id, "season")


def fetch_l14(mlbam_id: int) -> dict | None:
    """L14-day rolling hitting totals."""
    today = date.today()
    start = today - timedelta(days=L14_DAYS)
    url = f"{MLB_API}/people/{mlbam_id}/stats"
    params = {
        "stats": "byDateRange",
        "group": "hitting",
        "startDate": start.isoformat(),
        "endDate": today.isoformat(),
        "season": CURRENT_SEASON,
    }
    return _fetch_stat(url, params, mlbam_id, "l14")


def _fetch_stat(url: str, params: dict, mlbam_id: int, label: str) -> dict | None:
    """
    Shared MLB Stats API call + parse, with retry on rate limits and 5xx errors.

    On 429 (rate limit) or 5xx, retry with exponential backoff.
    On 404 or other 4xx, return None silently (player not found = expected).
    On empty splits, return None (player has no stats in this window = expected).
    On unexpected failure, LOG it so we can see what's happening (the old code
    silently swallowed everything, which masked rate-limit failures as
    "no data").
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=10)
        except Exception as e:
            log.warning("  fetch_%s network error for mlbam=%d (attempt %d): %s",
                        label, mlbam_id, attempt + 1, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None

        # Rate limit or server error → retry
        if r.status_code in (429, 500, 502, 503, 504):
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF ** attempt
                log.info("  fetch_%s got %d for mlbam=%d, backing off %.1fs",
                         label, r.status_code, mlbam_id, backoff)
                time.sleep(backoff)
                continue
            log.warning("  fetch_%s gave up on mlbam=%d after %d retries (status %d)",
                        label, mlbam_id, MAX_RETRIES, r.status_code)
            return None

        # 4xx (other than 429) → player not found, no point retrying
        if not r.ok:
            log.debug("  fetch_%s mlbam=%d returned %d", label, mlbam_id, r.status_code)
            return None

        # Success — parse
        try:
            data = r.json()
        except Exception as e:
            log.warning("  fetch_%s mlbam=%d returned non-JSON: %s", label, mlbam_id, e)
            return None

        stats_list = data.get("stats", [])
        if not stats_list:
            return None
        splits = stats_list[0].get("splits", [])
        if not splits:
            return None
        return splits[0].get("stat", {})

    return None


# ──────────────────────────────────────────────────────────────
# Transformation
# ──────────────────────────────────────────────────────────────

def extract_rates(stat: dict) -> dict:
    """
    Pull R, RBI, PA out of a stat block and compute per-PA rates.
    Returns a dict with the four fields ready to upsert.
    """
    pa = safe_int(stat.get("plateAppearances")) or 0
    runs = safe_int(stat.get("runs")) or 0
    rbis = safe_int(stat.get("rbi")) or 0

    r_per_pa = (runs / pa) if pa > 0 else None
    rbi_per_pa = (rbis / pa) if pa > 0 else None

    return {
        "runs":       runs,
        "rbis":       rbis,
        "r_per_pa":   round(r_per_pa, 4) if r_per_pa is not None else None,
        "rbi_per_pa": round(rbi_per_pa, 4) if rbi_per_pa is not None else None,
    }


def upsert_counting_stats(
    batter_id: int,
    window_type: str,
    counting: dict,
) -> None:
    """
    UPDATE existing batter_stats row (same batter_id + season + window_type +
    vs_hand='A') with the counting-stat fields. Doesn't touch the Savant
    columns (hits, hr, barrel%, etc.) that pull_savant.py owns.

    We only target vs_hand='A' since R/RBI per PA aren't available split
    by handedness from MLB Stats API for free.

    If no matching row exists, we SKIP rather than insert. The parent row
    is owned by pull_savant.py; if it hasn't created the row yet, we wait.
    Inserting a stub here would create orphaned rows missing all the
    Savant fields the projection model needs.
    """
    sb.table("batter_stats") \
        .update(counting) \
        .eq("batter_id", batter_id) \
        .eq("season", CURRENT_SEASON) \
        .eq("window_type", window_type) \
        .eq("vs_hand", "A") \
        .execute()


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def process_batter(b: dict) -> tuple[bool, bool]:
    """
    Pull season + L14 counting stats for one batter.
    Returns (season_ok, l14_ok).
    """
    mlbam_id = b["mlbam_id"]
    batter_id = b["id"]

    # Season
    season_stat = fetch_season(mlbam_id)
    season_ok = False
    if season_stat:
        rates = extract_rates(season_stat)
        # Skip if no PA at all (player hasn't appeared)
        if rates["runs"] or rates["rbis"]:
            upsert_counting_stats(batter_id, "season", rates)
            season_ok = True

    time.sleep(REQUEST_DELAY)

    # L14 (matches pull_savant.py window_type naming: "l14", not "l14g")
    l14_stat = fetch_l14(mlbam_id)
    l14_ok = False
    if l14_stat:
        rates = extract_rates(l14_stat)
        # Skip if no PA in window (didn't play in last 14 days)
        if rates["runs"] or rates["rbis"]:
            upsert_counting_stats(batter_id, "l14", rates)
            l14_ok = True

    return season_ok, l14_ok


def main():
    log.info("🧅 Cebolla Lab — batter counting-stats pull starting (season %d)",
             CURRENT_SEASON)

    batters = get_batters()
    log.info("Found %d batters", len(batters))

    if not batters:
        log.info("No batters to sync.")
        return

    season_success = 0
    l14_success = 0
    skipped = 0

    for i, b in enumerate(batters, 1):
        if i % 50 == 0:
            log.info("  Progress: %d/%d  (season=%d, l14=%d, skipped=%d)",
                     i, len(batters), season_success, l14_success, skipped)

        try:
            s_ok, l_ok = process_batter(b)
            if s_ok:
                season_success += 1
            if l_ok:
                l14_success += 1
            if not s_ok and not l_ok:
                skipped += 1
        except Exception as e:
            log.warning("  Error processing %s (mlbam=%d): %s",
                        b.get("name", "?"), b.get("mlbam_id", -1), e)
            skipped += 1

        time.sleep(REQUEST_DELAY)

    log.info("✓ Done. season=%d, l14=%d, skipped=%d / %d total",
             season_success, l14_success, skipped, len(batters))


if __name__ == "__main__":
    main()
