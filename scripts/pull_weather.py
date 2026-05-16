"""
pull_weather.py — Pull per-stadium weather and compute wind→HR adjustments.

For each game scheduled today or tomorrow, hit Open-Meteo for the home
stadium's forecasted weather at game time. Compute:
  - temp_f, wind_mph, wind_dir_deg, precip_pct
  - wind_label (e.g. "out to CF", "in from RF")
  - aggregate HR factor (park × weather × handedness)

Runs hourly via GitHub Actions to keep forecasts fresh.
"""

import os
import sys
import logging
from datetime import datetime, timezone, date, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Stadium orientation: compass bearing (degrees) from home plate to CF.
# 0 = N, 90 = E, 180 = S, 270 = W.
# Used to convert wind direction → "out to CF" vs "in from CF" etc.
# Approximate; refine with measured values if needed.
# ────────────────────────────────────────────────────────────────
CF_BEARING_BY_TEAM_ABBREV = {
    "ARI": 23,    "ATL": 50,   "BAL": 38,   "BOS": 45,   "CHC": 30,
    "CWS": 130,   "CIN": 35,   "CLE": 0,    "COL": 0,    "DET": 145,
    "HOU": 348,   "KC": 45,    "LAA": 60,   "LAD": 25,   "MIA": 40,
    "MIL": 135,   "MIN": 90,   "NYM": 25,   "NYY": 75,   "ATH": 60,
    "PHI": 15,    "PIT": 117,  "SD": 0,     "SF": 90,    "SEA": 45,
    "STL": 60,    "TB": 45,    "TEX": 0,    "TOR": 0,    "WSH": 30,
}


def wind_relative_to_cf(wind_from_deg: int, cf_bearing: int) -> tuple[str, float]:
    """
    wind_from_deg = direction wind is coming FROM (Open-Meteo convention).
    Wind blowing TOWARD = (wind_from_deg + 180) % 360.

    Returns (label, hr_multiplier).
    """
    blowing_toward = (wind_from_deg + 180) % 360
    # Angle between wind direction and CF
    diff = (blowing_toward - cf_bearing + 360) % 360
    if diff > 180:
        diff = 360 - diff  # symmetric

    # Categorize:
    if diff <= 30:
        return "out to CF", 1.10
    elif diff <= 60:
        return "out to LF/RF", 1.05
    elif diff <= 120:
        return "cross", 1.00
    elif diff <= 150:
        return "in from LF/RF", 0.95
    else:
        return "in from CF", 0.90


def temp_factor(temp_f: float | None) -> float:
    """Hot air = ball travels further. ~3% per 10°F over 70°F baseline."""
    if temp_f is None:
        return 1.0
    delta = (temp_f - 70) / 10
    return 1.0 + (0.03 * delta)


def fetch_weather(lat: float, lng: float, game_time_utc: str) -> dict | None:
    """Get forecast for the hour of game time."""
    try:
        params = {
            "latitude": lat,
            "longitude": lng,
            "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "forecast_days": 2,
        }
        r = requests.get(OPEN_METEO, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        # Match game time hour to an entry in hourly arrays
        hours = data["hourly"]["time"]
        # Parse target hour: "2026-05-16T23:00"
        target = game_time_utc[:13]  # YYYY-MM-DDTHH

        for i, t in enumerate(hours):
            if t.startswith(target):
                return {
                    "temp_f": data["hourly"]["temperature_2m"][i],
                    "wind_mph": data["hourly"]["wind_speed_10m"][i],
                    "wind_dir_deg": data["hourly"]["wind_direction_10m"][i],
                    "precip_pct": data["hourly"]["precipitation_probability"][i],
                }
        # Fallback: first available hour after now
        return {
            "temp_f": data["hourly"]["temperature_2m"][0],
            "wind_mph": data["hourly"]["wind_speed_10m"][0],
            "wind_dir_deg": data["hourly"]["wind_direction_10m"][0],
            "precip_pct": data["hourly"]["precipitation_probability"][0],
        }
    except Exception as e:
        log.warning("  Weather fetch failed: %s", e)
        return None


def update_game_weather(game: dict, team: dict) -> bool:
    """Pull weather + compute HR factors, write back to the games row."""
    if team["is_dome"]:
        # Dome: no weather impact, just park factors
        sb.table("games").update({
            "temp_f": 72,
            "wind_mph": 0,
            "wind_label": "dome",
            "precip_pct": 0,
            "hr_factor_lhb": float(team["park_hr_lhb"]),
            "hr_factor_rhb": float(team["park_hr_rhb"]),
            "hr_factor_overall": float(team["park_hr_factor"]),
            "weather_updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", game["id"]).execute()
        return True

    weather = fetch_weather(
        float(team["stadium_lat"]),
        float(team["stadium_lng"]),
        game["game_time_utc"],
    )
    if not weather:
        return False

    cf_bearing = CF_BEARING_BY_TEAM_ABBREV.get(team["abbrev"], 0)
    wind_label, wind_mult = wind_relative_to_cf(
        int(weather["wind_dir_deg"]), cf_bearing,
    )
    # Scale wind effect by wind speed (no effect below 5 mph)
    speed = float(weather["wind_mph"])
    if speed < 5:
        wind_mult = 1.0
    else:
        # Interpolate: 5 mph = mild, 15 mph = full effect
        intensity = min((speed - 5) / 10, 1.0)
        wind_mult = 1.0 + (wind_mult - 1.0) * intensity

    t_mult = temp_factor(weather["temp_f"])

    park_lhb = float(team["park_hr_lhb"])
    park_rhb = float(team["park_hr_rhb"])
    park_all = float(team["park_hr_factor"])

    payload = {
        "temp_f": round(weather["temp_f"], 1),
        "wind_mph": round(speed, 1),
        "wind_dir_deg": int(weather["wind_dir_deg"]),
        "wind_label": wind_label,
        "precip_pct": int(weather["precip_pct"]) if weather["precip_pct"] is not None else 0,
        "hr_factor_lhb": round(park_lhb * wind_mult * t_mult, 3),
        "hr_factor_rhb": round(park_rhb * wind_mult * t_mult, 3),
        "hr_factor_overall": round(park_all * wind_mult * t_mult, 3),
        "weather_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb.table("games").update(payload).eq("id", game["id"]).execute()
    return True


def main():
    log.info("🧅 Cebolla Lab — Weather sync starting")

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    games = sb.table("games") \
        .select("id, mlb_game_pk, game_date, game_time_utc, home_team_id, status") \
        .gte("game_date", today) \
        .lte("game_date", tomorrow) \
        .not_.in_("status", ["Final", "Game Over"]) \
        .execute()

    log.info("Found %d upcoming games", len(games.data))

    teams = {
        t["id"]: t for t in sb.table("teams").select("*").execute().data
    }

    ok = fail = 0
    for g in games.data:
        team = teams.get(g["home_team_id"])
        if not team:
            log.warning("  Game %d: unknown home team", g["id"])
            fail += 1
            continue

        if update_game_weather(g, team):
            ok += 1
        else:
            fail += 1

    log.info("🧅 Weather sync complete: %d ok, %d failed", ok, fail)


if __name__ == "__main__":
    main()
