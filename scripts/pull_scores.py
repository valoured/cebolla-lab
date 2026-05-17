"""
pull_scores.py — Update live scores + inning state on games.

Runs on the same hourly slate cadence as pull_lineups. Hits MLB's linescore
endpoint (lighter than boxscore — we only need runs + inning).

For Final games: writes final score (no inning info).
For Live games: writes current score + inning state.
For Scheduled games: skips.
"""

import os
import sys
import logging
import requests
from datetime import datetime, timezone, date
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def get_todays_games() -> list[dict]:
    """Fetch today's games that aren't already Final."""
    today = date.today().isoformat()
    res = sb.table("games").select(
        "id, mlb_game_pk, status"
    ).eq("game_date", today).execute()
    return res.data or []


def fetch_linescore(game_pk: int) -> dict | None:
    """Fetch the linescore + abstract game state for a single game."""
    try:
        r = requests.get(
            f"{MLB_API}/game/{game_pk}/linescore",
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log.warning("  linescore fetch failed for game_pk=%s: %s", game_pk, e)
        return None


def fetch_status(game_pk: int) -> str | None:
    """Fetch the abstract game state ('Live' | 'Final' | 'Preview')."""
    try:
        r = requests.get(
            f"{MLB_API}/schedule?gamePk={game_pk}",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        dates = data.get("dates") or []
        if not dates:
            return None
        games = dates[0].get("games") or []
        if not games:
            return None
        return games[0].get("status", {}).get("detailedState")
    except requests.RequestException as e:
        log.warning("  status fetch failed for game_pk=%s: %s", game_pk, e)
        return None


def main():
    log.info("🧅 Cebolla Lab — score sync starting")

    games = get_todays_games()
    log.info("Found %d games today", len(games))

    updated = 0
    finalized = 0
    skipped = 0

    for game in games:
        game_pk = game["mlb_game_pk"]
        game_id = game["id"]
        current_status = (game.get("status") or "").lower()

        # Skip games already marked Final (their score won't change)
        if "final" in current_status or "game over" in current_status:
            skipped += 1
            continue

        # Fetch fresh status to detect transitions Scheduled→Live, Live→Final
        new_status = fetch_status(game_pk)
        if not new_status:
            skipped += 1
            continue

        status_lower = new_status.lower()
        is_live = "in progress" in status_lower or "manager challenge" in status_lower
        is_final = "final" in status_lower or "game over" in status_lower or "completed" in status_lower

        # Scheduled / Pre-Game / Warmup: just update status, no score yet
        if not is_live and not is_final:
            sb.table("games").update({
                "status": new_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", game_id).execute()
            continue

        # Fetch the linescore
        linescore = fetch_linescore(game_pk)
        if not linescore:
            skipped += 1
            continue

        teams = linescore.get("teams") or {}
        away_runs = teams.get("away", {}).get("runs")
        home_runs = teams.get("home", {}).get("runs")

        update_data = {
            "status": new_status,
            "away_score": away_runs,
            "home_score": home_runs,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if is_live:
            update_data["current_inning"] = linescore.get("currentInning")
            update_data["inning_state"] = linescore.get("inningState")
            log.info("  game %s LIVE: away=%s home=%s inning=%s %s",
                     game_id, away_runs, home_runs,
                     update_data["inning_state"],
                     update_data["current_inning"])
            updated += 1
        elif is_final:
            update_data["current_inning"] = None
            update_data["inning_state"] = None
            log.info("  game %s FINAL: away=%s home=%s",
                     game_id, away_runs, home_runs)
            finalized += 1

        sb.table("games").update(update_data).eq("id", game_id).execute()

    log.info("🧅 Sync complete: %d live, %d finalized, %d skipped",
             updated, finalized, skipped)


if __name__ == "__main__":
    main()
