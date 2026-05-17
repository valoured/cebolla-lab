"""
pull_pitcher_stats.py — Pull clean season pitching stats from MLB Stats API.

For every pitcher in our `players` table where is_pitcher=true, fetch:
  - IP, BF, HR allowed, BB, SO, ER
  - ERA, FIP (computed), WHIP, HR/9, K/9, BB/9, HR/PA

MLB Stats API endpoint:
  GET /api/v1/people/{mlbam_id}/stats?stats=season&group=pitching&season=YYYY

This replaces the broken `compute_pitcher_hr_per_9` in compute_projections.py
which was incorrectly summing PAs across pitch types.

Runs once daily (overnight).
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

CURRENT_SEASON = 2026
MLB_API = "https://statsapi.mlb.com/api/v1"
REQUEST_DELAY = 0.3   # polite to MLB's free API

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def get_pitchers() -> list[dict]:
    """All pitchers we know about."""
    res = sb.table("players").select("id, mlbam_id, name, throws") \
        .eq("is_pitcher", True).execute()
    return res.data


def fetch_pitcher_season(mlbam_id: int) -> dict | None:
    """MLB Stats API season pitching stats for one pitcher."""
    url = f"{MLB_API}/people/{mlbam_id}/stats"
    params = {
        "stats": "season",
        "group": "pitching",
        "season": CURRENT_SEASON,
    }
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
        log.warning("  Failed for mlbam=%d: %s", mlbam_id, e)
        return None


def parse_ip(ip_str) -> float:
    """
    MLB API returns IP as a string like '125.2' meaning 125 and 2/3 innings.
    Convert to true decimal: 125.2 → 125.667.
    """
    if ip_str is None:
        return 0.0
    try:
        s = str(ip_str)
        if "." in s:
            whole, frac = s.split(".", 1)
            whole = int(whole)
            frac = int(frac[0])  # 0, 1, or 2 outs
            return whole + (frac / 3.0)
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def safe_num(v) -> float | None:
    if v is None or v == "-.--" or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def transform(stat: dict, pitcher_id: int, throws: str | None) -> dict:
    """Convert MLB Stats API response into our pitcher_stats row."""
    ip = parse_ip(stat.get("inningsPitched"))
    bf = safe_int(stat.get("battersFaced")) or 0
    hr = safe_int(stat.get("homeRuns")) or 0
    hits_allowed = safe_int(stat.get("hits")) or 0

    hr_per_9 = (hr / ip * 9) if ip > 0 else None
    hr_per_pa = (hr / bf) if bf > 0 else None
    hits_per_9 = (hits_allowed / ip * 9) if ip > 0 else None
    hit_per_pa = (hits_allowed / bf) if bf > 0 else None

    return {
        "pitcher_id": pitcher_id,
        "season": CURRENT_SEASON,
        "window_type": "season",
        "games_started": safe_int(stat.get("gamesStarted")),
        "innings_pitched": round(ip, 1) if ip else None,
        "batters_faced": bf if bf else None,
        "hr_allowed": hr,
        "hits_allowed": hits_allowed,
        "bb": safe_int(stat.get("baseOnBalls")),
        "so": safe_int(stat.get("strikeOuts")),
        "er": safe_int(stat.get("earnedRuns")),
        "era": safe_num(stat.get("era")),
        "whip": safe_num(stat.get("whip")),
        "hr_per_9": round(hr_per_9, 2) if hr_per_9 is not None else None,
        "k_per_9": safe_num(stat.get("strikeoutsPer9Inn")),
        "bb_per_9": safe_num(stat.get("walksPer9Inn")),
        "hr_per_pa": round(hr_per_pa, 4) if hr_per_pa is not None else None,
        "hits_per_9": round(hits_per_9, 2) if hits_per_9 is not None else None,
        "hit_per_pa": round(hit_per_pa, 4) if hit_per_pa is not None else None,
        "baa": safe_num(stat.get("avg")),
        "throws": throws,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    log.info("🧅 Cebolla Lab — pitcher stats sync starting (season %d)", CURRENT_SEASON)

    pitchers = get_pitchers()
    log.info("Found %d known pitchers", len(pitchers))

    if not pitchers:
        log.info("No pitchers to sync.")
        return

    rows = []
    no_data = 0
    success = 0

    for i, p in enumerate(pitchers, 1):
        if i % 50 == 0:
            log.info("  Progress: %d / %d", i, len(pitchers))

        stat = fetch_pitcher_season(p["mlbam_id"])
        if not stat:
            no_data += 1
            time.sleep(REQUEST_DELAY)
            continue

        try:
            row = transform(stat, p["id"], p.get("throws"))
            # Skip pitchers with very little MLB time this year
            if (row["batters_faced"] or 0) < 20:
                no_data += 1
            else:
                rows.append(row)
                success += 1
        except Exception as e:
            log.warning("  Transform failed for %s: %s", p["name"], e)

        time.sleep(REQUEST_DELAY)

    log.info("Prepared %d pitcher_stats rows (%d had no/insufficient data)",
             len(rows), no_data)

    # Upsert in chunks
    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        sb.table("pitcher_stats").upsert(
            chunk,
            on_conflict="pitcher_id,season,window_type",
        ).execute()
        written += len(chunk)

    log.info("✓ Wrote %d pitcher_stats rows", written)

    # Diagnostic: print a few notable pitchers
    if rows:
        # Sort by HR/9
        non_null = [r for r in rows if r["hr_per_9"] is not None]
        non_null.sort(key=lambda r: r["hr_per_9"], reverse=True)
        log.info("─── Top 5 HR/9 (gopher prone) ───")
        for r in non_null[:5]:
            log.info("   pitcher_id=%d  IP=%.1f  HR=%d  HR/9=%.2f",
                     r["pitcher_id"], r["innings_pitched"] or 0,
                     r["hr_allowed"], r["hr_per_9"])
        log.info("─── Bottom 5 HR/9 (HR-suppressing) ───")
        for r in non_null[-5:]:
            log.info("   pitcher_id=%d  IP=%.1f  HR=%d  HR/9=%.2f",
                     r["pitcher_id"], r["innings_pitched"] or 0,
                     r["hr_allowed"], r["hr_per_9"])

    log.info("🧅 Pitcher stats sync complete")


if __name__ == "__main__":
    main()
