"""
ballparks.py — v2 static ballpark metadata (Day 3).

ONLY roof type lives here — the genuinely-new static datum. Coordinates and the
home-plate→CF compass bearing already live in the teams table
(teams.stadium_lat / stadium_lng / home_plate_bearing) and are read from there
at runtime, so they are NOT duplicated here (single source of truth, avoids
drift). Mirrors the small-static-map pattern (cf. tier_system._LEAGUE_BRL_BY_FAMILY
and pull_weather.CF_BEARING_FALLBACK).

ROOF_TYPE values (static, per stadium):
  'open'        — no roof; full weather effect           → roof_status 'no_roof'
  'retractable' — roof open/closed unknown in advance    → roof_status 'retractable_uncertain' (50% discount)
  'fixed'       — permanently enclosed                   → roof_status 'closed' (weather-neutral, index 1.000)

The per-GAME roof_status (no_roof / retractable_uncertain / closed) is derived
from this type by the pull script; see roof_status_for().
"""

ROOF_TYPE = {
    "ARI": "retractable",  # Chase Field — retractable
    "ATL": "open",         # Truist Park
    "ATH": "open",         # Sutter Health Park (Sacramento) — outdoor, plays hot
    "BAL": "open",         # Oriole Park at Camden Yards
    "BOS": "open",         # Fenway Park
    "CHC": "open",         # Wrigley Field
    "CWS": "open",         # Rate Field
    "CIN": "open",         # Great American Ball Park
    "CLE": "open",         # Progressive Field
    "COL": "open",         # Coors Field
    "DET": "open",         # Comerica Park
    "HOU": "retractable",  # Daikin Park — retractable
    "KC":  "open",         # Kauffman Stadium
    "LAA": "open",         # Angel Stadium
    "LAD": "open",         # Dodger Stadium
    "MIA": "retractable",  # loanDepot park — retractable
    "MIL": "retractable",  # American Family Field — retractable
    "MIN": "open",         # Target Field
    "NYM": "open",         # Citi Field
    "NYY": "open",         # Yankee Stadium
    "PHI": "open",         # Citizens Bank Park
    "PIT": "open",         # PNC Park
    "SD":  "open",         # Petco Park
    "SF":  "open",         # Oracle Park
    "SEA": "retractable",  # T-Mobile Park — retractable
    "STL": "open",         # Busch Stadium
    "TB":  "fixed",        # Tropicana Field — permanently enclosed dome (back home 2026)
    "TEX": "retractable",  # Globe Life Field — retractable
    "TOR": "retractable",  # Rogers Centre — retractable
    "WSH": "open",         # Nationals Park
}

DEFAULT_ROOF_TYPE = "open"

# Roof TYPE (static) → roof STATUS (per-game, fed to compute_weather_hr_index).
# Retractables resolve to 'retractable_uncertain' because we don't know the
# open/closed state in advance → the index applies a 50% weather discount.
_TYPE_TO_STATUS = {
    "open":        "no_roof",
    "retractable": "retractable_uncertain",
    "fixed":       "closed",
}


def roof_type_for(abbrev: str) -> str:
    """Static roof type for a team abbrev (DEFAULT_ROOF_TYPE if unknown)."""
    return ROOF_TYPE.get(abbrev, DEFAULT_ROOF_TYPE)


def roof_status_for(abbrev: str) -> str:
    """Per-game roof status string consumed by compute_weather_hr_index()."""
    return _TYPE_TO_STATUS.get(roof_type_for(abbrev), "no_roof")
