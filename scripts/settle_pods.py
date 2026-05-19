"""
settle_pods.py — Settle pending Plays of the Day after MLB games go Final.

For every pending POD:
  1. Check that the underlying game is Final.
  2. Hit MLB Stats API boxscore for the game.
  3. Find the player's batting line.
  4. Grade based on the POD's market:
       - hr_anytime          → win if homeRuns >= 1
       - h_r_rbi_<line>      → win if (hits + runs + RBI) > line
  5. Compute payout from american_odds × stake.
  6. Update pods row with status + payout + settled_at.

Idempotent: only touches rows with status='pending'. Once status is
'win'/'loss'/'push'/'void', the row is frozen.

This script intentionally mirrors settle_bets.py's MLB-boxscore logic
to keep behavior consistent across the two grading systems. If you
update one's HR-grading rule, update the other.

Runs hourly on the slate cron so today's POD settles within an hour
of the game ending.
"""

import os
import sys
import logging
from datetime import datetime, timezone

import requests
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


def american_profit(odds: int, stake: float) -> float:
    """
    Return PROFIT (not total payout) for a winning bet at given odds/stake.
    +900 odds, $10 stake → +$90 profit
    -150 odds, $10 stake → +$6.67 profit
    """
    if odds >= 0:
        return stake * (odds / 100.0)
    return stake * (100.0 / abs(odds))


def fetch_boxscore(mlb_game_pk: int) -> dict | None:
    """Pull boxscore from MLB Stats API."""
    url = f"{MLB_API}/game/{mlb_game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        if not r.ok:
            return None
        return r.json()
    except Exception as e:
        log.warning("  Failed boxscore for game_pk=%d: %s", mlb_game_pk, e)
        return None


def find_player_batting(boxscore: dict, mlbam_id: int) -> dict | None:
    """Find a batter's stat line in the boxscore. Returns None if absent."""
    if not boxscore:
        return None
    for side in ("away", "home"):
        team = boxscore.get("teams", {}).get(side, {})
        players = team.get("players", {})
        player_key = f"ID{mlbam_id}"
        if player_key in players:
            stats = players[player_key].get("stats", {}).get("batting", {})
            if stats:
                return stats
    return None


def grade_hr_pod(batting: dict | None, stake: float, odds: int) -> tuple[str, float]:
    """
    Returns (status, payout) for an HR-market POD.

    Rules:
      - No batting line (player didn't appear): VOID → payout 0 (stake refunded conceptually)
      - PA = 0 but in box (rare defensive sub): VOID → payout 0
      - HR ≥ 1: WIN → +profit
      - HR = 0 with PAs: LOSS → -stake
    """
    if batting is None:
        return ("void", 0.0)

    hr   = int(batting.get("homeRuns", 0) or 0)
    ab   = int(batting.get("atBats", 0) or 0)
    bb   = int(batting.get("baseOnBalls", 0) or 0)
    sf   = int(batting.get("sacFlies", 0) or 0)
    hbp  = int(batting.get("hitByPitch", 0) or 0)
    pa   = ab + bb + sf + hbp

    if pa == 0:
        return ("void", 0.0)

    if hr >= 1:
        return ("win", round(american_profit(odds, stake), 2))
    return ("loss", round(-stake, 2))


def parse_hrr_line(market: str) -> float | None:
    """
    Extract the line from an h_r_rbi market string.
    'h_r_rbi_2.5' → 2.5
    'h_r_rbi_yes' → None (legacy, not a POD format)
    """
    if not market or not market.startswith("h_r_rbi_"):
        return None
    suffix = market[len("h_r_rbi_"):]
    try:
        return float(suffix)
    except (ValueError, TypeError):
        return None


def grade_hrr_pod(batting: dict | None, stake: float, odds: int, line: float) -> tuple[str, float]:
    """
    Returns (status, payout) for an HRR-market POD.

    Rules:
      - No batting line (player didn't appear): VOID → payout 0
      - PA = 0 but in box (rare defensive sub): VOID → payout 0
      - (hits + runs + RBI) > line: WIN → +profit
        (e.g. line=1.5 needs 2 or more; line=2.5 needs 3 or more)
      - (hits + runs + RBI) <= line: LOSS → -stake
    """
    if batting is None:
        return ("void", 0.0)

    hits = int(batting.get("hits", 0) or 0)
    runs = int(batting.get("runs", 0) or 0)
    rbis = int(batting.get("rbi", 0) or 0)
    ab   = int(batting.get("atBats", 0) or 0)
    bb   = int(batting.get("baseOnBalls", 0) or 0)
    sf   = int(batting.get("sacFlies", 0) or 0)
    hbp  = int(batting.get("hitByPitch", 0) or 0)
    pa   = ab + bb + sf + hbp

    if pa == 0:
        return ("void", 0.0)

    total = hits + runs + rbis
    if total > line:
        return ("win", round(american_profit(odds, stake), 2))
    return ("loss", round(-stake, 2))


def settle_one(pod: dict) -> bool:
    """Attempt to settle a single pending POD. Returns True if status changed."""
    game_id = pod.get("game_id")
    if not game_id:
        log.warning("POD %d has no game_id, skipping", pod["id"])
        return False

    # Look up the game — need mlb_game_pk + status + player MLBAM id
    game_res = sb.table("games") \
        .select("id, mlb_game_pk, status") \
        .eq("id", game_id) \
        .single() \
        .execute()
    game = game_res.data
    if not game:
        log.warning("POD %d: game %d not found", pod["id"], game_id)
        return False

    status = (game.get("status") or "")
    if status not in ("Final", "Game Over", "Completed Early"):
        return False  # not yet final

    player_res = sb.table("players") \
        .select("mlbam_id, name") \
        .eq("id", pod["player_id"]) \
        .single() \
        .execute()
    player = player_res.data
    if not player or not player.get("mlbam_id"):
        log.warning("POD %d: player %d has no mlbam_id", pod["id"], pod["player_id"])
        return False

    box = fetch_boxscore(game["mlb_game_pk"])
    if not box:
        log.warning("POD %d: boxscore fetch failed for game_pk %d",
                    pod["id"], game["mlb_game_pk"])
        return False

    batting = find_player_batting(box, int(player["mlbam_id"]))

    market = (pod.get("market") or "").lower()
    odds = int(pod["american_odds"])
    stake = float(pod.get("stake") or 10.00)

    if market == "hr_anytime":
        result, payout = grade_hr_pod(batting, stake, odds)
    elif market.startswith("h_r_rbi_"):
        line = parse_hrr_line(market)
        if line is None:
            log.warning("POD %d: could not parse HRR line from market %r",
                        pod["id"], market)
            return False
        result, payout = grade_hrr_pod(batting, stake, odds, line)
    else:
        log.warning("POD %d: market %r not supported by settler", pod["id"], market)
        return False

    sb.table("pods").update({
        "status": result,
        "payout": payout,
        "settled_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", pod["id"]).execute()

    log.info("POD %d (%s): %s | payout %+.2f",
             pod["id"], player["name"], result.upper(), payout)
    return True


def main():
    log.info("🧅 POD settler — scanning pending PODs")
    res = sb.table("pods") \
        .select("*") \
        .eq("status", "pending") \
        .execute()
    pending = res.data or []
    log.info("Found %d pending POD(s)", len(pending))

    settled = 0
    for pod in pending:
        if settle_one(pod):
            settled += 1

    log.info("✓ Settled %d POD(s)", settled)


if __name__ == "__main__":
    main()
