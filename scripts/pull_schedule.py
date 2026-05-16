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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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

    return {
        "mlb_game_pk": game["gamePk"],
        "game_date": game["gameDate"][:10],
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
    date_str = get_today_iso()
    print(f"🧅 Cebolla Lab — pulling schedule for {date_str}")

    games = fetch_schedule(date_str)
    print(f"   Found {len(games)} games")

    rows = []
    for g in games:
        row = process_game(g)
        if row:
            rows.append(row)

    if rows:
        sb.table("games").upsert(rows, on_conflict="mlb_game_pk").execute()
        print(f"   ✓ Upserted {len(rows)} games")
    else:
        print("   (no games today)")


if __name__ == "__main__":
    main()
