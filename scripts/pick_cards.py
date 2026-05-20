"""
pick_cards.py — AI-built Cebolla Cards.

Generates daily parlay cards from today's projection pool. Runs after
pick_pod.py in the 2:45 AM ET POD lock window.

CARD TIERS (variable per slate quality):
  two_leg   — up to 3 cards, ev_per_dollar > 0.05
  three_leg — up to 2 cards, ev_per_dollar > 0.08
  four_leg  — up to 1 card,  ev_per_dollar > 0.10 AND 4+ strong candidates

STAKE RECOMMENDATIONS (canonical, frontend can scale):
  two_leg=$10, three_leg=$5, four_leg=$1

MATH:
  combined_prob   = ∏(leg_prob) × (1 - correlation_penalty)
  parlay_decimal  = ∏(leg_decimal)
  parlay_american = decimal_to_american(parlay_decimal)
  implied_prob    = 1 / parlay_decimal
  edge            = combined_prob - implied_prob
  ev_per_dollar   = combined_prob × (parlay_decimal - 1) - (1 - combined_prob)

CORRELATION PENALTIES:
  same game     -8%   (weather, lineup, umpire shared)
  same team     -12%  (lineup state shared, e.g. two HRs from same team
                       requires same hot offense)
  same player   -15%  (player having a good day correlates across markets)

CANDIDATE FLOORS (per-market):
  hr_anytime   projected_prob >= 0.08, edge >= 0.03
  h_r_rbi_1.5  projected_prob >= 0.40, edge >= 0.03
  h_r_rbi_2.5  projected_prob >= 0.20, edge >= 0.03
  hits_yes     projected_prob >= 0.55, edge >= 0.03
  rbi_yes      projected_prob >= 0.35, edge >= 0.03

OBJECTIVE:
  Mix of EV and "right reads". Each combination scored as:
    score = ev_per_dollar * 100 + avg_leg_edge * 50

  Higher EV preferred, but combined edge also matters (rewards combinations
  where each leg has its own independent edge, not just one carry-leg).

DEDUP:
  After scoring, greedy selection: take highest-scoring combo, exclude its
  players from subsequent same-tier picks. Three-leggers cannot share more
  than 1 leg with any selected two-legger. Four-leggers cannot share more
  than 2 legs.
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from itertools import combinations

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pick_cards")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ────────────────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────────────────

# Market floors — each market needs its own min projected_prob because
# the markets have wildly different base rates. HR is ~10% baseline,
# Hits 0.5 is ~70% baseline — same floor would be nonsense.
MARKET_FLOORS = {
    "hr_anytime":  {"min_prob": 0.08, "min_edge": 0.03},
    "h_r_rbi_1.5": {"min_prob": 0.40, "min_edge": 0.03},
    "h_r_rbi_2.5": {"min_prob": 0.20, "min_edge": 0.03},
    "hits_yes":    {"min_prob": 0.55, "min_edge": 0.03},
    "rbi_yes":     {"min_prob": 0.35, "min_edge": 0.03},
}

# Stake recommendations by tier (canonical — frontend can scale linearly)
STAKE_REC = {
    "two_leg":   10.00,
    "three_leg":  5.00,
    "four_leg":   1.00,
}

# EV gates by tier — don't ship a card unless it clears its tier's EV bar
EV_GATES = {
    "two_leg":   0.05,
    "three_leg": 0.08,
    "four_leg":  0.10,
}

# Card count caps by tier (variable per slate, capped here)
CARD_CAPS = {
    "two_leg":   3,
    "three_leg": 2,
    "four_leg":  1,
}

# Correlation penalties applied to combined_prob
CORRELATION_PENALTIES = {
    "same_game":   0.08,
    "same_team":   0.12,
    "same_player": 0.15,
}

# Dedup: max shared legs between selected cards across tiers
SHARING_LIMITS = {
    "three_vs_two": 1,   # 3-leggers can share at most 1 leg with any 2-legger
    "four_vs_any":  2,   # 4-legger can share at most 2 legs with anything
}

# Global exposure caps (across the entire daily card menu, all tiers combined)
MAX_PLAYER_APPEARANCES = 2   # any single player appears on at most 2 cards
MAX_SAME_GAME_CARDS    = 1   # at most 1 card whose legs all share a single game

# Market diversification: mandate at least this many cards use ONLY non-HR
# markets (Hits / RBI / HRR). Prevents the menu from being entirely
# dependent on HR variance, which is the noisiest market we cover.
# A "non-HR card" has zero legs with market='hr_anytime'.
MIN_NON_HR_CARDS = 1

# ────────────────────────────────────────────────────────────────────────
# DATE
# ────────────────────────────────────────────────────────────────────────

def get_today_iso():
    """ET-relative slate date — same as pick_pod."""
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


# ────────────────────────────────────────────────────────────────────────
# MATH HELPERS
# ────────────────────────────────────────────────────────────────────────

def american_to_decimal(american):
    """+150 -> 2.5, -150 -> 1.667"""
    if american is None:
        return None
    a = int(american)
    if a > 0:
        return 1 + a / 100.0
    elif a < 0:
        return 1 + 100.0 / abs(a)
    else:
        return None


def decimal_to_american(decimal):
    """2.5 -> +150, 1.667 -> -150"""
    if decimal is None or decimal <= 1:
        return None
    if decimal >= 2:
        return int(round((decimal - 1) * 100))
    else:
        return int(round(-100 / (decimal - 1)))


def implied_from_decimal(decimal):
    if decimal is None or decimal <= 0:
        return None
    return 1.0 / decimal


# ────────────────────────────────────────────────────────────────────────
# CANDIDATE FETCH
# ────────────────────────────────────────────────────────────────────────

def fetch_today_games(date_iso):
    """Pull today's games + team abbrevs."""
    res = sb.table("games") \
        .select("id, away_team_id, home_team_id, "
                "away_team:teams!games_away_team_id_fkey(abbrev), "
                "home_team:teams!games_home_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .execute()
    return res.data or []


def fetch_candidates(date_iso, games):
    """
    Build the unified candidate pool across all markets, applying per-market
    floors. Returns a flat list of candidate dicts.
    """
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}

    markets = list(MARKET_FLOORS.keys())
    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, "
                "edge, best_american_odds, best_book") \
        .in_("game_id", game_ids) \
        .in_("market", markets) \
        .not_.is_("edge", "null") \
        .not_.is_("best_american_odds", "null") \
        .execute()
    projections = proj_res.data or []

    if not projections:
        return []

    # Lookup player info in batch
    player_ids = list({p["player_id"] for p in projections if p.get("player_id")})
    players_res = sb.table("players").select("id, name, mlbam_id, team_id") \
        .in_("id", player_ids).execute()
    players_by_id = {p["id"]: p for p in (players_res.data or [])}

    candidates = []
    for p in projections:
        # Apply floors
        floor = MARKET_FLOORS.get(p["market"])
        if floor is None:
            continue
        prob = float(p.get("projected_prob") or 0)
        edge = float(p.get("edge") or 0)
        if prob < floor["min_prob"]:
            continue
        if edge < floor["min_edge"]:
            continue

        player = players_by_id.get(p["player_id"])
        if not player:
            continue
        game = games_by_id.get(p["game_id"])
        if not game:
            continue

        # Resolve team + opponent abbrevs
        is_home = player["team_id"] == game["home_team_id"]
        own = (game["home_team"] if is_home else game["away_team"]) or {}
        opp = (game["away_team"] if is_home else game["home_team"]) or {}

        american = int(p["best_american_odds"])
        decimal = american_to_decimal(american)
        if decimal is None or decimal <= 1:
            continue

        # Parse line from market string (e.g. h_r_rbi_1.5 -> 1.5)
        line = None
        if p["market"].startswith("h_r_rbi_"):
            try:
                line = float(p["market"].split("_")[-1])
            except (ValueError, IndexError):
                line = None
        elif p["market"] == "hr_anytime":
            line = 0.5

        candidates.append({
            "player_id":       p["player_id"],
            "player_mlbam_id": player["mlbam_id"],
            "player_name":     player["name"],
            "team_id":         player["team_id"],
            "team_abbrev":     own.get("abbrev"),
            "opponent_abbrev": opp.get("abbrev"),
            "game_id":         p["game_id"],
            "market":          p["market"],
            "line":            line,
            "projected_prob":  prob,
            "no_vig_prob":     float(p.get("no_vig_prob") or 0) or None,
            "american_odds":   american,
            "decimal_odds":    decimal,
            "edge":            edge,
            "book":            p.get("best_book") or "draftkings",
        })

    # Sort by edge descending — the strongest plays float to top
    candidates.sort(key=lambda c: c["edge"], reverse=True)
    return candidates


# ────────────────────────────────────────────────────────────────────────
# COMBINATION SCORING
# ────────────────────────────────────────────────────────────────────────

def correlation_penalty(legs):
    """
    Compute correlation penalty for a combination of legs. Penalties stack
    additively (with a 0.40 cap to prevent absurd combined penalties).

    Returns: float in [0, 0.40] representing fraction to subtract from
             naive product-of-probs.
    """
    penalty = 0.0
    seen_games = {}
    seen_teams = {}
    seen_players = {}

    for leg in legs:
        g = leg.get("game_id")
        t = leg.get("team_id")
        p = leg.get("player_id")
        if g in seen_games:
            penalty += CORRELATION_PENALTIES["same_game"]
        if t in seen_teams:
            penalty += CORRELATION_PENALTIES["same_team"]
        if p in seen_players:
            penalty += CORRELATION_PENALTIES["same_player"]
        seen_games[g] = True
        seen_teams[t] = True
        seen_players[p] = True

    return min(penalty, 0.40)


def parlay_math(legs):
    """
    Compute parlay math for a combination of legs.

    Returns dict with: combined_prob, decimal_odds, american_odds,
    implied_prob, edge, ev_per_dollar
    """
    if not legs:
        return None

    # Naive (independence) combined prob
    raw_combined = 1.0
    for leg in legs:
        raw_combined *= leg["projected_prob"]

    # Apply correlation penalty
    penalty = correlation_penalty(legs)
    combined_prob = raw_combined * (1 - penalty)

    # Parlay decimal odds = product of individual decimals
    parlay_decimal = 1.0
    for leg in legs:
        parlay_decimal *= leg["decimal_odds"]

    parlay_american = decimal_to_american(parlay_decimal)
    implied = implied_from_decimal(parlay_decimal)

    edge = combined_prob - implied
    # EV per $1 stake: combined_prob × profit-on-win - (1 - combined_prob)
    ev = combined_prob * (parlay_decimal - 1) - (1 - combined_prob)

    return {
        "combined_prob": round(combined_prob, 5),
        "decimal_odds":  round(parlay_decimal, 3),
        "american_odds": parlay_american,
        "implied_prob":  round(implied, 5) if implied else None,
        "edge":          round(edge, 5),
        "ev_per_dollar": round(ev, 4),
        "correlation_penalty": round(penalty, 3),
    }


def score_combination(math, legs):
    """
    Objective function: mix of EV and average leg edge.

    Higher EV → bigger payoff at observed probabilities.
    Higher avg leg edge → rewards combos where each leg is independently good
    (not just one carry leg with several mediocre add-ons).
    """
    if not math or math["ev_per_dollar"] is None:
        return -999
    avg_edge = sum(l["edge"] for l in legs) / len(legs)
    return math["ev_per_dollar"] * 100 + avg_edge * 50


def make_combo_record(legs, tier, slate_quality):
    """Bundle a combination's math + legs + tier into a finalized record."""
    math = parlay_math(legs)
    if not math:
        return None

    score = score_combination(math, legs)
    stake = STAKE_REC[tier]
    profit_if_hit = round(stake * (math["decimal_odds"] - 1), 2)

    return {
        "tier":          tier,
        "leg_count":     len(legs),
        "legs":          legs,
        "math":          math,
        "score":         score,
        "stake_rec":     stake,
        "payout_if_hit": profit_if_hit,
        "label":         label_for(legs, tier, math),
    }


def label_for(legs, tier, math):
    """Human-readable label for a card based on its character."""
    n = len(legs)
    avg_odds = sum(abs(l["american_odds"]) for l in legs) / n
    has_longshot = any(l["american_odds"] >= 500 for l in legs)
    same_game = len(set(l["game_id"] for l in legs)) == 1

    if same_game:
        return "Same-Game Combo"
    if tier == "four_leg":
        return "Lottery Shot"
    if has_longshot and tier != "two_leg":
        return "Mixed Lottery"
    if avg_odds < 200:
        return "Safe Stack"
    if tier == "three_leg":
        return "Power Stack"
    return "Value Combo"


# ────────────────────────────────────────────────────────────────────────
# COMBINATION GENERATION
# ────────────────────────────────────────────────────────────────────────

def generate_combos(candidates, leg_count, max_keep=200):
    """
    Generate scored combinations of `leg_count` legs from candidates.

    Hard cap on naive combinations (C(n, k) explodes fast). When there are
    many candidates we restrict to top-edge candidates first.
    """
    # Cap pool by tier to keep computation reasonable
    if leg_count == 2:
        pool = candidates[:20]   # top 20 by edge
    elif leg_count == 3:
        pool = candidates[:15]
    elif leg_count == 4:
        pool = candidates[:12]
    else:
        pool = candidates

    combos = []
    for combo in combinations(pool, leg_count):
        # No duplicate players within a card
        player_ids = [l["player_id"] for l in combo]
        if len(set(player_ids)) != leg_count:
            continue
        rec = make_combo_record(list(combo), tier_for_legs(leg_count), None)
        if rec and rec["score"] > -999:
            combos.append(rec)

    combos.sort(key=lambda r: r["score"], reverse=True)
    return combos[:max_keep]


def tier_for_legs(n):
    return {2: "two_leg", 3: "three_leg", 4: "four_leg"}.get(n, "straight")


# ────────────────────────────────────────────────────────────────────────
# DEDUP / SELECTION
# ────────────────────────────────────────────────────────────────────────

def select_cards(all_combos_by_tier):
    """
    Greedy selection across tiers with overlap penalties + global exposure caps.

    Order: select 2-leggers first (most card-slots, most popular), then
    3-leggers (allowed to share at most 1 leg with any 2-legger), then
    4-leggers (allowed to share at most 2 legs with anything selected).

    Global caps (across ALL tiers combined):
      - Any single player appears on at most MAX_PLAYER_APPEARANCES cards
        (prevents one player blowing up half the menu on an off night)
      - At most MAX_SAME_GAME_CARDS cards where ALL legs are from one game
        (don't spam SGPs — books price them too tight)

    Within a tier, also avoid the same player appearing on two cards of
    that tier — keeps each tier's menu diverse.
    """
    selected = {"two_leg": [], "three_leg": [], "four_leg": []}

    # Global trackers across all tiers
    global_player_counts = {}   # player_id -> count of cards they're on
    same_game_card_count = 0    # how many fully-same-game cards we've selected

    def player_caps_ok(legs):
        """Would adding this combo push any player past MAX_PLAYER_APPEARANCES?"""
        for leg in legs:
            pid = leg["player_id"]
            if global_player_counts.get(pid, 0) >= MAX_PLAYER_APPEARANCES:
                return False
        return True

    def is_same_game_card(legs):
        """All legs share exactly one game_id?"""
        return len(set(l["game_id"] for l in legs)) == 1

    def commit(combo, tier_bucket):
        """Add combo to selection + update global trackers."""
        tier_bucket.append(combo)
        for leg in combo["legs"]:
            pid = leg["player_id"]
            global_player_counts[pid] = global_player_counts.get(pid, 0) + 1

    # ── Two-leggers first ─────────────────────────────────────────────
    two_legs = all_combos_by_tier.get("two_leg", [])
    used_players_2 = set()
    for combo in two_legs:
        if len(selected["two_leg"]) >= CARD_CAPS["two_leg"]:
            break
        if combo["math"]["ev_per_dollar"] < EV_GATES["two_leg"]:
            continue
        legs = combo["legs"]
        player_ids = set(l["player_id"] for l in legs)
        # Within-tier dedup
        if player_ids & used_players_2:
            continue
        # Global player exposure cap
        if not player_caps_ok(legs):
            continue
        # Same-game cap
        if is_same_game_card(legs) and same_game_card_count >= MAX_SAME_GAME_CARDS:
            continue
        commit(combo, selected["two_leg"])
        used_players_2.update(player_ids)
        if is_same_game_card(legs):
            same_game_card_count += 1

    # ── Three-leggers ─────────────────────────────────────────────────
    three_legs = all_combos_by_tier.get("three_leg", [])
    used_players_3 = set()
    for combo in three_legs:
        if len(selected["three_leg"]) >= CARD_CAPS["three_leg"]:
            break
        if combo["math"]["ev_per_dollar"] < EV_GATES["three_leg"]:
            continue
        legs = combo["legs"]
        player_ids = set(l["player_id"] for l in legs)
        # Within-tier dedup
        if player_ids & used_players_3:
            continue
        # Global player exposure cap
        if not player_caps_ok(legs):
            continue
        # Same-game cap
        if is_same_game_card(legs) and same_game_card_count >= MAX_SAME_GAME_CARDS:
            continue
        # Overlap check vs selected 2-leggers
        too_overlapping = False
        for tl in selected["two_leg"]:
            tl_players = set(l["player_id"] for l in tl["legs"])
            if len(player_ids & tl_players) > SHARING_LIMITS["three_vs_two"]:
                too_overlapping = True
                break
        if too_overlapping:
            continue
        commit(combo, selected["three_leg"])
        used_players_3.update(player_ids)
        if is_same_game_card(legs):
            same_game_card_count += 1

    # ── Four-legger ──────────────────────────────────────────────────
    four_legs = all_combos_by_tier.get("four_leg", [])
    for combo in four_legs:
        if len(selected["four_leg"]) >= CARD_CAPS["four_leg"]:
            break
        if combo["math"]["ev_per_dollar"] < EV_GATES["four_leg"]:
            continue
        legs = combo["legs"]
        player_ids = set(l["player_id"] for l in legs)
        # Global player exposure cap
        if not player_caps_ok(legs):
            continue
        # Same-game cap
        if is_same_game_card(legs) and same_game_card_count >= MAX_SAME_GAME_CARDS:
            continue
        # Overlap check vs ALL selected
        too_overlapping = False
        for other in selected["two_leg"] + selected["three_leg"]:
            other_players = set(l["player_id"] for l in other["legs"])
            if len(player_ids & other_players) > SHARING_LIMITS["four_vs_any"]:
                too_overlapping = True
                break
        if too_overlapping:
            continue
        commit(combo, selected["four_leg"])
        if is_same_game_card(legs):
            same_game_card_count += 1

    # ── Market diversification: ensure at least MIN_NON_HR_CARDS in menu ──
    #
    # If the natural selection above produced 0 non-HR cards (everything is
    # HR-heavy), force-swap in the best non-HR alternatives. This protects
    # against nights where HR variance wipes out the whole menu.
    #
    # Strategy: count current non-HR cards. For each shortfall:
    #   1. Search across all tiers' generated combos for the highest-EV
    #      candidate that is purely non-HR AND clears its tier's EV gate
    #   2. Find the LOWEST-EV currently-selected HR-heavy card to evict
    #   3. Swap them, respecting global exposure caps (recompute after each swap)
    enforce_non_hr_mandate(selected, all_combos_by_tier)

    return selected


def is_non_hr_card(combo):
    """A card is 'non-HR' if NONE of its legs are hr_anytime."""
    return all(leg["market"] != "hr_anytime" for leg in combo["legs"])


def enforce_non_hr_mandate(selected, all_combos_by_tier):
    """
    Post-selection: ensure at least MIN_NON_HR_CARDS cards in the menu use
    only non-HR markets. If we're short, evict the lowest-EV HR-heavy card
    and swap in the highest-EV non-HR alternative.

    Mutates `selected` in place.
    """
    def count_non_hr():
        total = 0
        for tier_list in selected.values():
            for combo in tier_list:
                if is_non_hr_card(combo):
                    total += 1
        return total

    def all_selected_tier_pairs():
        """Yield (tier_key, combo) pairs across all selected tiers."""
        for tier_key, tier_list in selected.items():
            for combo in tier_list:
                yield tier_key, combo

    deficit = MIN_NON_HR_CARDS - count_non_hr()
    if deficit <= 0:
        return  # nothing to fix

    log.info("  market diversification: %d non-HR card%s short, attempting swap",
             deficit, "" if deficit == 1 else "s")

    # Build a candidate pool of non-HR combos from each tier, with EV gate already applied.
    non_hr_pool = []   # list of (tier_key, combo)
    for tier_key, combos in all_combos_by_tier.items():
        gate = EV_GATES.get(tier_key, 0.05)
        for combo in combos:
            if is_non_hr_card(combo) and combo["math"]["ev_per_dollar"] >= gate:
                non_hr_pool.append((tier_key, combo))
    # Best non-HR options first (highest EV)
    non_hr_pool.sort(key=lambda x: x[1]["math"]["ev_per_dollar"], reverse=True)

    if not non_hr_pool:
        log.warning("  no non-HR alternatives clear EV gates — slate is HR-only tonight")
        return

    swaps_done = 0
    swaps_needed = deficit

    # Track which non-HR combos we've already considered (avoid retry loops)
    seen_combo_ids = set()

    for tier_key, non_hr_combo in non_hr_pool:
        if swaps_done >= swaps_needed:
            break
        combo_id = id(non_hr_combo)
        if combo_id in seen_combo_ids:
            continue
        seen_combo_ids.add(combo_id)

        non_hr_player_ids = set(l["player_id"] for l in non_hr_combo["legs"])

        # Find the LOWEST-EV currently-selected HR-heavy card to evict.
        # Constraints on the swap:
        #   1. Evicted card must be in the SAME tier (preserve menu shape)
        #   2. Removing it shouldn't drop us below 1 card in that tier when
        #      we still need one there — but if all tiers have multiple, OK
        evictable = []
        for sel_tier_key, sel_combo in all_selected_tier_pairs():
            if sel_tier_key != tier_key:
                continue
            if is_non_hr_card(sel_combo):
                continue   # don't evict an already-non-HR card
            evictable.append(sel_combo)
        if not evictable:
            continue
        # Cheapest (lowest-EV) HR-heavy card in this tier
        evictable.sort(key=lambda c: c["math"]["ev_per_dollar"])
        victim = evictable[0]
        victim_player_ids = set(l["player_id"] for l in victim["legs"])

        # Verify the swap doesn't violate global exposure caps.
        # Recompute player counts after removing victim + adding non_hr_combo.
        proj_player_counts = {}
        for sel_tier_key, sel_combo in all_selected_tier_pairs():
            if sel_combo is victim:
                continue
            for leg in sel_combo["legs"]:
                pid = leg["player_id"]
                proj_player_counts[pid] = proj_player_counts.get(pid, 0) + 1
        # Add non-HR combo's players
        violates_cap = False
        for pid in non_hr_player_ids:
            new_count = proj_player_counts.get(pid, 0) + 1
            if new_count > MAX_PLAYER_APPEARANCES:
                violates_cap = True
                break
        if violates_cap:
            continue

        # Execute swap
        selected[tier_key].remove(victim)
        selected[tier_key].append(non_hr_combo)
        swaps_done += 1
        log.info("  swap %d: evict %s (%s, EV=%.3f) → add %s (%s, EV=%.3f)",
                 swaps_done,
                 victim.get("label", "?"), tier_key, victim["math"]["ev_per_dollar"],
                 non_hr_combo.get("label", "?"), tier_key, non_hr_combo["math"]["ev_per_dollar"])

    if swaps_done < swaps_needed:
        log.warning("  market diversification: only %d/%d swaps possible (exposure caps blocked rest)",
                    swaps_done, swaps_needed)


# ────────────────────────────────────────────────────────────────────────
# DB WRITES
# ────────────────────────────────────────────────────────────────────────

def wipe_today(date_iso):
    """Delete today's existing cards (and cascade to legs) before re-picking."""
    res = sb.table("cards").delete().eq("card_date", date_iso).execute()
    count = len(res.data or [])
    log.info("  wiped %d existing cards for %s", count, date_iso)


def insert_card(date_iso, combo):
    """Insert one card + its legs."""
    math = combo["math"]
    card_payload = {
        "card_date":     date_iso,
        "tier":          combo["tier"],
        "label":         combo["label"],
        "leg_count":     combo["leg_count"],
        "combined_prob": math["combined_prob"],
        "combined_odds": math["american_odds"],
        "decimal_odds":  math["decimal_odds"],
        "implied_prob":  math["implied_prob"],
        "edge":          math["edge"],
        "ev_per_dollar": math["ev_per_dollar"],
        "stake_rec":     combo["stake_rec"],
        "payout_if_hit": combo["payout_if_hit"],
        "status":        "pending",
    }
    card_res = sb.table("cards").insert(card_payload).execute()
    if not card_res.data:
        log.error("  failed to insert card: %s", card_payload)
        return None
    card_id = card_res.data[0]["id"]

    legs_payload = []
    for i, leg in enumerate(combo["legs"], 1):
        legs_payload.append({
            "card_id":         card_id,
            "leg_order":       i,
            "game_id":         leg["game_id"],
            "player_id":       leg["player_id"],
            "player_mlbam_id": leg["player_mlbam_id"],
            "player_name":     leg["player_name"],
            "team_abbrev":     leg["team_abbrev"],
            "opponent_abbrev": leg["opponent_abbrev"],
            "market":          leg["market"],
            "line":            leg["line"],
            "projected_prob":  leg["projected_prob"],
            "no_vig_prob":     leg["no_vig_prob"],
            "american_odds":   leg["american_odds"],
            "edge":            leg["edge"],
            "book":            leg["book"],
            "status":          "pending",
        })
    sb.table("card_legs").insert(legs_payload).execute()
    return card_id


# ────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────

def main():
    today = get_today_iso()
    log.info("🧅 Card picker — slate %s", today)

    games = fetch_today_games(today)
    if not games:
        log.warning("No games for %s — skipping", today)
        return

    candidates = fetch_candidates(today, games)
    log.info("Candidate pool: %d plays clearing per-market floors", len(candidates))

    if len(candidates) < 2:
        log.warning("Too few candidates (need >=2 for 2-leggers) — no cards today")
        wipe_today(today)
        return

    # Slate quality gauge
    strong_plays = [c for c in candidates if c["edge"] >= 0.05]
    if len(strong_plays) >= 5:
        slate_quality = "strong"
    elif len(strong_plays) >= 3:
        slate_quality = "medium"
    else:
        slate_quality = "light"
    log.info("Slate quality: %s (%d plays with 5%%+ edge)",
             slate_quality, len(strong_plays))

    # Generate combinations per tier
    all_combos_by_tier = {}
    for leg_count in [2, 3, 4]:
        if len(candidates) < leg_count:
            all_combos_by_tier[tier_for_legs(leg_count)] = []
            continue
        combos = generate_combos(candidates, leg_count)
        all_combos_by_tier[tier_for_legs(leg_count)] = combos
        log.info("  %d-leg combos generated: %d", leg_count, len(combos))

    # Select cards with dedup
    selected = select_cards(all_combos_by_tier)

    total_selected = sum(len(v) for v in selected.values())
    if total_selected == 0:
        log.info("No combinations cleared EV gates — no cards today")
        wipe_today(today)
        return

    log.info("Selected: %d two-leg, %d three-leg, %d four-leg",
             len(selected["two_leg"]), len(selected["three_leg"]),
             len(selected["four_leg"]))

    # Wipe today's existing cards before inserting new ones
    wipe_today(today)

    # Insert all selected cards
    for tier in ["two_leg", "three_leg", "four_leg"]:
        for combo in selected[tier]:
            cid = insert_card(today, combo)
            math = combo["math"]
            log.info("  ✓ %s [%s] EV=%.3f edge=%.3f odds=%s (id=%s)",
                     combo["tier"], combo["label"],
                     math["ev_per_dollar"], math["edge"],
                     "+%d" % math["american_odds"] if math["american_odds"] >= 0
                                                  else str(math["american_odds"]),
                     cid)
            for i, leg in enumerate(combo["legs"], 1):
                log.info("    leg%d: %s %s @ %s (proj=%.2f%%, edge=+%.2f%%)",
                         i, leg["player_name"], leg["market"],
                         "+%d" % leg["american_odds"] if leg["american_odds"] >= 0
                                                       else str(leg["american_odds"]),
                         leg["projected_prob"] * 100,
                         leg["edge"] * 100)

    log.info("✓ Cards picker complete")


if __name__ == "__main__":
    main()
