"""
pull_rosters.py — Daily 40-man roster sync for all 30 MLB teams.

Hits MLB Stats API's roster endpoint per team and upserts every player
into `players` with correct team_id, position, bats, throws, is_pitcher.

This is the source of truth for `players.team_id`. Other scripts
(pull_savant, pull_lineups, etc.) upsert players when they encounter
them, but only this script systematically attaches every roster member
to a team. Without this run, the Team Deep Dive page on the frontend
sees only the small fraction of players that have appeared in recent
lineups or Statcast events.

Idempotent. Safe to run as often as desired; the upsert key is mlbam_id.

Schedule: once daily via GitHub Actions, alongside the morning pipeline.
"""

import os
import sys
import logging
import time
import requests
from datetime import datetime, timezone
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"

# Roster type — '40Man' gives the full 40-man including minors callups
# that show up on the active roster. 'active' is more restrictive (26-man).
# 40Man covers anyone who could plausibly play, which is what we want.
ROSTER_TYPE = "40Man"

# Polite delay between team requests so we don't hammer the API.
REQUEST_DELAY_SECONDS = 0.25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def get_team_map() -> dict[int, dict]:
    """{mlb_id: {id, abbrev, name}} for every team in our DB."""
    res = sb.table("teams").select("id, mlb_id, abbrev, name").execute()
    return {t["mlb_id"]: t for t in res.data}


def fetch_roster(mlb_team_id: int) -> list[dict]:
    """Pull the 40-man roster for a team from MLB Stats API."""
    url = f"{MLB_API}/teams/{mlb_team_id}/roster"
    params = {"rosterType": ROSTER_TYPE}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("roster", [])
    except Exception as e:
        log.warning("Failed to fetch roster for team %d: %s", mlb_team_id, e)
        return []


def fetch_person(mlbam_id: int) -> dict | None:
    """
    Pull full player detail from MLB Stats API to get bats/throws.
    The roster endpoint doesn't include these — they live on the person record.
    """
    url = f"{MLB_API}/people/{mlbam_id}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        people = r.json().get("people", [])
        return people[0] if people else None
    except Exception as e:
        log.warning("Failed to fetch person %d: %s", mlbam_id, e)
        return None


def build_player_payload(roster_entry: dict, person: dict | None,
                         our_team_id: int) -> dict | None:
    """Build the upsert payload for one player."""
    person_obj = roster_entry.get("person") or {}
    mlbam_id = person_obj.get("id")
    name = person_obj.get("fullName") or person_obj.get("nameFirstLast")
    if not mlbam_id or not name:
        return None

    pos_obj = roster_entry.get("position") or {}
    pos_abbrev = pos_obj.get("abbreviation")  # 'P', 'C', '1B', '2B', '3B', 'SS', 'OF', 'LF', 'CF', 'RF', 'DH'
    pos_code = pos_obj.get("code")            # numeric, '1' = pitcher

    # Pitcher detection: prefer the position code (most reliable) but also
    # accept the abbreviation as a fallback.
    is_pitcher = (str(pos_code) == "1") or (pos_abbrev == "P")

    # Bats / throws come from the /people/{id} record. The roster endpoint
    # doesn't expose them.
    bats = None
    throws = None
    if person:
        bat_side = person.get("batSide") or {}
        throw_hand = person.get("pitchHand") or {}
        bats = bat_side.get("code")    # 'L' / 'R' / 'S'
        throws = throw_hand.get("code")  # 'L' / 'R'

    return {
        "mlbam_id": mlbam_id,
        "name": name,
        "team_id": our_team_id,
        "position": pos_abbrev,
        "bats": bats,
        "throws": throws,
        "is_pitcher": is_pitcher,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    log.info("🧅 Cebolla Lab — Roster sync starting (%s)", ROSTER_TYPE)
    team_map = get_team_map()
    if not team_map:
        log.error("No teams in DB — run seed_teams.py first")
        sys.exit(1)

    log.info("Found %d teams in DB", len(team_map))

    all_payloads: list[dict] = []
    seen_mlbam: set[int] = set()
    teams_processed = 0
    teams_failed = 0

    for mlb_team_id, team_row in team_map.items():
        our_team_id = team_row["id"]
        roster = fetch_roster(mlb_team_id)
        if not roster:
            teams_failed += 1
            log.warning("Skipping %s (no roster returned)", team_row.get("abbrev"))
            continue

        log.info("  %s: %d players", team_row.get("abbrev"), len(roster))

        for entry in roster:
            person_obj = entry.get("person") or {}
            mlbam_id = person_obj.get("id")
            if not mlbam_id or mlbam_id in seen_mlbam:
                continue
            seen_mlbam.add(mlbam_id)

            # Pull bats/throws from the person record. This is the most
            # expensive part of the run — one call per player ~= 1200 calls
            # for all 30 teams' 40-mans. Polite delay prevents 429s.
            person = fetch_person(mlbam_id)
            time.sleep(REQUEST_DELAY_SECONDS)

            payload = build_player_payload(entry, person, our_team_id)
            if payload:
                all_payloads.append(payload)

        teams_processed += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    log.info("Prepared %d player rows from %d teams (%d failed)",
             len(all_payloads), teams_processed, teams_failed)

    if not all_payloads:
        log.warning("No payloads to write — exiting")
        return

    # Chunk writes so we don't blow Supabase's request size budget.
    written = 0
    for i in range(0, len(all_payloads), 100):
        chunk = all_payloads[i:i + 100]
        sb.table("players").upsert(
            chunk,
            on_conflict="mlbam_id",
        ).execute()
        written += len(chunk)
        if i % 500 == 0 and i > 0:
            log.info("  Wrote %d / %d", written, len(all_payloads))

    log.info("✓ Wrote %d player roster rows", written)


if __name__ == "__main__":
    main()
