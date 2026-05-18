"""
pull_scores.py — Update live scores + inning state on games.

Runs on the same cron cadence as the rest of the slate pipeline. Hits MLB's
schedule endpoint with linescore hydration (1 request covers all of today's
games at once).

Status handling:
  - Scheduled/Pre-Game/Warmup → update status only, no score yet
  - Live (In Progress, Manager Challenge, Delayed Start, Suspended) → write score + inning state
  - Final/Game Over/Completed Early → write final score, clear inning info
  - Postponed/Cancelled → mark as such, no score
  - Stuck/Stale (game_time + 5h passed, still non-final) → force-finalize so the
    row doesn't haunt the slate forever
"""

import os
import logging
import requests
from datetime import datetime, timezone, timedelta, date

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"

# MLB API status classification.
#
# MLB has 100+ detailedState variants like "Delayed Start: Rain",
# "Delayed Start: Wet Grounds", "Delayed Start: Lightning", "Delayed: Wind", etc.
# Exact-match sets miss 95% of them. Instead we use:
#   1. abstractGameState (canonical: "Preview" | "Live" | "Final" | "Other")
#   2. Substring matching on detailedState for nuances
#
# Reference: https://statsapi.mlb.com/api/v1/gameStatus

# Terminal/final states — game is done, not coming back
TERMINAL_KEYWORDS = (
    "final", "game over", "completed", "postponed",
    "cancelled", "canceled", "forfeit",
)

# Active/live states — game is currently playing OR delayed mid-stream
LIVE_KEYWORDS = (
    "in progress", "manager challenge", "umpire review",
    "replay", "instant replay",
    "delayed",          # catches "Delayed", "Delayed Start: Rain", "Delayed: Wet Grounds", etc.
    "suspended",        # catches "Suspended", "Suspended: Rain", etc.
)

# Pre-game states — game hasn't started yet
PREGAME_KEYWORDS = (
    "scheduled", "pre-game", "pregame", "warmup", "status unknown",
)

# A game's row gets force-finalized if its scheduled start was more than this
# long ago and it's still showing non-terminal status. Average MLB game is ~3h,
# extras + rain delays can push to 4.5h; 6h is safe upper bound (allows for
# legitimate long delays before we assume the data is just stuck).
STALE_AFTER_HOURS = 6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def get_active_games() -> list[dict]:
    """
    Fetch all games that might still need a status update:
      - Today and tomorrow's games (to catch UTC date transitions)
      - Any non-terminal games from the past 2 days (catches games that ran late
        across the UTC date line OR got stuck Scheduled)
    """
    today = date.today()
    earliest = (today - timedelta(days=2)).isoformat()
    latest   = (today + timedelta(days=1)).isoformat()

    res = sb.table("games").select(
        "id, mlb_game_pk, status, game_date, game_time_utc"
    ).gte("game_date", earliest).lte("game_date", latest).execute()

    return res.data or []


def fetch_schedule_with_linescore(game_dates: list[str]) -> dict[int, dict]:
    """
    Hit /schedule once per unique date and return {game_pk: schedule_row}.
    The schedule response with hydrate=linescore contains score + inning state
    inline, so we don't need to also hit /linescore.
    """
    out = {}
    for d in game_dates:
        try:
            r = requests.get(
                f"{MLB_API}/schedule",
                params={
                    "date": d,
                    "sportId": 1,
                    "hydrate": "linescore",
                },
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
            for date_block in payload.get("dates", []):
                for g in date_block.get("games", []):
                    pk = g.get("gamePk")
                    if pk:
                        out[pk] = g
        except requests.RequestException as e:
            log.warning("schedule fetch failed for %s: %s", d, e)
    return out


def classify_status(detailed: str | None, abstract: str | None) -> str:
    """
    Map MLB's two status fields to one of: 'pregame' | 'live' | 'final' | 'unknown'.

    Strategy:
      1. detailedState substring match — handles all the "Delayed: Wet Grounds"
         "Delayed Start: Wind" etc. variants by keyword
      2. Fall back to abstractGameState which is canonical (Preview/Live/Final)

    Order matters: check final FIRST because "Postponed: Rain" shouldn't match
    "Delayed" or "Suspended" lower in the chain.
    """
    d = (detailed or "").strip().lower()
    a = (abstract or "").strip().lower()

    # ── Final / terminal — check first to catch "Postponed: Rain" before "delayed" etc. ──
    if any(k in d for k in TERMINAL_KEYWORDS):
        return "final"
    if a == "final":
        return "final"

    # ── Live / in-progress / delayed mid-game / suspended ──
    if any(k in d for k in LIVE_KEYWORDS):
        return "live"

    # ── Pre-game ──
    if any(k in d for k in PREGAME_KEYWORDS):
        return "pregame"

    # ── Fall back to abstract state ──
    if a == "live":
        return "live"
    if a == "preview":
        return "pregame"

    return "unknown"


def is_delay_status(detailed: str | None) -> bool:
    """
    True if MLB is reporting any kind of delay/suspension. Used to differentiate
    "actively delayed" from "actively in progress" so the frontend can apply
    amber glow vs red glow.
    """
    d = (detailed or "").strip().lower()
    return "delayed" in d or "suspended" in d


def parse_game_time(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        # Supabase stores as 'YYYY-MM-DDTHH:MM:SS+00:00' or similar
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def main():
    log.info("🧅 Cebolla Lab — score sync starting")

    games = get_active_games()
    log.info("Looking at %d games across past 2 days + tomorrow", len(games))

    if not games:
        log.info("No active games. Nothing to do.")
        return

    # Group by date for batched schedule fetches
    dates_to_fetch = sorted({g["game_date"] for g in games})
    schedule_map = fetch_schedule_with_linescore(dates_to_fetch)
    log.info("Fetched schedule for %d dates, got %d game_pks back",
             len(dates_to_fetch), len(schedule_map))

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(hours=STALE_AFTER_HOURS)

    updated, finalized, force_finalized, skipped = 0, 0, 0, 0

    for game in games:
        game_id = game["id"]
        game_pk = game["mlb_game_pk"]
        current = (game.get("status") or "").strip()
        current_class = classify_status(current, None)

        # Already terminal — skip
        if current_class == "final":
            skipped += 1
            continue

        sched = schedule_map.get(game_pk)
        update_data = {"updated_at": now.isoformat()}

        # ─── Fallback: force-finalize stale games ───
        # If MLB API doesn't return data for us OR the game is way past start time
        # and still non-terminal, force-finalize so the row doesn't haunt the slate.
        game_time = parse_game_time(game.get("game_time_utc"))
        is_way_past = game_time is not None and game_time < stale_threshold

        if sched is None:
            if is_way_past:
                log.info("  game %s force-finalize (no MLB data, %s past start)",
                         game_id, now - game_time)
                update_data["status"] = "Final"
                update_data["current_inning"] = None
                update_data["inning_state"] = None
                sb.table("games").update(update_data).eq("id", game_id).execute()
                force_finalized += 1
            else:
                skipped += 1
            continue

        # We have MLB data — use it
        status = sched.get("status") or {}
        detailed = status.get("detailedState")
        abstract = status.get("abstractGameState")
        new_class = classify_status(detailed, abstract)

        linescore = sched.get("linescore") or {}
        teams = linescore.get("teams") or {}
        away_runs = teams.get("away", {}).get("runs")
        home_runs = teams.get("home", {}).get("runs")

        # Build the update payload
        if new_class == "final":
            update_data["status"] = detailed or "Final"
            update_data["away_score"] = away_runs
            update_data["home_score"] = home_runs
            update_data["current_inning"] = None
            update_data["inning_state"] = None
            log.info("  game %s FINAL: away=%s home=%s",
                     game_id, away_runs, home_runs)
            finalized += 1

        elif new_class == "live":
            update_data["status"] = detailed or "In Progress"
            update_data["away_score"] = away_runs
            update_data["home_score"] = home_runs
            update_data["current_inning"] = linescore.get("currentInning")
            update_data["inning_state"] = linescore.get("inningState")

            # Distinguish delayed from in-progress for clearer logs
            if is_delay_status(detailed):
                log.info("  game %s DELAYED (%s): away=%s home=%s",
                         game_id, detailed, away_runs, home_runs)
            else:
                log.info("  game %s LIVE: away=%s home=%s inning=%s %s",
                         game_id, away_runs, home_runs,
                         update_data["inning_state"],
                         update_data["current_inning"])
            updated += 1

        elif new_class == "pregame":
            # Belt-and-suspenders force-finalize: pregame status but game time
            # is way past — MLB API is stuck, finalize anyway
            if is_way_past:
                log.info("  game %s force-finalize (pregame %s past start)",
                         game_id, now - game_time)
                update_data["status"] = "Final"
                update_data["current_inning"] = None
                update_data["inning_state"] = None
                force_finalized += 1
            else:
                update_data["status"] = detailed or "Scheduled"
                skipped += 1

        else:  # unknown
            update_data["status"] = detailed or current
            skipped += 1

        sb.table("games").update(update_data).eq("id", game_id).execute()

    log.info("🧅 Sync complete: %d live, %d finalized, %d force-finalized, %d skipped",
             updated, finalized, force_finalized, skipped)


if __name__ == "__main__":
    main()
