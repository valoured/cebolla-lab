"""
settle_cards.py — Grade Cebolla Cards after games settle.

Mirrors settle_pods.py architecture: fetch MLB boxscore for each game,
extract batting line by mlbam_id, grade each leg, roll up to card status.

Card status rules:
  - WIN  → all non-void legs hit
  - LOSS → any leg is loss
  - VOID → all legs voided (rare — full slate postponed, etc.)
  - PENDING → at least one leg still pending

Per-leg status lets the frontend show partial green/red dots BEFORE the
full card settles. E.g. on a 3-legger if 2 legs hit and 1 is pending,
frontend shows 2 green / 1 yellow.
"""

import os
import logging
from datetime import datetime, timezone, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("settle_cards")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"


# ────────────────────────────────────────────────────────────────────────
# DATE
# ────────────────────────────────────────────────────────────────────────

def get_recent_date_window():
    """Settle cards from the last 3 days — covers late settlements."""
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=3), today


# ────────────────────────────────────────────────────────────────────────
# MLB BOXSCORE FETCH (mirrors settle_pods.py)
# ────────────────────────────────────────────────────────────────────────

def fetch_boxscore(mlb_game_pk):
    """Pull boxscore from MLB Stats API."""
    url = f"{MLB_API}/game/{mlb_game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        if not r.ok:
            return None
        return r.json()
    except Exception as e:
        log.warning("  boxscore fetch failed for game_pk=%d: %s", mlb_game_pk, e)
        return None


def find_player_batting(boxscore, mlbam_id):
    """Find a batter's stat line in the boxscore. Returns None if absent."""
    if not boxscore or mlbam_id is None:
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


# ────────────────────────────────────────────────────────────────────────
# STATUS CLASSIFIER (mirrors pull_scores.py — same keyword lists)
# ────────────────────────────────────────────────────────────────────────

TERMINAL_KEYWORDS = ("final", "game over", "completed", "postponed",
                     "cancelled", "canceled", "forfeit")
LIVE_KEYWORDS = ("in progress", "manager challenge", "umpire review",
                 "replay", "instant replay", "delayed", "suspended")
PREGAME_KEYWORDS = ("scheduled", "pre-game", "pregame", "warmup", "status unknown")


def classify_game_status(status_str):
    """Map raw status string → 'final' | 'live' | 'pregame' | 'unknown'.
    Terminal checked first so 'Postponed' doesn't match 'delayed'."""
    s = (status_str or "").strip().lower()
    if not s:
        return "unknown"
    if any(k in s for k in TERMINAL_KEYWORDS):
        return "final"
    if any(k in s for k in LIVE_KEYWORDS):
        return "live"
    if any(k in s for k in PREGAME_KEYWORDS):
        return "pregame"
    return "unknown"


# ────────────────────────────────────────────────────────────────────────
# LEG GRADING
# ────────────────────────────────────────────────────────────────────────

def parse_hrr_line(market):
    """h_r_rbi_1.5 -> 1.5"""
    try:
        return float(market.split("_")[-1])
    except (ValueError, IndexError):
        return None


def grade_leg(leg, batting, game_status):
    """
    Grade a single leg using boxscore batting line + game status.
    Returns: 'win' | 'loss' | 'void' | 'pending'.
    """
    klass = classify_game_status(game_status)
    if klass == "live" or klass == "pregame" or klass == "unknown":
        return "pending"
    # Final from here

    if batting is None:
        # Game final but player has no batting line — didn't appear (defensive
        # sub, DH/PH never used, etc.). Standard sportsbook rule: VOID.
        return "void"

    pa = int(batting.get("plateAppearances") or 0)
    if pa == 0:
        # Player in box but no PAs — also void
        return "void"

    hits = int(batting.get("hits") or 0)
    runs = int(batting.get("runs") or 0)
    rbi  = int(batting.get("rbi") or 0)
    hr   = int(batting.get("homeRuns") or 0)

    market = leg["market"]
    if market == "hr_anytime":
        return "win" if hr >= 1 else "loss"
    elif market == "hits_yes":
        return "win" if hits >= 1 else "loss"
    elif market == "rbi_yes":
        return "win" if rbi >= 1 else "loss"
    elif market.startswith("h_r_rbi_"):
        line = parse_hrr_line(market)
        if line is None:
            return "pending"
        total = hits + runs + rbi
        return "win" if total > line else "loss"
    else:
        log.warning("  unknown market: %s", market)
        return "pending"


# ────────────────────────────────────────────────────────────────────────
# CARD STATUS ROLL-UP
# ────────────────────────────────────────────────────────────────────────

def roll_up_card_status(leg_statuses):
    """
    Aggregate per-leg statuses into card-level status.

    - LOSS  → any leg lost (entire card is dead)
    - PENDING → any leg still pending
    - VOID  → ALL legs voided
    - WIN   → all non-void legs won
    """
    if "loss" in leg_statuses:
        return "loss"
    if "pending" in leg_statuses:
        return "pending"
    if all(s == "void" for s in leg_statuses):
        return "void"
    return "win"


def compute_card_payout(card, status, won_legs):
    """
    Compute actual P&L for a settled card at its recommended stake.

    On VOID legs, the leg drops out of the parlay — recompute effective
    decimal odds from won legs only. Returns signed P&L.
    """
    stake = float(card["stake_rec"] or 0)
    if status == "loss":
        return -stake
    if status == "void":
        return 0.0
    if status == "win":
        if not won_legs:
            return 0.0
        eff_decimal = 1.0
        for leg in won_legs:
            american = leg.get("american_odds")
            if american is None:
                continue
            if american > 0:
                eff_decimal *= (1 + american / 100.0)
            else:
                eff_decimal *= (1 + 100.0 / abs(american))
        profit = stake * (eff_decimal - 1)
        return round(profit, 2)
    return 0.0


# ────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ────────────────────────────────────────────────────────────────────────

def fetch_pending_cards(date_from, date_to):
    """Fetch cards in window with status='pending'."""
    cards_res = sb.table("cards") \
        .select("id, card_date, tier, label, stake_rec, status, "
                "decimal_odds, combined_odds") \
        .gte("card_date", date_from.isoformat()) \
        .lte("card_date", date_to.isoformat()) \
        .in_("status", ["pending"]) \
        .execute()
    cards = cards_res.data or []
    if not cards:
        return []

    card_ids = [c["id"] for c in cards]
    legs_res = sb.table("card_legs") \
        .select("*") \
        .in_("card_id", card_ids) \
        .execute()
    legs_by_card = {}
    for leg in (legs_res.data or []):
        legs_by_card.setdefault(leg["card_id"], []).append(leg)

    return [{"card": c, "legs": legs_by_card.get(c["id"], [])} for c in cards]


def fetch_games_for_legs(legs):
    """Map game_id -> game row (with mlb_game_pk + status)."""
    if not legs:
        return {}
    game_ids = list({l["game_id"] for l in legs if l.get("game_id")})
    res = sb.table("games").select("id, mlb_game_pk, status") \
        .in_("id", game_ids).execute()
    return {g["id"]: g for g in (res.data or [])}


# ────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────

def main():
    date_from, date_to = get_recent_date_window()
    log.info("🧅 Card settler — window %s to %s", date_from, date_to)

    pending = fetch_pending_cards(date_from, date_to)
    if not pending:
        log.info("No pending cards to settle")
        return

    log.info("Pending cards: %d", len(pending))

    all_legs = [leg for entry in pending for leg in entry["legs"]]
    games_by_id = fetch_games_for_legs(all_legs)

    # Cache boxscores per mlb_game_pk — many cards share games, this is huge
    # for performance and rate-limit safety.
    boxscore_cache = {}

    def get_boxscore(game_id):
        game = games_by_id.get(game_id)
        if not game or not game.get("mlb_game_pk"):
            return None
        pk = game["mlb_game_pk"]
        if pk in boxscore_cache:
            return boxscore_cache[pk]
        # Only fetch for final games (live boxscores would give partial data
        # but we treat live legs as pending anyway, so no need)
        klass = classify_game_status(game.get("status"))
        if klass != "final":
            boxscore_cache[pk] = None
            return None
        box = fetch_boxscore(pk)
        boxscore_cache[pk] = box
        return box

    settled_wins = 0
    settled_losses = 0
    settled_voids = 0
    still_pending = 0

    for entry in pending:
        card = entry["card"]
        legs = entry["legs"]
        if not legs:
            log.warning("  card %d has no legs — skipping", card["id"])
            continue

        leg_statuses = []
        won_legs = []
        leg_updates = []

        for leg in legs:
            game = games_by_id.get(leg["game_id"])
            game_status = (game or {}).get("status")

            # Boxscore is only needed for final games
            box = get_boxscore(leg["game_id"])
            batting = find_player_batting(box, leg.get("player_mlbam_id")) if box else None

            grade = grade_leg(leg, batting, game_status)
            leg_statuses.append(grade)
            if grade == "win":
                won_legs.append(leg)
            if leg.get("status") != grade:
                leg_updates.append({"id": leg["id"], "status": grade})

        new_card_status = roll_up_card_status(leg_statuses)
        payout = compute_card_payout(card, new_card_status, won_legs)

        # Update per-leg statuses first (so frontend shows partial progress
        # even when card itself stays pending)
        for upd in leg_updates:
            sb.table("card_legs").update({"status": upd["status"]}) \
                .eq("id", upd["id"]).execute()

        if new_card_status == "pending":
            still_pending += 1
            log.info("  card %d (%s): still pending — legs=%s",
                     card["id"], card.get("label") or card.get("tier"),
                     leg_statuses)
            continue

        card_update = {
            "status": new_card_status,
            "payout": payout,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        sb.table("cards").update(card_update).eq("id", card["id"]).execute()

        if new_card_status == "win":
            settled_wins += 1
            log.info("  ✓ card %d WIN: %s → +$%.2f (legs=%s)",
                     card["id"], card.get("label"), payout, leg_statuses)
        elif new_card_status == "loss":
            settled_losses += 1
            log.info("  ✗ card %d LOSS: %s → -$%.2f (legs=%s)",
                     card["id"], card.get("label"), abs(payout), leg_statuses)
        elif new_card_status == "void":
            settled_voids += 1
            log.info("  ○ card %d VOID: %s → $0.00 (legs=%s)",
                     card["id"], card.get("label"), leg_statuses)

    log.info("✓ Settler complete — wins=%d, losses=%d, voids=%d, pending=%d",
             settled_wins, settled_losses, settled_voids, still_pending)


if __name__ == "__main__":
    main()
