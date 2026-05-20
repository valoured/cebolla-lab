"""
settle_cards.py — Grade Cebolla Cards after games settle.

Runs alongside settle_pods.py post-game. Grades each leg independently
(win/loss/void) using the same scoring data settle_pods uses. Then rolls
up to card-level status:

  Card status rules:
    - WIN  → all non-void legs hit
    - LOSS → any leg is loss
    - VOID → no legs hit AND all legs are void (postponements, etc.)
    - PENDING → at least one leg still pending

Per-leg status lets the frontend show partial green/red dots BEFORE the
full card settles. E.g. on a 3-legger if 2 legs hit and 1 is pending,
frontend shows 2 green / 1 yellow.
"""

import os
import logging
from datetime import datetime, timezone, timedelta

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("settle_cards")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ────────────────────────────────────────────────────────────────────────
# DATE
# ────────────────────────────────────────────────────────────────────────

def get_recent_date_window():
    """
    Settle cards from the last 3 days. Cards stay pending until grading,
    so the window covers late settlements.
    """
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=3), today


# ────────────────────────────────────────────────────────────────────────
# LEG GRADING
# ────────────────────────────────────────────────────────────────────────

def parse_hrr_line(market):
    """h_r_rbi_1.5 -> 1.5"""
    try:
        return float(market.split("_")[-1])
    except (ValueError, IndexError):
        return None


def grade_leg(leg, batter_stats_by_player, game_status_by_game):
    """
    Grade a single leg using batter stats + game status. Returns one of:
    'win', 'loss', 'void', 'pending'.
    """
    game_id = leg["game_id"]
    player_id = leg["player_id"]
    market = leg["market"]

    # Need the game to be final to grade
    g_status = game_status_by_game.get(game_id)
    if not g_status:
        return "pending"
    if g_status in ("postponed", "cancelled", "suspended"):
        return "void"
    if g_status != "final":
        return "pending"

    stats = batter_stats_by_player.get(player_id)
    if not stats:
        # Game final but no batter stats — usually means player didn't play
        return "void"

    hits = int(stats.get("hits") or 0)
    runs = int(stats.get("runs") or 0)
    rbi  = int(stats.get("rbi") or 0)
    hr   = int(stats.get("hr") or 0)

    # Grade by market
    if market == "hr_anytime":
        return "win" if hr >= 1 else "loss"
    elif market == "hits_yes":
        # Default line 0.5 (anytime hit). Other lines could be supported later.
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
    Aggregate per-leg statuses into a card-level status.

    - LOSS  → any leg lost (entire card is dead)
    - PENDING → any leg still pending (not all games final)
    - VOID  → ALL legs voided (rare — full slate postponed, etc.)
    - WIN   → all non-void legs won
    """
    if "loss" in leg_statuses:
        return "loss"
    if "pending" in leg_statuses:
        return "pending"
    if all(s == "void" for s in leg_statuses):
        return "void"
    # Remaining case: mix of wins and voids → card wins (voided legs drop)
    return "win"


def compute_card_payout(card, status, won_legs):
    """
    Compute the actual P&L for a settled card at its recommended stake.

    On VOID legs, the leg is removed from the parlay → the effective parlay
    decimal odds shrink. We recompute decimal odds from won legs only.

    Returns: signed P&L (positive on win, negative on loss, 0 on void).
    """
    stake = float(card["stake_rec"] or 0)
    if status == "loss":
        return -stake
    if status == "void":
        return 0.0
    if status == "win":
        if not won_legs:
            # Edge case: all legs voided but we said "win" — treat as void
            return 0.0
        # Recompute effective decimal from won legs only (in case some voided)
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
    """
    Fetch cards in window that have at least one pending leg.
    Returns list of {card, legs}.
    """
    cards_res = sb.table("cards") \
        .select("id, card_date, tier, label, stake_rec, status, decimal_odds, combined_odds") \
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


def fetch_batter_stats_for_legs(legs):
    """Bulk fetch batter_game_stats for all (game_id, player_id) tuples."""
    if not legs:
        return {}
    game_ids = list({l["game_id"] for l in legs if l.get("game_id")})
    player_ids = list({l["player_id"] for l in legs if l.get("player_id")})

    res = sb.table("batter_game_stats").select("*") \
        .in_("game_id", game_ids).in_("player_id", player_ids).execute()
    out = {}
    for row in (res.data or []):
        # Key by (game_id, player_id) — same player can have stats across multiple games
        key = (row["game_id"], row["player_id"])
        out[key] = row
    return out


def fetch_game_statuses(legs):
    """Map game_id -> game status (final / live / scheduled / postponed)."""
    if not legs:
        return {}
    game_ids = list({l["game_id"] for l in legs if l.get("game_id")})
    res = sb.table("games").select("id, status") \
        .in_("id", game_ids).execute()
    return {g["id"]: (g.get("status") or "").lower() for g in (res.data or [])}


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

    # Bulk fetch all needed data
    all_legs = [leg for entry in pending for leg in entry["legs"]]
    batter_stats = fetch_batter_stats_for_legs(all_legs)
    game_statuses = fetch_game_statuses(all_legs)

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

        # Grade each leg
        leg_statuses = []
        won_legs = []
        leg_updates = []
        for leg in legs:
            # Lookup stats by (game_id, player_id) — game_id pinpoints which game
            stats = batter_stats.get((leg["game_id"], leg["player_id"]))
            stats_for_grade = stats if stats else None

            grade = grade_leg(
                leg,
                {leg["player_id"]: stats_for_grade} if stats_for_grade else {},
                game_statuses,
            )
            leg_statuses.append(grade)
            if grade == "win":
                won_legs.append(leg)
            # Update only if status changed
            if leg.get("status") != grade:
                leg_updates.append({"id": leg["id"], "status": grade})

        # Roll up to card status
        new_card_status = roll_up_card_status(leg_statuses)
        payout = compute_card_payout(card, new_card_status, won_legs)

        # Update per-leg statuses first
        for upd in leg_updates:
            sb.table("card_legs").update({"status": upd["status"]}) \
                .eq("id", upd["id"]).execute()

        # Update card
        if new_card_status == "pending":
            still_pending += 1
            log.info("  card %d (%s): still pending — legs=%s",
                     card["id"], card.get("label") or card.get("tier"), leg_statuses)
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
