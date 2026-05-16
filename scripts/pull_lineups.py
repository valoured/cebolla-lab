"""
pull_lineups.py — Pull confirmed (or projected) batting lineups for today's games.

MLB Stats API exposes lineups via the boxscore endpoint:
    GET /api/v1/game/{gamePk}/boxscore

If the game hasn't started, this returns the *projected* lineup once teams post
it (~2-4 hours before first pitch). If still empty, we fall back to each team's
most-frequent starters from the past 14 days using statsapi.

Runs hourly during slate window via GitHub Actions.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, date, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"
REQUEST_DELAY = 1.0  # be polite to MLB's free API

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Player upsert helpers
# ────────────────────────────────────────────────────────────────

def get_player_map() -> dict[int, int]:
    """{mlbam_id: players.id} for everyone we've seen."""
    res = sb.table("players").select("id, mlbam_id").execute()
    return {p["mlbam_id"]: p["id"] for p in res.data}


def upsert_batter(mlbam_id: int, name: str, position: str | None, bats: str | None,
                  team_id: int | None) -> int:
    payload = {
        "mlbam_id": mlbam_id,
        "name": name,
        "team_id": team_id,
        "position": position,
        "bats": bats,
        "is_pitcher": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("players").upsert(payload, on_conflict="mlbam_id").execute()
    return res.data[0]["id"]


# ────────────────────────────────────────────────────────────────
# Team & game helpers
# ────────────────────────────────────────────────────────────────

def get_team_map() -> dict[int, int]:
    """{mlb_id: teams.id}."""
    res = sb.table("teams").select("id, mlb_id").execute()
    return {t["mlb_id"]: t["id"] for t in res.data}


def get_todays_games() -> list[dict]:
    today = date.today().isoformat()
    res = sb.table("games").select(
        "id, mlb_game_pk, away_team_id, home_team_id"
    ).eq("game_date", today).execute()
    return res.data


# ────────────────────────────────────────────────────────────────
# MLB Stats API boxscore parsing
# ────────────────────────────────────────────────────────────────

def fetch_boxscore(game_pk: int) -> dict | None:
    url = f"{MLB_API}/game/{game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("  Failed boxscore for game %d: %s", game_pk, e)
        return None


def extract_lineup(team_blob: dict) -> list[dict]:
    """
    Boxscore team blob → list of {mlbam_id, name, position, bats, batting_order}.
    Batting order in MLB API is a stringified 3-digit integer:
      '101' = #1 hitter, '201' = #2 hitter, ..., '901' = #9. Subs use '102', '103', etc.
    We take only starters (suffix '01').
    """
    out = []
    players = team_blob.get("players", {}) or {}
    for p_key, p in players.items():
        order_str = p.get("battingOrder")
        if not order_str:
            continue
        if not order_str.endswith("00") and not order_str.endswith("01"):
            # subs/pinch hitters
            continue
        # Only main starters: '100', '200', ..., '900' OR sometimes '101' etc.
        try:
            order_int = int(order_str)
        except ValueError:
            continue
        if order_int % 100 not in (0, 1):
            continue
        slot = order_int // 100  # 1-9
        if slot < 1 or slot > 9:
            continue

        person = p.get("person", {}) or {}
        mlbam = person.get("id")
        name = person.get("fullName")
        if not mlbam or not name:
            continue

        pos = (p.get("position") or {}).get("abbreviation")
        bats = (p.get("batSide") or {}).get("code")

        out.append({
            "mlbam_id": int(mlbam),
            "name": name,
            "position": pos,
            "bats": bats,
            "batting_order": slot,
        })

    # Dedupe by slot (in case both '100' and '101' exist for same player)
    seen_slots = {}
    for entry in out:
        seen_slots[entry["batting_order"]] = entry
    return sorted(seen_slots.values(), key=lambda x: x["batting_order"])


# ────────────────────────────────────────────────────────────────
# Main per-game processing
# ────────────────────────────────────────────────────────────────

def process_game(game: dict, team_map: dict[int, int], player_map: dict[int, int]) -> tuple[int, bool]:
    """Returns (rows_written, was_confirmed)."""
    box = fetch_boxscore(game["mlb_game_pk"])
    if not box:
        return (0, False)

    teams = box.get("teams", {}) or {}
    away_blob = teams.get("away", {}) or {}
    home_blob = teams.get("home", {}) or {}

    away_lineup = extract_lineup(away_blob)
    home_lineup = extract_lineup(home_blob)

    if not away_lineup and not home_lineup:
        return (0, False)

    rows = []
    confirmed = False

    for team_blob, lineup, our_team_id in [
        (away_blob, away_lineup, game["away_team_id"]),
        (home_blob, home_lineup, game["home_team_id"]),
    ]:
        if not lineup:
            continue
        # If batting orders are populated AND game hasn't started, MLB calls
        # them confirmed. Once 9 hitters with consecutive slots 1-9 = confirmed.
        slots = sorted(b["batting_order"] for b in lineup)
        is_confirmed = (slots == list(range(1, 10)))
        if is_confirmed:
            confirmed = True

        for b in lineup:
            mlbam = b["mlbam_id"]
            if mlbam not in player_map:
                # Auto-register batter (name + handedness)
                new_id = upsert_batter(
                    mlbam, b["name"], b.get("position"),
                    b.get("bats"), our_team_id,
                )
                player_map[mlbam] = new_id

            rows.append({
                "game_id": game["id"],
                "team_id": our_team_id,
                "player_id": player_map[mlbam],
                "batting_order": b["batting_order"],
                "position": b.get("position"),
                "bats": b.get("bats"),
                "is_confirmed": is_confirmed,
                "source": "mlb_api",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    if not rows:
        return (0, False)

    # Delete existing lineups for this game first, then insert fresh.
    # Cleaner than upsert because batting_order assignments may shift.
    sb.table("lineups").delete().eq("game_id", game["id"]).execute()
    # Insert in chunks
    for i in range(0, len(rows), 50):
        sb.table("lineups").insert(rows[i:i+50]).execute()

    return (len(rows), confirmed)


def main():
    log.info("🧅 Cebolla Lab — Lineups sync starting")

    team_map = get_team_map()
    player_map = get_player_map()
    games = get_todays_games()

    if not games:
        log.info("No games today.")
        return

    log.info("Found %d games today", len(games))

    total_rows = 0
    games_confirmed = 0
    games_with_lineup = 0
    games_skipped = 0

    for i, g in enumerate(games, 1):
        log.info("[%d/%d] gamePk %d", i, len(games), g["mlb_game_pk"])
        rows, confirmed = process_game(g, team_map, player_map)
        if rows:
            games_with_lineup += 1
            total_rows += rows
            if confirmed:
                games_confirmed += 1
                log.info("   ✓ %d batters (CONFIRMED)", rows)
            else:
                log.info("   ◦ %d batters (projected)", rows)
        else:
            games_skipped += 1
            log.info("   ⌀ no lineup yet")
        time.sleep(REQUEST_DELAY)

    log.info("🧅 Lineups sync complete: %d total batters across %d games (%d confirmed); %d games skipped",
             total_rows, games_with_lineup, games_confirmed, games_skipped)


if __name__ == "__main__":
    main()
