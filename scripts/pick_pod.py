"""
pick_pod.py — Daily Play of the Day selection.

Selects ONE HR prop per slate that has:
  - projected_prob >= 0.30 (floor — no wild longshots)
  - largest edge among qualifying projections
  - American odds present (we need a number to log)

Writes a row to the `pods` table with status='pending'. Idempotent:
if a POD already exists for today, this script does nothing.

Run AFTER:
  - pull_schedule.py    (so today's games exist)
  - pull_dk_odds.py     (so odds_snapshots are current)
  - compute_projections.py (so projections.projected_prob and .edge are fresh)

Run BEFORE first pitch so the pick is locked in honestly. The morning
cron at 14:13 UTC (10:13 AM ET) is the right window — after projections
have been computed but before any games start.
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# Selection thresholds
MIN_PROJECTED_PROB = 0.30  # filter out wild longshots
HR_MARKET = "hr_0.5"       # over 0.5 HRs = at least one HR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def get_today_iso() -> str:
    """ET-relative baseball day. Same convention as pull_schedule.py."""
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.date().isoformat()


def existing_pod_for(date_iso: str) -> bool:
    """Return True if a POD already exists for this date."""
    r = sb.table("pods").select("id").eq("pod_date", date_iso).limit(1).execute()
    return bool(r.data)


def fetch_candidates(date_iso: str) -> list[dict]:
    """
    Fetch HR projections for today's games above the prob floor.
    Returns rows enriched with game + player metadata for the snapshot.
    """
    # Step 1: get game IDs for today (and only games still scheduled — no point
    # picking a POD for a game that's already final or in progress).
    games_res = sb.table("games") \
        .select("id, away_team_id, home_team_id, "
                "away_team:teams!games_away_team_id_fkey(abbrev), "
                "home_team:teams!games_home_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .not_.in_("status", ["Final", "Game Over", "Completed Early", "In Progress"]) \
        .execute()
    games = games_res.data or []
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}

    # Step 2: fetch projections for those games, HR market only.
    # Note: edge can be null if odds weren't available — we filter those out.
    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, edge, "
                "best_american_odds, best_book, model_version") \
        .in_("game_id", game_ids) \
        .eq("market", HR_MARKET) \
        .gte("projected_prob", MIN_PROJECTED_PROB) \
        .not_.is_("edge", "null") \
        .not_.is_("best_american_odds", "null") \
        .order("edge", desc=True) \
        .execute()
    projections = proj_res.data or []
    if not projections:
        return []

    # Step 3: enrich with player metadata
    player_ids = list({p["player_id"] for p in projections})
    player_res = sb.table("players") \
        .select("id, mlbam_id, name, team_id") \
        .in_("id", player_ids) \
        .execute()
    players_by_id = {p["id"]: p for p in (player_res.data or [])}

    # Build the candidate list
    enriched = []
    for p in projections:
        game = games_by_id.get(p["game_id"])
        player = players_by_id.get(p["player_id"])
        if not game or not player:
            continue
        # Figure out which team is the opponent
        is_home = player.get("team_id") == game.get("home_team_id")
        own_abbrev = (game["home_team"] if is_home else game["away_team"])["abbrev"]
        opp_abbrev = (game["away_team"] if is_home else game["home_team"])["abbrev"]
        enriched.append({
            "game_id": p["game_id"],
            "player_id": p["player_id"],
            "player_mlbam_id": player.get("mlbam_id"),
            "player_name": player["name"],
            "team_abbrev": own_abbrev,
            "opponent_abbrev": opp_abbrev,
            "market": p["market"],
            "projected_prob": p["projected_prob"],
            "no_vig_prob": p["no_vig_prob"],
            "edge": p["edge"],
            "american_odds": p["best_american_odds"],
            "book": p["best_book"],
            "model_version": p["model_version"],
        })
    return enriched


def main():
    today = get_today_iso()
    log.info("🧅 POD picker — slate %s", today)

    if existing_pod_for(today):
        log.info("POD already exists for %s. Nothing to do.", today)
        return

    candidates = fetch_candidates(today)
    if not candidates:
        log.warning("No qualifying HR projections for %s (need >= %.2f projected_prob with odds).",
                    today, MIN_PROJECTED_PROB)
        return

    pick = candidates[0]
    log.info("Top candidate: %s (%s vs %s) — projected %.2f%%, odds %+d, edge %.3f",
             pick["player_name"],
             pick["team_abbrev"],
             pick["opponent_abbrev"],
             100 * float(pick["projected_prob"]),
             pick["american_odds"],
             float(pick["edge"]))

    # Insert. UNIQUE(pod_date) prevents duplicates if this races.
    sb.table("pods").insert({
        "pod_date": today,
        "game_id": pick["game_id"],
        "player_id": pick["player_id"],
        "player_mlbam_id": pick["player_mlbam_id"],
        "market": pick["market"],
        "projected_prob": pick["projected_prob"],
        "no_vig_prob": pick["no_vig_prob"],
        "edge": pick["edge"],
        "american_odds": pick["american_odds"],
        "book": pick["book"],
        "model_version": pick["model_version"],
        "player_name": pick["player_name"],
        "team_abbrev": pick["team_abbrev"],
        "opponent_abbrev": pick["opponent_abbrev"],
        "stake": 10.00,
        "status": "pending",
    }).execute()
    log.info("✓ POD locked for %s", today)


if __name__ == "__main__":
    main()
