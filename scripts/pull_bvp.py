"""
pull_bvp.py — Career batter-vs-pitcher splits.

For each game in today's slate:
  - Identify the home + away starting pitchers
  - Identify every batter in confirmed/projected lineups
  - For each (batter, pitcher) tuple, hit MLB Stats API for career splits
  - Upsert into bvp_history

Endpoint:
  GET /api/v1/people/{batter_mlbam}/stats
      ?stats=vsPlayerTotal
      &opposingPlayerId={pitcher_mlbam}
      &group=hitting
      &sportId=1

Returns: {stats: [{splits: [{stat: {plateAppearances, atBats, hits, homeRuns,
                                    baseOnBalls, strikeOuts, avg, ops}}]}]}

Notes:
  - 'vsPlayerTotal' is career-aggregated. For per-season splits, 'vsPlayer'
    would return one row per season — we use the career total since the
    UI shows a single H/PA number.
  - Many (batter, pitcher) pairs have ZERO career PAs. The API returns
    an empty splits list in that case. We skip those — no row written.
  - Rate-limited politely: 0.4s between calls + retry on 429/5xx.
  - Slate has ~12-15 games × 18 batters × 1 opposing starter = ~250 calls.
    Total runtime ~2 min with the delay.

Run order: pull_lineups + pull_schedule must already have set today's
starting pitchers and lineups before this fires.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pull_bvp")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"
REQUEST_DELAY_SEC = 0.4
MAX_RETRIES = 3


def get_today_iso():
    """ET-relative slate date (matches the rest of the pipeline)."""
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


def fetch_vs_player_total(batter_mlbam: int, pitcher_mlbam: int) -> dict | None:
    """
    Fetch career batter-vs-pitcher splits.

    Returns the stat dict (plateAppearances, atBats, hits, homeRuns, etc.)
    or None if the pair has no career PAs.

    Retries on 429 / 5xx with exponential backoff. Logs and returns None
    for other failures so one bad batter doesn't kill the whole run.
    """
    url = f"{MLB_API}/people/{batter_mlbam}/stats"
    params = {
        "stats": "vsPlayerTotal",
        "opposingPlayerId": pitcher_mlbam,
        "group": "hitting",
        "sportId": "1",
    }
    delay = 2.0
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=15)
        except requests.RequestException as e:
            log.warning("  network error b=%d p=%d (attempt %d): %s",
                        batter_mlbam, pitcher_mlbam, attempt + 1, e)
            time.sleep(delay)
            delay *= 2
            continue

        if r.status_code in (429, 500, 502, 503, 504):
            log.warning("  retryable status %d b=%d p=%d (attempt %d, sleeping %.1fs)",
                        r.status_code, batter_mlbam, pitcher_mlbam, attempt + 1, delay)
            time.sleep(delay)
            delay *= 2
            continue

        if not r.ok:
            log.warning("  non-OK status %d b=%d p=%d", r.status_code, batter_mlbam, pitcher_mlbam)
            return None

        data = r.json() or {}
        stats_list = data.get("stats", [])
        for s in stats_list:
            for split in s.get("splits", []):
                stat = split.get("stat", {})
                if stat:
                    return stat
        return None   # no splits = no career matchup

    log.warning("  giving up on b=%d p=%d after %d retries",
                batter_mlbam, pitcher_mlbam, MAX_RETRIES)
    return None


def parse_numeric(stat: dict, key: str) -> int | None:
    """Pull an int from the stat blob, tolerating missing/string values."""
    v = stat.get(key)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def parse_decimal(stat: dict, key: str) -> float | None:
    """Pull a numeric (AVG, OPS) from the stat blob. MLB returns strings."""
    v = stat.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_today_pitcher_batter_pairs(date_iso: str) -> list[tuple[int, int, int, int]]:
    """
    For today's games, return all (batter_internal_id, batter_mlbam_id,
    pitcher_internal_id, pitcher_mlbam_id) tuples. Each lineup batter
    is paired with the OPPOSING team's starting pitcher.

    Skips games with no starting pitcher set, or empty lineups.
    """
    games_res = sb.table("games") \
        .select("id, home_team_id, away_team_id, home_pitcher_id, away_pitcher_id") \
        .eq("game_date", date_iso) \
        .execute()
    games = games_res.data or []
    if not games:
        log.warning("No games for %s", date_iso)
        return []

    # Gather pitcher MLBAM lookup for every pitcher in slate
    pitcher_ids = list({
        pid for g in games
        for pid in (g.get("home_pitcher_id"), g.get("away_pitcher_id"))
        if pid is not None
    })
    if not pitcher_ids:
        log.warning("No starting pitchers set yet for %s", date_iso)
        return []

    p_res = sb.table("players").select("id, mlbam_id") \
        .in_("id", pitcher_ids).execute()
    pitcher_mlbam = {p["id"]: p["mlbam_id"] for p in (p_res.data or [])
                     if p.get("mlbam_id") is not None}

    pairs = []
    for g in games:
        gid = g["id"]
        # Fetch this game's lineups
        l_res = sb.table("lineups").select("player_id, team_id") \
            .eq("game_id", gid).execute()
        lineups = l_res.data or []
        if not lineups:
            continue

        # Need batter MLBAM ids
        batter_ids = list({l["player_id"] for l in lineups if l.get("player_id")})
        if not batter_ids:
            continue
        b_res = sb.table("players").select("id, mlbam_id") \
            .in_("id", batter_ids).execute()
        batter_mlbam = {b["id"]: b["mlbam_id"] for b in (b_res.data or [])
                        if b.get("mlbam_id") is not None}

        for l in lineups:
            bid = l["player_id"]
            team = l["team_id"]
            # Opposing starting pitcher
            opp_pitcher_id = (g["away_pitcher_id"] if team == g["home_team_id"]
                              else g["home_pitcher_id"])
            if not opp_pitcher_id:
                continue
            b_mlb = batter_mlbam.get(bid)
            p_mlb = pitcher_mlbam.get(opp_pitcher_id)
            if not b_mlb or not p_mlb:
                continue
            pairs.append((bid, b_mlb, opp_pitcher_id, p_mlb))

    return pairs


def upsert_bvp_row(batter_id: int, pitcher_id: int, stat: dict):
    """Upsert one BvP row. The UNIQUE(batter_id, pitcher_id) handles dedup."""
    pa = parse_numeric(stat, "plateAppearances")
    ab = parse_numeric(stat, "atBats")
    if pa is None and ab is None:
        # No usable matchup data
        return False

    payload = {
        "batter_id": batter_id,
        "pitcher_id": pitcher_id,
        "pa": pa,
        "ab": ab,
        "hits": parse_numeric(stat, "hits"),
        "hr": parse_numeric(stat, "homeRuns"),
        "bb": parse_numeric(stat, "baseOnBalls"),
        "so": parse_numeric(stat, "strikeOuts"),
        "avg": parse_decimal(stat, "avg"),
        "ops": parse_decimal(stat, "ops"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb.table("bvp_history").upsert(
        payload, on_conflict="batter_id,pitcher_id"
    ).execute()
    return True


def main():
    today = get_today_iso()
    log.info("🧅 BvP pull — slate %s", today)

    pairs = get_today_pitcher_batter_pairs(today)
    if not pairs:
        log.warning("No (batter, pitcher) pairs to fetch — skipping run.")
        return

    log.info("Fetching career BvP for %d (batter, pitcher) pairs", len(pairs))

    fetched = 0
    written = 0
    empty = 0
    failed = 0

    for i, (bid, b_mlb, pid, p_mlb) in enumerate(pairs, 1):
        if i % 25 == 0:
            log.info("  progress: %d/%d (written=%d, empty=%d, failed=%d)",
                     i, len(pairs), written, empty, failed)

        stat = fetch_vs_player_total(b_mlb, p_mlb)
        fetched += 1

        if stat is None:
            empty += 1
            time.sleep(REQUEST_DELAY_SEC)
            continue

        try:
            if upsert_bvp_row(bid, pid, stat):
                written += 1
            else:
                empty += 1
        except Exception as e:
            log.warning("  upsert failed b=%d p=%d: %s", bid, pid, e)
            failed += 1

        time.sleep(REQUEST_DELAY_SEC)

    log.info("✓ BvP pull complete — fetched=%d, written=%d, empty=%d, failed=%d",
             fetched, written, empty, failed)


if __name__ == "__main__":
    main()
