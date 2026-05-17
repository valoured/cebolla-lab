"""
settle_bets.py — Auto-grade pending bets after MLB games go Final.

For every pending bet:
  1. Check game status. Skip if not Final.
  2. Hit MLB Stats API boxscore for the game.
  3. Find the player's batting line.
  4. Decide win/loss based on market + side + line.
  5. Compute PnL from american_odds × stake.
  6. Update bet_log row.

Supported markets:
  - hr_anytime_yes  — win if HR ≥ 1
  - hr_anytime_no   — win if HR = 0
  - hits_over       — win if hits > line
  - hits_under      — win if hits < line  (push if =)
  (extend here as we add markets)

Runs hourly on cron. Idempotent (skips already-settled).
"""

import os
import sys
import logging
from datetime import datetime, timezone, date

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


def american_payout(odds: int, stake: float) -> float:
    """Return total payout (stake + profit) for a winning bet."""
    if odds >= 0:
        return stake + (stake * odds / 100)
    return stake + (stake * 100 / -odds)


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
    """Find a batter's stat line in the boxscore."""
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


def grade_bet(bet: dict, batting: dict | None) -> tuple[str, float] | None:
    """
    Return (result, pnl) for a bet given the boxscore batting line.
    result ∈ {'win', 'loss', 'push', 'void'}
    Returns None if we don't know how to grade this market.
    """
    market = (bet.get("market") or "").lower()
    side   = (bet.get("side") or "").lower()
    line   = float(bet.get("line") or 0)
    odds   = bet.get("american_odds")
    stake  = float(bet.get("stake") or 0)

    # No batting line means player didn't appear → void (we refund)
    if batting is None:
        return ("void", 0.0)

    hr     = int(batting.get("homeRuns", 0) or 0)
    hits   = int(batting.get("hits", 0) or 0)
    ab     = int(batting.get("atBats", 0) or 0)
    # MLB counts a PA as anything that isn't a sub-out (rough but works):
    bb     = int(batting.get("baseOnBalls", 0) or 0)
    sf     = int(batting.get("sacFlies", 0) or 0)
    hbp    = int(batting.get("hitByPitch", 0) or 0)
    pa     = ab + bb + sf + hbp

    # If player was in the boxscore but had zero PAs (defensive sub, etc.) → void
    if pa == 0:
        return ("void", 0.0)

    # ─── HR Anytime ───
    if market in ("hr_anytime", "hr_anytime_yes", "hr_anytime_yes_no"):
        if side in ("yes", "over"):
            win = hr >= 1
        elif side in ("no", "under"):
            win = hr == 0
        else:
            return None

    # ─── Hits over/under ───
    elif market in ("hits", "hits_over_under"):
        if side == "over":
            if hits > line:
                win = True
            elif hits == line:
                return ("push", 0.0)
            else:
                win = False
        elif side == "under":
            if hits < line:
                win = True
            elif hits == line:
                return ("push", 0.0)
            else:
                win = False
        else:
            return None

    else:
        return None  # unknown market

    # Win/loss PnL
    if win:
        pnl = american_payout(odds, stake) - stake
        return ("win", pnl)
    else:
        return ("loss", -stake)


def get_pending_bets() -> list[dict]:
    res = sb.table("bet_log").select(
        "id, game_id, player_id, market, side, line, american_odds, stake, model_version"
    ).eq("result", "pending").execute()
    return res.data


def get_game(game_id: int) -> dict | None:
    res = sb.table("games").select(
        "id, mlb_game_pk, status"
    ).eq("id", game_id).execute()
    return res.data[0] if res.data else None


def get_player(player_id: int) -> dict | None:
    res = sb.table("players").select("id, mlbam_id, name").eq("id", player_id).execute()
    return res.data[0] if res.data else None


def main():
    log.info("🧅 Cebolla Lab — settle_bets starting")

    pending = get_pending_bets()
    log.info("Found %d pending bets", len(pending))

    if not pending:
        log.info("Nothing to settle.")
        return

    # Cache boxscores per game_pk (multiple bets may share a game)
    boxscore_cache: dict[int, dict | None] = {}
    game_cache: dict[int, dict | None] = {}
    player_cache: dict[int, dict | None] = {}

    settled = 0
    skipped_not_final = 0
    errors = 0

    for bet in pending:
        try:
            game = game_cache.setdefault(bet["game_id"], get_game(bet["game_id"]))
            if not game:
                log.warning("  bet %d: game %d not found, skipping", bet["id"], bet["game_id"])
                continue

            status = (game.get("status") or "").lower()
            if status not in {"final", "game over", "completed early"}:
                skipped_not_final += 1
                continue

            mlb_pk = game.get("mlb_game_pk")
            if mlb_pk not in boxscore_cache:
                boxscore_cache[mlb_pk] = fetch_boxscore(mlb_pk)
            box = boxscore_cache[mlb_pk]

            player = player_cache.setdefault(bet["player_id"], get_player(bet["player_id"]))
            if not player:
                log.warning("  bet %d: player %d not found", bet["id"], bet["player_id"])
                continue

            batting = find_player_batting(box, player["mlbam_id"])
            graded = grade_bet(bet, batting)

            if graded is None:
                log.warning("  bet %d: unknown market %s/%s, leaving pending",
                            bet["id"], bet["market"], bet["side"])
                continue

            result, pnl = graded
            stake = float(bet.get("stake") or 0)
            payout = stake + pnl if result in ("win",) else (stake if result in ("push","void") else 0)

            sb.table("bet_log").update({
                "result": result,
                "pnl": round(pnl, 2),
                "payout": round(payout, 2),
                "settled_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", bet["id"]).execute()

            settled += 1
            log.info("  ✓ bet %d  %s  $%.2f stake @ %+d → %s  pnl=%+.2f",
                     bet["id"], player.get("name", "?"),
                     stake, bet.get("american_odds") or 0,
                     result.upper(), pnl)

        except Exception as e:
            log.exception("  Error grading bet %d: %s", bet.get("id"), e)
            errors += 1

    log.info("🧅 Settled %d  |  skipped (not final): %d  |  errors: %d",
             settled, skipped_not_final, errors)


if __name__ == "__main__":
    main()
