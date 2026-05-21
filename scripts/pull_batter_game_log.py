"""
pull_batter_game_log.py — MLB Stats API boxscores → batter_game_log

For every FINAL game we haven't logged yet, fetch its boxscore and
write one row per batter to `batter_game_log`. Backfills the season
history on first run, then runs daily (or post-game) to keep current.

Why boxscore-first instead of player-first?
  MLB Stats API has both gameLog (per-player) and boxscore (per-game)
  endpoints. A naive implementation hits gameLog for every player
  every night — that's ~1500 API calls a day.
  Boxscore is ~15 calls a day (one per game) and includes every
  player's batting line for that game. We save 100x bandwidth and
  rate-limit headroom.

Idempotency:
  Upsert keyed on (batter_id, game_id). Re-running on a game we've
  already logged just overwrites with the latest numbers (which is
  what we want if a stat ever gets corrected post-publication).

Coverage:
  - Pulls ALL final games in the current season that don't yet have a
    batter_game_log row (or have stale rows older than the game's
    most recent update).
  - Initial backfill: walks the entire season on first run. Subsequent
    runs only touch newly-finalized games.

Run cadence:
  Daily at 12 UTC (8 AM ET) is enough to capture every game from the
  previous day. Could go hourly during the slate if we want intra-game
  refresh, but final stats only change post-game so daily is fine.

MLB Stats API endpoints used:
  GET /api/v1/game/{gamePk}/boxscore   — batting lines for both teams

This script is *additive* to the existing pull stack. It does NOT
replace pull_batter_counting_stats.py — that fills the aggregate
batter_stats windows used by the projection model. This script fills
batter_game_log for streak-style UI.
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
REQUEST_DELAY = 0.4
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

# Final-ish statuses we'll log. Doesn't include in-progress; final
# stats can shift slightly during a game (every PA changes them).
FINAL_STATUSES = {
    "final", "game over", "completed early"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────

def safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def is_final(status: str | None) -> bool:
    if not status:
        return False
    return status.lower().strip() in FINAL_STATUSES


# ──────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────

def get_games_to_pull() -> list[dict]:
    """
    Final games in the current season that either:
      (a) have no batter_game_log rows yet, or
      (b) were updated more recently than our last pull for this game.

    Implementation: pull all final games in the season, then subtract
    games that already have batter_game_log rows. This is simple and
    correct for the typical "nightly backfill" workflow.

    For (b) — "stale on a corrected boxscore" — we don't currently
    detect that. If a stat correction lands, we'd need to manually
    re-pull. In practice MLB rarely corrects past lines materially.
    """
    season_start = f"{CURRENT_SEASON}-03-01"
    season_end = f"{CURRENT_SEASON}-11-30"

    # All final games in the season
    games_res = sb.table("games").select(
        "id, mlb_game_pk, game_date, status, "
        "home_team_id, away_team_id"
    ).gte("game_date", season_start) \
     .lte("game_date", season_end) \
     .execute()

    all_games = games_res.data or []
    final_games = [g for g in all_games if is_final(g.get("status"))]

    if not final_games:
        return []

    # Already-logged game_ids — pull in batches to avoid Supabase limits
    # on .in_() filter sizes.
    logged_ids: set[int] = set()
    BATCH = 500
    final_ids = [g["id"] for g in final_games]
    for i in range(0, len(final_ids), BATCH):
        chunk = final_ids[i : i + BATCH]
        # Distinct game_ids that already have at least one row
        res = sb.table("batter_game_log").select("game_id") \
            .in_("game_id", chunk).execute()
        for row in (res.data or []):
            logged_ids.add(row["game_id"])

    todo = [g for g in final_games if g["id"] not in logged_ids]
    return todo


def get_player_id_map() -> dict[int, dict]:
    """
    mlbam_id → {player row}. Lets us resolve boxscore mlbam_ids to our
    internal player_id for FK. We page through `players` because
    Supabase default-caps select() at 1000 rows.
    """
    by_mlbam: dict[int, dict] = {}
    page_size = 1000
    offset = 0
    while True:
        res = sb.table("players").select(
            "id, mlbam_id, name, bats, team_id, is_pitcher"
        ).range(offset, offset + page_size - 1).execute()
        page = res.data or []
        for p in page:
            if p.get("mlbam_id"):
                by_mlbam[p["mlbam_id"]] = p
        if len(page) < page_size:
            break
        offset += page_size
    return by_mlbam


# ──────────────────────────────────────────────────────────────
# MLB Stats API
# ──────────────────────────────────────────────────────────────

def fetch_boxscore(game_pk: int) -> dict | None:
    """Boxscore for a single game. Retries on transient errors."""
    url = f"{MLB_API}/game/{game_pk}/boxscore"
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=12)
        except Exception as e:
            log.warning("  boxscore net error gamePk=%d (try %d): %s",
                        game_pk, attempt + 1, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return None

        if r.status_code in (429, 500, 502, 503, 504):
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            log.warning("  boxscore gave up on gamePk=%d (status %d)",
                        game_pk, r.status_code)
            return None

        if not r.ok:
            log.debug("  boxscore gamePk=%d status %d", game_pk, r.status_code)
            return None

        try:
            return r.json()
        except Exception as e:
            log.warning("  boxscore non-JSON for gamePk=%d: %s", game_pk, e)
            return None
    return None


# ──────────────────────────────────────────────────────────────
# Extraction
# ──────────────────────────────────────────────────────────────

def extract_batter_lines(boxscore: dict, game_row: dict) -> list[dict]:
    """
    Parse an MLB boxscore JSON into a list of batter_game_log dicts
    (one per batter who recorded any plate appearance).

    Boxscore structure (relevant bits):
      teams.{away,home}.players.{ID_<mlbam>}
        .stats.batting → { plateAppearances, atBats, hits, ... }
        .battingOrder  → '100', '200', etc. (slot * 100; first 0 = subs)
        .position.code → '1' through '12' (1 = pitcher)
        .person.id     → mlbam_id

    We skip rows with no PA — bench guys who never came up shouldn't
    pollute the streak data.
    """
    out: list[dict] = []

    teams = boxscore.get("teams") or {}
    for side_key in ("away", "home"):
        side = teams.get(side_key) or {}
        team = side.get("team") or {}
        team_mlb_id = team.get("id")  # we'll resolve to our team_id elsewhere

        players = side.get("players") or {}
        for _player_key, p in players.items():
            person = p.get("person") or {}
            mlbam_id = safe_int(person.get("id"))
            if not mlbam_id:
                continue

            batting = (p.get("stats") or {}).get("batting") or {}
            pa = safe_int(batting.get("plateAppearances")) or 0
            if pa <= 0:
                # Didn't bat. Skip — keeps streak math from being
                # diluted by bench appearances.
                continue

            # batting order is e.g. "100" for slot 1, "201" for first
            # sub at slot 2. We just want the slot.
            bo_raw = p.get("battingOrder")
            batting_order = None
            if bo_raw:
                try:
                    bo_int = int(bo_raw)
                    batting_order = bo_int // 100
                    if batting_order < 1 or batting_order > 9:
                        batting_order = None
                except (ValueError, TypeError):
                    pass

            ab      = safe_int(batting.get("atBats"))      or 0
            hits    = safe_int(batting.get("hits"))        or 0
            doubles = safe_int(batting.get("doubles"))     or 0
            triples = safe_int(batting.get("triples"))     or 0
            hr      = safe_int(batting.get("homeRuns"))    or 0
            rbis    = safe_int(batting.get("rbi"))         or 0
            runs    = safe_int(batting.get("runs"))        or 0
            bb      = safe_int(batting.get("baseOnBalls")) or 0
            so      = safe_int(batting.get("strikeOuts"))  or 0
            hbp     = safe_int(batting.get("hitByPitch"))  or 0
            sb      = safe_int(batting.get("stolenBases")) or 0

            singles = hits - doubles - triples - hr
            total_bases = singles + 2 * doubles + 3 * triples + 4 * hr
            h_r_rbi = hits + runs + rbis

            out.append({
                # Caller resolves _mlbam_id → batter_id and team mlb_id
                # → team_id before the upsert.
                "_mlbam_id": mlbam_id,
                "_team_mlb_id": team_mlb_id,
                "_side": side_key,
                "game_id": game_row["id"],
                "game_date": game_row["game_date"],
                "batting_order": batting_order,
                "pa": pa,
                "ab": ab,
                "hits": hits,
                "doubles": doubles,
                "triples": triples,
                "hr": hr,
                "rbis": rbis,
                "runs": runs,
                "bb": bb,
                "so": so,
                "hbp": hbp,
                "sb": sb,
                "had_hit": hits >= 1,
                "had_hr":  hr >= 1,
                "had_rbi": rbis >= 1,
                "had_run": runs >= 1,
                "total_bases": total_bases,
                "h_r_rbi": h_r_rbi,
                "game_status": game_row.get("status"),
                "source": "mlb_api",
            })
    return out


# ──────────────────────────────────────────────────────────────
# Upsert
# ──────────────────────────────────────────────────────────────

def get_team_id_map() -> dict[int, int]:
    """mlb_id → internal team id."""
    res = sb.table("teams").select("id, mlb_id").execute()
    return {t["mlb_id"]: t["id"] for t in (res.data or [])}


def upsert_rows(rows: list[dict], players_by_mlbam: dict[int, dict],
                teams_by_mlbid: dict[int, int], game_row: dict) -> int:
    """
    Resolve foreign keys and upsert in one chunked call.
    Returns number of rows successfully written.
    """
    home_team_id = game_row.get("home_team_id")
    away_team_id = game_row.get("away_team_id")

    resolved: list[dict] = []
    for r in rows:
        player = players_by_mlbam.get(r["_mlbam_id"])
        if not player:
            # Player isn't in our players table yet. Skip — they'll get
            # filled in on the next pull_schedule or roster sync.
            continue

        # Skip pitchers from the batter log even if they had a PA
        # (interleague rare cases). Streak markets are for hitters.
        if player.get("is_pitcher"):
            continue

        team_id = teams_by_mlbid.get(r["_team_mlb_id"])
        # Opposing team and is_home derived from side
        if r["_side"] == "home":
            is_home = True
            opp_team_id = away_team_id
        else:
            is_home = False
            opp_team_id = home_team_id

        out = {
            "batter_id": player["id"],
            "game_id": r["game_id"],
            "game_date": r["game_date"],
            "team_id": team_id,
            "opponent_team_id": opp_team_id,
            "is_home": is_home,
            "batting_order": r["batting_order"],
            "bats": player.get("bats"),
            # vs_hand: opposing SP hand. We don't have it on the
            # boxscore directly; fill from game_row in the caller via
            # a separate lookup if you want. For now leave NULL —
            # available later through games.{home,away}_pitcher.throws.
            "vs_hand": None,
            "pa": r["pa"],
            "ab": r["ab"],
            "hits": r["hits"],
            "doubles": r["doubles"],
            "triples": r["triples"],
            "hr": r["hr"],
            "rbis": r["rbis"],
            "runs": r["runs"],
            "bb": r["bb"],
            "so": r["so"],
            "hbp": r["hbp"],
            "sb": r["sb"],
            "had_hit": r["had_hit"],
            "had_hr": r["had_hr"],
            "had_rbi": r["had_rbi"],
            "had_run": r["had_run"],
            "total_bases": r["total_bases"],
            "h_r_rbi": r["h_r_rbi"],
            "game_status": r["game_status"],
            "source": r["source"],
            "pulled_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        resolved.append(out)

    if not resolved:
        return 0

    # Upsert. We use on_conflict for the (batter_id, game_id) unique
    # constraint so re-pulls update rather than error out.
    try:
        sb.table("batter_game_log").upsert(
            resolved,
            on_conflict="batter_id,game_id",
        ).execute()
        return len(resolved)
    except Exception as e:
        log.error("  upsert failed for game_id=%d: %s", game_row["id"], e)
        return 0


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    log.info("🧅 Cebolla Lab — batter game-log pull starting (season %d)",
             CURRENT_SEASON)

    games = get_games_to_pull()
    if not games:
        log.info("No new final games to log. Done.")
        return

    log.info("Found %d games to pull", len(games))

    players_by_mlbam = get_player_id_map()
    log.info("Loaded %d players for FK resolution", len(players_by_mlbam))

    teams_by_mlbid = get_team_id_map()
    log.info("Loaded %d teams", len(teams_by_mlbid))

    total_rows = 0
    games_ok = 0
    games_skipped = 0

    for i, g in enumerate(games, 1):
        if i % 10 == 0:
            log.info("  Progress: %d/%d  (rows so far: %d)",
                     i, len(games), total_rows)

        try:
            box = fetch_boxscore(g["mlb_game_pk"])
        except Exception as e:
            log.warning("  fetch failed for gamePk=%d: %s", g["mlb_game_pk"], e)
            games_skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        if not box:
            games_skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        rows = extract_batter_lines(box, g)
        if not rows:
            games_skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        written = upsert_rows(rows, players_by_mlbam, teams_by_mlbid, g)
        total_rows += written
        if written > 0:
            games_ok += 1

        time.sleep(REQUEST_DELAY)

    log.info("Done. %d games processed, %d skipped. %d rows written.",
             games_ok, games_skipped, total_rows)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.exception("Fatal: %s", e)
        sys.exit(1)
