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
REQUEST_DELAY = 0.25  # polite to MLB's free API
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
    """All known batters (non-pitchers)."""
    res = sb.table("players").select("id, mlbam_id, name") \
        .eq("is_pitcher", False).execute()
    return [r for r in res.data if r.get("mlbam_id")]


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
    """Shared MLB Stats API call + parse."""
    try:
        r = requests.get(url, params=params, timeout=10)
        if not r.ok:
            return None
        data = r.json()
        stats_list = data.get("stats", [])
        if not stats_list:
            return None
        splits = stats_list[0].get("splits", [])
        if not splits:
            return None
        return splits[0].get("stat", {})
    except Exception as e:
        log.debug("  fetch_%s failed for mlbam=%d: %s", label, mlbam_id, e)
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

    If no row exists yet (rare — pull_savant runs first), we insert a
    minimal row to avoid losing the counting stats.
    """
    # Try update first
    res = sb.table("batter_stats") \
        .update(counting) \
        .eq("batter_id", batter_id) \
        .eq("season", CURRENT_SEASON) \
        .eq("window_type", window_type) \
        .eq("vs_hand", "A") \
        .execute()

    if not res.data:
        # No matching row — insert a minimal stub. pull_savant will
        # fill in the rest next time it runs.
        sb.table("batter_stats").insert({
            "batter_id": batter_id,
            "season": CURRENT_SEASON,
            "window_type": window_type,
            "vs_hand": "A",
            **counting,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()


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

    # L14
    l14_stat = fetch_l14(mlbam_id)
    l14_ok = False
    if l14_stat:
        rates = extract_rates(l14_stat)
        # Skip if no PA in window (didn't play in last 14 days)
        if rates["runs"] or rates["rbis"]:
            upsert_counting_stats(batter_id, "l14g", rates)
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
