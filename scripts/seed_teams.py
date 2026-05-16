"""
seed_teams.py — Populate the `teams` table with all 30 MLB teams.

Includes stadium lat/lng for weather lookups and rough HR park factors.
Park factors are 3-year averages from Baseball Savant (publicly known).
Run once after creating the schema.

Usage:
    pip install supabase python-dotenv
    cp .env.example .env  # add SUPABASE_URL and SUPABASE_KEY
    python scripts/seed_teams.py
"""

import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service key, not anon

# (mlb_id, abbrev, name, league, division, stadium, lat, lng,
#  park_hr_factor, park_hr_lhb, park_hr_rhb, park_ba_factor, is_dome)
TEAMS = [
    (109, "ARI", "Arizona Diamondbacks",   "NL", "West",     "Chase Field",         33.4453, -112.0667, 1.04, 1.05, 1.03, 1.00, True),
    (144, "ATL", "Atlanta Braves",         "NL", "East",     "Truist Park",         33.8908, -84.4678,  1.01, 1.00, 1.02, 0.99, False),
    (110, "BAL", "Baltimore Orioles",      "AL", "East",     "Oriole Park at Camden Yards", 39.2839, -76.6217, 1.06, 1.04, 1.09, 1.01, False),
    (111, "BOS", "Boston Red Sox",         "AL", "East",     "Fenway Park",         42.3467, -71.0972,  1.05, 0.97, 1.08, 1.06, False),
    (112, "CHC", "Chicago Cubs",           "NL", "Central",  "Wrigley Field",       41.9484, -87.6553,  1.02, 1.00, 1.03, 1.00, False),
    (145, "CWS", "Chicago White Sox",      "AL", "Central",  "Guaranteed Rate Field", 41.83, -87.6339, 1.06, 1.08, 1.05, 1.00, False),
    (113, "CIN", "Cincinnati Reds",        "NL", "Central",  "Great American Ball Park", 39.0975, -84.5067, 1.13, 1.16, 1.11, 1.01, False),
    (114, "CLE", "Cleveland Guardians",    "AL", "Central",  "Progressive Field",   41.4962, -81.6852,  0.97, 0.96, 0.98, 0.99, False),
    (115, "COL", "Colorado Rockies",       "NL", "West",     "Coors Field",         39.7559, -104.9942, 1.13, 1.08, 1.18, 1.12, False),
    (116, "DET", "Detroit Tigers",         "AL", "Central",  "Comerica Park",       42.3390, -83.0485,  0.94, 0.92, 0.96, 0.97, False),
    (117, "HOU", "Houston Astros",         "AL", "West",     "Daikin Park",         29.7572, -95.3556,  1.03, 1.05, 1.02, 0.99, True),
    (118, "KC",  "Kansas City Royals",     "AL", "Central",  "Kauffman Stadium",    39.0517, -94.4803,  0.92, 0.92, 0.93, 1.01, False),
    (108, "LAA", "Los Angeles Angels",     "AL", "West",     "Angel Stadium",       33.8003, -117.8827, 1.00, 1.01, 1.00, 0.98, False),
    (119, "LAD", "Los Angeles Dodgers",    "NL", "West",     "Dodger Stadium",      34.0739, -118.24,   1.04, 1.06, 1.03, 0.97, False),
    (146, "MIA", "Miami Marlins",          "NL", "East",     "loanDepot park",      25.7781, -80.2197,  0.86, 0.85, 0.87, 0.96, True),
    (158, "MIL", "Milwaukee Brewers",      "NL", "Central",  "American Family Field", 43.0285, -87.9712, 1.03, 1.04, 1.02, 0.99, True),
    (142, "MIN", "Minnesota Twins",        "AL", "Central",  "Target Field",        44.9818, -93.2776,  0.96, 0.94, 0.97, 1.00, False),
    (121, "NYM", "New York Mets",          "NL", "East",     "Citi Field",          40.7571, -73.8458,  0.94, 0.92, 0.95, 0.98, False),
    (147, "NYY", "New York Yankees",       "AL", "East",     "Yankee Stadium",      40.8296, -73.9262,  1.10, 1.18, 1.04, 1.00, False),
    (133, "ATH", "Athletics",               "AL", "West",    "Sutter Health Park",  38.5803, -121.5128, 0.98, 0.97, 1.00, 0.99, False),
    (143, "PHI", "Philadelphia Phillies",  "NL", "East",     "Citizens Bank Park",  39.9061, -75.1665,  1.07, 1.10, 1.05, 1.00, False),
    (134, "PIT", "Pittsburgh Pirates",     "NL", "Central",  "PNC Park",            40.4469, -80.0058,  0.91, 0.89, 0.93, 1.00, False),
    (135, "SD",  "San Diego Padres",       "NL", "West",     "Petco Park",          32.7073, -117.157,  0.92, 0.91, 0.93, 0.97, False),
    (137, "SF",  "San Francisco Giants",   "NL", "West",     "Oracle Park",         37.7786, -122.3893, 0.85, 0.78, 0.91, 0.98, False),
    (136, "SEA", "Seattle Mariners",       "AL", "West",     "T-Mobile Park",       47.5914, -122.3325, 0.93, 0.91, 0.94, 0.97, False),
    (138, "STL", "St. Louis Cardinals",    "NL", "Central",  "Busch Stadium",       38.6226, -90.1928,  0.95, 0.93, 0.96, 1.00, False),
    (139, "TB",  "Tampa Bay Rays",         "AL", "East",     "Steinbrenner Field",  27.9805, -82.5067,  0.97, 0.96, 0.98, 0.99, False),
    (140, "TEX", "Texas Rangers",          "AL", "West",     "Globe Life Field",    32.7475, -97.0822,  0.99, 1.00, 0.98, 0.99, True),
    (141, "TOR", "Toronto Blue Jays",      "AL", "East",     "Rogers Centre",       43.6414, -79.3894,  1.05, 1.06, 1.04, 1.00, True),
    (120, "WSH", "Washington Nationals",   "NL", "East",     "Nationals Park",      38.873,  -77.0074,  1.01, 1.02, 1.00, 1.00, False),
]


def seed():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = []
    for t in TEAMS:
        rows.append({
            "mlb_id": t[0], "abbrev": t[1], "name": t[2],
            "league": t[3], "division": t[4], "stadium": t[5],
            "stadium_lat": t[6], "stadium_lng": t[7],
            "park_hr_factor": t[8], "park_hr_lhb": t[9],
            "park_hr_rhb": t[10], "park_ba_factor": t[11],
            "is_dome": t[12],
        })
    res = sb.table("teams").upsert(rows, on_conflict="mlb_id").execute()
    print(f"Seeded {len(res.data)} teams.")


if __name__ == "__main__":
    seed()
