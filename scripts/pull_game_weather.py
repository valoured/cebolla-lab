"""
pull_game_weather.py — v2 per-game weather snapshots → game_weather table.

v2 rebuild Day 3. Distinct from the legacy pull_weather.py (which writes a single
park×weather aggregate onto the games row for v0.1.3). This writes MULTI-SNAPSHOT
weather-only rows to game_weather with a computed weather_hr_index, for the v2
HR model.

Reuses teams.stadium_lat / stadium_lng / home_plate_bearing (coords + CF axis)
and scripts/v2/ballparks.ROOF_TYPE (roof). Open-Meteo, no key. Times are UTC
(timezone param omitted) and matched against games.game_time_utc.

Snapshots:
  --snapshot cron_330am            (default; the 3:30 AM ET pick-time forecast)
  --snapshot 1h_before_first_pitch (the hourly-cron refresh; per-game window
                                    selection is applied when wired into cron)

Usage:
  python pull_game_weather.py --dry-run               # fetch + compute + print; NO DB writes
  python pull_game_weather.py                         # prod upsert (snapshot cron_330am)
  python pull_game_weather.py --snapshot 1h_before_first_pitch
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

from v2.weather_index import compute_weather_hr_index
from v2 import ballparks

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pull_game_weather")

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
SOURCE = "open_meteo"
VALID_SNAPSHOTS = ("cron_330am", "1h_before_first_pitch")


def fetch_forecast(lat: float, lng: float, game_time_utc: str) -> dict | None:
    """Open-Meteo hourly forecast; return the row matching the game-time UTC hour."""
    params = {
        "latitude": lat,
        "longitude": lng,
        # Decision #5: fetch humidity; skip cloud_cover; keep precip for future use.
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation_probability",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "forecast_days": 2,            # timezone omitted → times are UTC
    }
    try:
        r = requests.get(OPEN_METEO, params=params, timeout=15)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        log.warning("  Open-Meteo fetch failed (%.4f,%.4f): %s", lat, lng, e)
        return None

    h = d["hourly"]
    target = (game_time_utc or "")[:13]          # 'YYYY-MM-DDTHH'
    idx = next((i for i, t in enumerate(h["time"]) if t.startswith(target)), 0)
    return {
        "temp_f": h["temperature_2m"][idx],
        "humidity_pct": h["relative_humidity_2m"][idx],
        "wind_mph": h["wind_speed_10m"][idx],
        "wind_dir_deg": h["wind_direction_10m"][idx],
        "precip_pct": h["precipitation_probability"][idx],
        "matched_hour": h["time"][idx],
    }


# Games already final at fetch time are expected only on ad-hoc late-day runs
# (or postponed makeups); the early-morning cron runs pre-slate. We still fetch.
FINAL_STATUSES = ("Final", "Game Over", "Completed Early")


def get_today_iso():
    """ET-relative slate date (same convention as the rest of the pipeline)."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def build_snapshot(game, team, snapshot_type):
    """Fetch + compute one game's weather snapshot dict (None on fetch failure)."""
    abbrev = team.get("abbrev")
    roof_status = ballparks.roof_status_for(abbrev)

    wx = fetch_forecast(float(team["stadium_lat"]), float(team["stadium_lng"]),
                        game.get("game_time_utc"))
    if not wx:
        return None

    bearing = team.get("home_plate_bearing")
    idx = compute_weather_hr_index(
        wx["temp_f"], wx["humidity_pct"], wx["wind_mph"], wx["wind_dir_deg"],
        bearing, roof_status,
    )
    return {
        "game_id": game["id"],
        "snapshot_type": snapshot_type,
        "temp_f": round(float(wx["temp_f"]), 1) if wx["temp_f"] is not None else None,
        "humidity_pct": round(float(wx["humidity_pct"]), 2) if wx["humidity_pct"] is not None else None,
        "wind_mph": round(float(wx["wind_mph"]), 1) if wx["wind_mph"] is not None else None,
        "wind_direction_deg": int(wx["wind_dir_deg"]) if wx["wind_dir_deg"] is not None else None,
        "precipitation_pct": int(wx["precip_pct"]) if wx["precip_pct"] is not None else None,
        "roof_status": roof_status,
        "weather_hr_index": idx,
        "source": SOURCE,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        # log-only context (stripped before upsert):
        "_abbrev": abbrev,
        "_bearing": bearing,
        "_matched_hour": wx["matched_hour"],
    }


def _interpret(idx):
    if idx >= 1.02:
        return f"BOOST  (+{(idx - 1) * 100:.1f}% HR)"
    if idx <= 0.98:
        return f"SUPPRESS ({(idx - 1) * 100:.1f}% HR)"
    return "neutral"


def print_validation(snaps):
    print(f"\n=== HARNESS: {len(snaps)} games (snapshot=cron_330am) ===")
    print(f"{'park':<5}{'roof':<22}{'temp':>6}{'hum':>6}{'wind':>7}{'dir':>5}{'idx':>7}  effect")
    for s in sorted(snaps, key=lambda s: s["weather_hr_index"], reverse=True):
        print(f"{s['_abbrev']:<5}{s['roof_status']:<22}{str(s['temp_f']):>6}{str(s['humidity_pct']):>6}"
              f"{str(s['wind_mph']):>7}{str(s['wind_direction_deg']):>5}{s['weather_hr_index']:>7}  "
              f"{_interpret(s['weather_hr_index'])}")

    ranked = sorted(snaps, key=lambda s: s["weather_hr_index"], reverse=True)
    print("\nTop 3 boosted:")
    for s in ranked[:3]:
        print(f"  {s['_abbrev']:<5} idx={s['weather_hr_index']}  ({s['roof_status']})")
    print("Bottom 3 suppressed:")
    for s in ranked[-3:]:
        print(f"  {s['_abbrev']:<5} idx={s['weather_hr_index']}  ({s['roof_status']})")

    retract = [s for s in snaps if s["roof_status"] == "retractable_uncertain"]
    closed = [s for s in snaps if s["roof_status"] == "closed"]
    print(f"\nretractable_uncertain (50% discount) games: {[s['_abbrev'] for s in retract] or 'none'}")
    print(f"closed-roof (index forced 1.000) games:    {[s['_abbrev'] for s in closed] or 'none'}")


def upsert(sb, snaps):
    payload = [{k: v for k, v in s.items() if not k.startswith("_")} for s in snaps]
    sb.table("game_weather").upsert(payload, on_conflict="game_id,snapshot_type").execute()
    return len(payload)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    dry_run = "--dry-run" in sys.argv
    snapshot_type = "cron_330am"
    if "--snapshot" in sys.argv:
        snapshot_type = sys.argv[sys.argv.index("--snapshot") + 1]
        if snapshot_type not in VALID_SNAPSHOTS:
            log.error("invalid --snapshot %r (expected one of %s)", snapshot_type, VALID_SNAPSHOTS)
            sys.exit(1)

    log.info("🧅 game_weather %s — snapshot=%s", "DRY-RUN" if dry_run else "sync", snapshot_type)
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    today = get_today_iso()
    games = sb.table("games").select(
        "id, game_time_utc, venue, home_team_id, status"
    ).eq("game_date", today).execute().data or []
    teams = {t["id"]: t for t in sb.table("teams").select(
        "id, abbrev, stadium_lat, stadium_lng, home_plate_bearing").execute().data or []}
    log.info("Slate %s: %d games", today, len(games))

    snaps = []
    for g in games:
        team = teams.get(g["home_team_id"])
        if not team or team.get("stadium_lat") is None:
            log.warning("  game %s: missing home team coords — skipped", g["id"])
            continue
        if g.get("status") in FINAL_STATUSES:
            log.warning("  game %s (%s) already %s at fetch time — fetching weather "
                        "anyway (expected on ad-hoc late runs / postponements).",
                        g["id"], team.get("abbrev"), g.get("status"))
        s = build_snapshot(g, team, snapshot_type)
        if s:
            snaps.append(s)

    if dry_run:
        print_validation(snaps)
        log.info("DRY-RUN complete — NO DB writes. %d snapshots computed.", len(snaps))
        return

    n = upsert(sb, snaps)
    log.info("✓ Upserted %d game_weather rows (snapshot=%s)", n, snapshot_type)


if __name__ == "__main__":
    main()
