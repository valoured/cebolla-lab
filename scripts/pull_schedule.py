"""
pull_schedule.py — Fetch today's MLB schedule + probable pitchers.

Uses the free public MLB Stats API (no key required).
Writes to `games` and upserts unknown pitchers into `players`.

Schedule:
- Run multiple times per day via GitHub Actions:
  - 06:00 UTC (early morning, first probable starters)
  - 14:00 UTC (mid-day lineup confirmations)
  - 22:00 UTC (final pre-game)
"""

import os
import requests
from datetime import datetime, timezone
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"


def get_today_iso():
    """
    Return today's date as ET (America/New_York), not UTC.

    MLB's schedule API takes a date param and returns games with that
    officialDate. The official date follows the team's local clock, with ET
    being a reasonable proxy for "the baseball day" — games in PT can run
    until ~1 AM ET but still count as the same baseball day.

    Using UTC here would miss late-night Pacific games or pick up tomorrow's
    games when the cron runs after 8 PM ET.
    """
    from datetime import timedelta
    # ET is UTC-4 during DST (summer), UTC-5 standard time.
    # Approximate by using UTC-5 as a baseline that works year-round —
    # the only edge case is between midnight ET and 5 AM UTC, which is
    # ~7 PM-12 AM ET, where we'd still want yesterday's games.
    # Using UTC-5 (EST) as a conservative offset handles this.
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime("%Y-%m-%d")


def fetch_schedule(date_str: str) -> list[dict]:
    """Hit MLB Stats API for the day's schedule with probable pitchers."""
    url = f"{MLB_API}/schedule"
    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "probablePitcher,linescore,team",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("dates"):
        return []
    return data["dates"][0]["games"]


def get_team_by_mlb_id(mlb_id: int) -> int | None:
    res = sb.table("teams").select("id").eq("mlb_id", mlb_id).execute()
    return res.data[0]["id"] if res.data else None


def upsert_pitcher(mlbam_id: int, name: str, team_id: int | None) -> int:
    """Create or update a pitcher record, return players.id."""
    payload = {
        "mlbam_id": mlbam_id,
        "name": name,
        "team_id": team_id,
        "is_pitcher": True,
        "position": "P",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("players").upsert(payload, on_conflict="mlbam_id").execute()
    return res.data[0]["id"]


def process_game(game: dict) -> dict | None:
    """Convert MLB API game payload into our `games` row."""
    teams = game.get("teams", {})
    away = teams.get("away", {})
    home = teams.get("home", {})

    away_team_id = get_team_by_mlb_id(away["team"]["id"])
    home_team_id = get_team_by_mlb_id(home["team"]["id"])
    if not (away_team_id and home_team_id):
        print(f"  ⚠ Skipping game {game['gamePk']} — unknown team")
        return None

    away_pp = away.get("probablePitcher")
    home_pp = home.get("probablePitcher")

    away_pitcher_id = None
    if away_pp:
        away_pitcher_id = upsert_pitcher(
            away_pp["id"], away_pp["fullName"], away_team_id
        )
    home_pitcher_id = None
    if home_pp:
        home_pitcher_id = upsert_pitcher(
            home_pp["id"], home_pp["fullName"], home_team_id
        )

    # CRITICAL: use officialDate, NOT gameDate[:10].
    #
    # gameDate is a UTC timestamp; slicing the first 10 chars gives the UTC
    # calendar date, which is WRONG for West Coast night games (their start
    # time falls after midnight UTC but they belong to the previous calendar
    # day per MLB and per common sense).
    #
    # officialDate is MLB's authoritative single-date assignment for the game
    # and always returns the correct value (e.g. a 9:40 PM PT game in San Diego
    # has officialDate=2026-05-18 even though gameDate is 2026-05-19T04:40:00Z).
    #
    # Falls back to gameDate slice only if officialDate is missing (shouldn't
    # happen but be defensive).
    official_date = game.get("officialDate")
    if not official_date:
        official_date = game["gameDate"][:10]

    return {
        "mlb_game_pk": game["gamePk"],
        "game_date": official_date,
        "game_time_utc": game["gameDate"],
        "away_team_id": away_team_id,
        "home_team_id": home_team_id,
        "away_pitcher_id": away_pitcher_id,
        "home_pitcher_id": home_pitcher_id,
        "venue": game.get("venue", {}).get("name"),
        "status": game.get("status", {}).get("detailedState"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    """
    Pull a 3-day window centered on today.

    Why 3 days: handles UTC <-> local-time edge cases.
    - "Today" by our reckoning is yesterday or tomorrow depending on cron time.
    - West Coast night games span the UTC date line.
    - This way we ALWAYS have the next 24-36 hours of games loaded, regardless
      of when the cron fires.

    officialDate handles the day-assignment correctly; we just need to make
    sure we fetch all relevant days.
    """
    from datetime import timedelta as _td

    today_et = datetime.now(timezone.utc) - _td(hours=4)
    dates_to_pull = [
        (today_et - _td(days=1)).strftime("%Y-%m-%d"),
        today_et.strftime("%Y-%m-%d"),
        (today_et + _td(days=1)).strftime("%Y-%m-%d"),
    ]

    print(f"🧅 Cebolla Lab — pulling schedule for {dates_to_pull}")

    all_rows = []
    for date_str in dates_to_pull:
        games = fetch_schedule(date_str)
        print(f"   {date_str}: {len(games)} games from MLB")
        for g in games:
            row = process_game(g)
            if row:
                all_rows.append(row)

    if all_rows:
        sb.table("games").upsert(all_rows, on_conflict="mlb_game_pk").execute()
        print(f"   ✓ Upserted {len(all_rows)} games across {len(dates_to_pull)} dates")
    else:
        print("   (no games found in window)")


if __name__ == "__main__":
    main()
