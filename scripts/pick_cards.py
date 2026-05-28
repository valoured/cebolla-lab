"""
pick_cards.py — AI-built Cebolla Cards (Phase 1: matchup-first legs).

PHASE 1 — MATCHUP-FIRST LEG POOL (replaces v0.4.0 tier/confidence)
═════════════════════════════════════════════════════════════════
Daily parlay cards built from a flat (batter × market) leg pool. Each
leg is enriched via the same Phase 1 primitives used by pick_pod —
eligibility (Gate A opportunity OR Gate B matchup exception, hard
exclusion at season_pa < 50), a primary_signal in ~[0,1] = max of
three matchup-anchored components, and an EV demote screen that
either qualifies, demotes the suggested stake tier, or disqualifies
the leg.

Legs rank by primary_signal DESC (edge DESC tiebreak). Combinations
sliced from top-N legs per tier, scored by:
    score = ev_per_dollar * 100 + sum(leg.primary_signal) * 5
EV dominates; primary_signal sum breaks ties — a card with strong
matchup signals tiebreaks above one with weak signals at the same EV.

Card-level stake tier derived from mean(leg.primary_signal) + the
WORST EV action across legs (so a card with one warn_drop leg gets
its suggested stake tier bumped one step worse).

CARD TIERS (variable per slate quality):
  two_leg   — up to 6 cards, ev_per_dollar > 0.05
  three_leg — up to 4 cards, ev_per_dollar > 0.08
  four_leg  — up to 2 cards, ev_per_dollar > 0.10

STAKE RECOMMENDATIONS (canonical, frontend can scale):
  two_leg=$10, three_leg=$5, four_leg=$1

MATH (unchanged):
  combined_prob   = ∏(leg_prob) × (1 - correlation_penalty)
  parlay_decimal  = ∏(leg_decimal)
  parlay_american = decimal_to_american(parlay_decimal)
  implied_prob    = 1 / parlay_decimal
  edge            = combined_prob - implied_prob
  ev_per_dollar   = combined_prob × (parlay_decimal - 1) - (1 - combined_prob)

CORRELATION PENALTIES (unchanged):
  same game     -12%   same team -15%   same player -15%

PER-MARKET PROBABILITY FLOORS (sanity, applied BEFORE Phase 1 enrich):
  hr_anytime    >= 0.08
  h_r_rbi_1.5   >= 0.40
  h_r_rbi_2.5   >= 0.20
  hits_yes      >= 0.55
  rbi_yes       >= 0.35
The Phase 1 EV screen handles negative-edge cases via demote/disqualify
downstream; the per-market min_prob is just a data-quality guard.

DEDUP / EXPOSURE (unchanged):
  Greedy selection per tier; 3-leg ≤1 shared leg with any 2-leg;
  4-leg ≤2 shared legs with anything. Player exposure cap 3 cards;
  same-game card cap 1. Market diversification mandates ≥2 non-HR
  cards via post-selection swap.

PERSISTED COLUMNS (Phase 1, added in sql/28):
  primary_signal, primary_signal_source, suggested_stake_tier,
  phase1_metadata — on BOTH cards and card_legs.
  Back-compat: primary_signal is ALSO written to confidence_score
  on both tables until the frontend cuts over.

REMOVED FROM PHASE 1 (function definitions remain in tier_system /
this file for a clean revert):
  - Heat reads (fetch_batter_heat, FROZEN filter, combined_tier).
  - detect_vulnerable_stacks CALL (definition kept for Phase 2 revival).
  - Tier 1/2/Stowers gates (replaced by Phase 1 eligibility).
  - confidence_score derivation (replaced by primary_signal).
  - Per-market min_edge floor (replaced by EV screen).
  - Contact_score helpers (Phase 1 ranks on primary_signal alone).
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from itertools import combinations

from supabase import create_client
from dotenv import load_dotenv

from tier_system import (
    # Phase 1 primitives
    evaluate_eligibility,
    compute_primary_signal_v3,
    apply_ev_screen,
    suggested_stake_tier_for,
    pitch_family_for,
    # Reused infra
    primary_pitch_type,
    load_thresholds,
    configure,
    _cfg_num,
)

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

# CRITICAL: must stay in sync with compute_projections.py:MODEL_VERSION and
# pick_pod.py:REQUIRED_MODEL_VERSION. Mismatch → empty candidate pool.
REQUIRED_MODEL_VERSION = "v0.4.0"

# Per-market projected_prob sanity floors. NOT a Phase 1 ranking gate — the
# EV screen handles negative-edge cases. This guards against shipping legs
# whose projected_prob is way below the line baseline (e.g. 1+ hits @ 8%),
# which is almost always a data issue rather than a real edge.
MARKET_MIN_PROB = {
    "hr_anytime":  0.08,
    "h_r_rbi_1.5": 0.40,
    "h_r_rbi_2.5": 0.20,
    "hits_yes":    0.55,
    "rbi_yes":     0.35,
}

# Stake recommendations by tier (canonical — frontend can scale linearly)
STAKE_REC = {
    "two_leg":   10.00,
    "three_leg":  5.00,
    "four_leg":   1.00,
}

# Card-level EV gates by tier — distinct from the per-leg EV screen.
# A card must clear its tier's gate to ship.
EV_GATES = {
    "two_leg":   0.05,
    "three_leg": 0.08,
    "four_leg":  0.10,
}

# Card count caps by tier
CARD_CAPS = {
    "two_leg":   6,
    "three_leg": 4,
    "four_leg":  2,
}

# Correlation penalties applied to combined_prob. SGP penalty intentionally
# HIGHER than initial design — SGPs usually carry unfavorable correlation
# and the books juice the prices accordingly.
CORRELATION_PENALTIES = {
    "same_game":   0.12,
    "same_team":   0.15,
    "same_player": 0.15,
}

# Dedup: max shared legs between selected cards across tiers
SHARING_LIMITS = {
    "three_vs_two": 1,
    "four_vs_any":  2,
}

# Global exposure caps across the entire daily card menu
MAX_PLAYER_APPEARANCES = 3
MAX_SAME_GAME_CARDS    = 1

# Market diversification: mandate at least this many cards use only non-HR
# markets (Hits / RBI / HRR). A "non-HR card" has zero legs in hr_anytime.
MIN_NON_HR_CARDS = 2


# ────────────────────────────────────────────────────────────────────────
# DATE
# ────────────────────────────────────────────────────────────────────────

def get_today_iso():
    """ET-relative slate date — same as pick_pod."""
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


# ────────────────────────────────────────────────────────────────────────
# FETCHERS
# ────────────────────────────────────────────────────────────────────────

def fetch_today_games(date_iso):
    """Pull today's games + team abbrevs + probable pitchers."""
    res = sb.table("games") \
        .select("id, away_team_id, home_team_id, "
                "home_pitcher_id, away_pitcher_id, "
                "away_team:teams!games_away_team_id_fkey(abbrev), "
                "home_team:teams!games_home_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .execute()
    return res.data or []


def fetch_starting_pitcher_for_game(games):
    """
    {(game_id, team_id): pitcher_id} keyed by the team the pitcher pitches FOR.
    Look up a batter's OPPONENT team in this dict to find their opposing pitcher.
    """
    out = {}
    for g in games:
        home_pid = g.get("home_pitcher_id")
        away_pid = g.get("away_pitcher_id")
        if home_pid:
            out[(g["id"], g["home_team_id"])] = home_pid
        if away_pid:
            out[(g["id"], g["away_team_id"])] = away_pid
    return out


def fetch_batter_l14_stats(player_ids, season):
    """
    L14 batter_stats — used as the L7 fallback for the recent_power_form
    component of primary_signal_v3.
    """
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, xslg, hit_per_pa, xba") \
        .eq("season", season) \
        .eq("window_type", "l14") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_batter_l7_stats(player_ids, season):
    """
    L7 batter_stats — primary input for recent_power_form (L7 xSLG / 2.0).
    pull_savant.py writes l7 alongside l14/l30/season on every run.
    """
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, xslg, hit_per_pa") \
        .eq("season", season) \
        .eq("window_type", "l7") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_batter_season_stats(player_ids, season):
    """
    Season-window batter_stats — drives Gate A (season_pa, family-summed
    pitch_type_pa via by_pitch_type) and Gate B (season barrel_pct, xslg
    power floors). by_pitch_type also feeds observed_vs_pitch_type.
    """
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, xslg, xba, hit_per_pa, by_pitch_type") \
        .eq("season", season) \
        .eq("window_type", "season") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_pitcher_arsenals(pitcher_ids, season):
    """
    Pitcher arsenals across both stances. primary_pitch_type() sums usage_pct
    across stances to pick the overall most-thrown pitch.
    """
    if not pitcher_ids:
        return {}
    res = sb.table("pitcher_arsenals") \
        .select("pitcher_id, pitch_type, vs_stance, usage_pct, hr_pct") \
        .eq("season", season) \
        .eq("window_type", "season") \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    out = {}
    for r in res.data or []:
        out.setdefault(r["pitcher_id"], []).append(r)
    return out


def fetch_bvp(batter_ids, pitcher_ids):
    """
    Career BvP per (batter, pitcher). Expanded vs prior versions to include
    ab/hits/hr for Gate B + observed_vs_pitcher primary signal.
    """
    if not batter_ids or not pitcher_ids:
        return {}
    res = sb.table("bvp_history") \
        .select("batter_id, pitcher_id, pa, ab, hits, hr, avg, ops") \
        .in_("batter_id", batter_ids) \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    return {(r["batter_id"], r["pitcher_id"]): r for r in (res.data or [])}


# ────────────────────────────────────────────────────────────────────────
# PHASE 1 FORENSICS HELPERS
# ────────────────────────────────────────────────────────────────────────

def _phase1_signal_components(bvp_row, by_pitch, pitcher_primary,
                              l7_stats, l14_stats, cfg):
    """
    Capture the three raw primary-signal component values + power-form source
    for the phase1_metadata.primary_signal_components forensics dict. Mirrors
    the gating in compute_primary_signal_v3 — reports ALL three values
    regardless of which won the max. Duplicate of pick_pod's helper (the
    function is intentionally kept local to each picker; if drift becomes
    a concern, lift to tier_system).
    """
    bvp_v = None
    if bvp_row:
        try:
            ab = int(bvp_row.get("ab") or 0)
            hr = int(bvp_row.get("hr") or 0)
            if ab >= int(_cfg_num(cfg, "primary_bvp_ab_min", 8)) and ab > 0:
                bvp_v = round(hr / ab, 5)
        except (TypeError, ValueError):
            pass

    pt_v = None
    if pitcher_primary and by_pitch:
        entry = by_pitch.get(pitcher_primary)
        family = pitch_family_for(pitcher_primary)
        if entry and isinstance(entry, dict) and family:
            fam_pa = 0
            for label, sub in by_pitch.items():
                if pitch_family_for(label) != family or not isinstance(sub, dict):
                    continue
                try:
                    fam_pa += int(sub.get("pa") or 0)
                except (TypeError, ValueError):
                    continue
            if fam_pa >= int(_cfg_num(cfg, "primary_pitch_type_pa_min", 20)) \
               and entry.get("hr_pct") is not None:
                try:
                    pt_v = round(float(entry["hr_pct"]) / 100.0, 5)
                except (TypeError, ValueError):
                    pass

    divisor = _cfg_num(cfg, "primary_l7_xslg_divisor", 2.0)
    pf_v = None
    pf_source = None
    if l7_stats and l7_stats.get("xslg") is not None:
        try:
            if int(l7_stats.get("pa") or 0) >= int(_cfg_num(cfg, "primary_l7_pa_min", 10)) \
               and divisor:
                pf_v = round(float(l7_stats["xslg"]) / divisor, 5)
                pf_source = "l7"
        except (TypeError, ValueError):
            pass
    if pf_v is None and l14_stats and l14_stats.get("xslg") is not None and divisor:
        try:
            pf_v = round(float(l14_stats["xslg"]) / divisor, 5)
            pf_source = "l14"
        except (TypeError, ValueError):
            pass

    return {
        "bvp_observed": bvp_v,
        "pitch_type_observed": pt_v,
        "power_form": pf_v,
        "power_form_source": pf_source,
    }


def _log_signal_distribution(unique_batters, cfg=None):
    """
    Locked-decision audit log — same format as pick_pod. Caller MUST pass a
    de-duplicated list (one entry per unique batter); legs of the same batter
    share primary_signal so counting per leg would skew the distribution.
    """
    if not unique_batters:
        log.info("Signal distribution: empty pool")
        return
    sigs = sorted(float(c.get("primary_signal") or 0.0) for c in unique_batters)
    n = len(sigs)
    s_min, s_max = sigs[0], sigs[-1]
    if n % 2:
        s_med = sigs[n // 2]
    else:
        s_med = (sigs[n // 2 - 1] + sigs[n // 2]) / 2

    lock_min    = _cfg_num(cfg, "stake_tier_lock_min", 0.65)
    safe_min    = _cfg_num(cfg, "stake_tier_safe_min", 0.50)
    risky_min   = _cfg_num(cfg, "stake_tier_risky_min", 0.30)
    lottery_min = _cfg_num(cfg, "stake_tier_lottery_min", 0.15)

    counts = {"lock": 0, "safe": 0, "risky": 0, "lottery": 0, "donation": 0}
    for s in sigs:
        if s >= lock_min:      counts["lock"]    += 1
        elif s >= safe_min:    counts["safe"]    += 1
        elif s >= risky_min:   counts["risky"]   += 1
        elif s >= lottery_min: counts["lottery"] += 1
        else:                  counts["donation"] += 1

    src_counts = {}
    for c in unique_batters:
        src = c.get("primary_signal_source") or "none"
        src_counts[src] = src_counts.get(src, 0) + 1

    log.info("Signal distribution: n=%d  min=%.3f  med=%.3f  max=%.3f",
             n, s_min, s_med, s_max)
    log.info("  by stake tier: lock=%d safe=%d risky=%d lottery=%d donation=%d",
             counts["lock"], counts["safe"], counts["risky"],
             counts["lottery"], counts["donation"])
    log.info("  by source:     bvp_observed=%d  pitch_type_observed=%d  "
             "l7_power_form=%d  l14_power_form=%d  none=%d",
             src_counts.get("bvp_observed", 0),
             src_counts.get("pitch_type_observed", 0),
             src_counts.get("l7_power_form", 0),
             src_counts.get("l14_power_form", 0),
             src_counts.get("none", 0))


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
# PHASE 1 LEG ENRICHMENT
# ────────────────────────────────────────────────────────────────────────

def _enrich_leg(candidate, games_by_id, players_by_id,
                batter_stats_l14, batter_stats_l7, batter_stats_season,
                pitcher_arsenals, bvp_pairs, starter_by_game_team, cfg):
    """
    Phase 1 enrichment for one leg. Mutates `candidate` in place with
    primary_signal, primary_signal_source, gate, ev_action, ev_warning,
    suggested_stake_tier, phase1_metadata, opposing_pitcher_id. Returns
    True if qualified, False on hard exclusion / ineligibility / EV
    disqualify.
    """
    game = games_by_id.get(candidate["game_id"])
    player = players_by_id.get(candidate["player_id"])
    if not game or not player:
        return False

    is_home = player.get("team_id") == game.get("home_team_id")
    opposing_team_id = game.get("away_team_id") if is_home else game.get("home_team_id")
    opposing_pitcher_id = starter_by_game_team.get((candidate["game_id"], opposing_team_id))
    # Persist on the leg so Phase 2 stack detection can group by opposing
    # pitcher (the call is removed in Phase 1 but the leg shape is preserved).
    candidate["opposing_pitcher_id"] = opposing_pitcher_id

    pitcher_primary = None
    if opposing_pitcher_id:
        arsenal = pitcher_arsenals.get(opposing_pitcher_id) or []
        pitcher_primary = primary_pitch_type(arsenal)

    bstats_season = batter_stats_season.get(candidate["player_id"])
    by_pitch = (bstats_season or {}).get("by_pitch_type") if bstats_season else None
    bvp_row = bvp_pairs.get((candidate["player_id"], opposing_pitcher_id)) if opposing_pitcher_id else None
    l14_stats = batter_stats_l14.get(candidate["player_id"])
    l7_stats = batter_stats_l7.get(candidate["player_id"])

    passed, gate, elig_detail = evaluate_eligibility(
        bstats_season, by_pitch, pitcher_primary, bvp_row, cfg=cfg
    )
    if not passed:
        return False

    signal, source = compute_primary_signal_v3(
        bvp_row, by_pitch, pitcher_primary, l7_stats, l14_stats, cfg=cfg
    )
    components = _phase1_signal_components(
        bvp_row, by_pitch, pitcher_primary, l7_stats, l14_stats, cfg
    )

    edge_val = candidate.get("edge")
    ev_action, ev_warning = apply_ev_screen(edge_val, cfg=cfg)
    if ev_action == "disqualify":
        return False

    stake_tier = suggested_stake_tier_for(signal, ev_action, cfg=cfg)

    candidate["primary_signal"] = signal
    candidate["primary_signal_source"] = source
    candidate["gate"] = gate
    candidate["ev_action"] = ev_action
    candidate["ev_warning"] = ev_warning
    candidate["suggested_stake_tier"] = stake_tier
    candidate["phase1_metadata"] = {
        "gate": gate,
        "ev_action": ev_action,
        "ev_warning": ev_warning,
        "eligibility_detail": elig_detail,
        "primary_signal_components": components,
        "primary_signal_source": source,
    }
    return True


# ────────────────────────────────────────────────────────────────────────
# STACK DETECTION (DEFINITION ONLY — NOT CALLED IN PHASE 1)
# ────────────────────────────────────────────────────────────────────────
# detect_vulnerable_stacks was the v2 Patch 5 stack-boost path: ≥3 qualified
# batters from the same team facing a vulnerable opposing pitcher had their
# confidence_score bumped by stack_boost. Phase 1 ranks on primary_signal
# (not confidence_score), so the boost no longer makes sense in this shape.
#
# We KEEP the function definition for a Phase 2 revival (a re-designed
# stack bonus can bump primary_signal of co-located batters instead). The
# call site in fetch_candidates is intentionally REMOVED in Phase 1.

def detect_vulnerable_stacks(candidates, pitcher_hr9, cfg=None):
    """
    [PHASE 1: NOT CALLED] v2 stack detection. Identifies ≥stack_min_candidates
    qualified batters from the SAME team in the SAME game facing a vulnerable
    opposing pitcher (hr_per_9 >= stack_hr9_min OR xfip >= stack_xfip_min).
    Each leg in a detected stack gets stack_boost added to its confidence_score
    (tier letter re-derived). Phase 2 should re-wire this against primary_signal
    or remove entirely.
    """
    stack_min = int(_cfg_num(cfg, "stack_min_candidates", 3))
    hr9_min   = _cfg_num(cfg, "stack_hr9_min", 1.3)
    xfip_min  = _cfg_num(cfg, "stack_xfip_min", 4.25)
    boost     = _cfg_num(cfg, "stack_boost", 0.10)

    groups = {}
    for c in candidates:
        groups.setdefault((c.get("game_id"), c.get("team_id")), []).append(c)

    stacked_legs = 0
    stacks_found = 0
    for (game_id, team_id), legs in groups.items():
        if len(legs) < stack_min:
            continue
        opp_pid = legs[0].get("opposing_pitcher_id")
        if not opp_pid:
            continue
        pstats = pitcher_hr9.get(opp_pid) or {}
        hr9 = pstats.get("hr_per_9")
        xfip = pstats.get("xfip")
        vulnerable = False
        if hr9 is not None and hr9 >= hr9_min:
            vulnerable = True
        if xfip is not None and xfip >= xfip_min:
            vulnerable = True
        if not vulnerable:
            continue
        stacks_found += 1
        for c in legs:
            c["is_stacked"] = True
            new_conf = max(0.0, min(1.0, (c.get("confidence_score") or 0.0) + boost))
            c["confidence_score"] = round(new_conf, 3)
            meta = c.setdefault("tier_metadata", {})
            meta["stack"] = {
                "stacked": True, "team_id": team_id, "opposing_pitcher_id": opp_pid,
                "stack_size": len(legs), "stack_boost": boost, "pitcher_hr_per_9": hr9,
            }
            stacked_legs += 1
    if stacks_found:
        log.info("Patch 5: %d vulnerable stack(s), %d legs boosted (+%.2f conf)",
                 stacks_found, stacked_legs, boost)
    return stacked_legs


# ────────────────────────────────────────────────────────────────────────
# CANDIDATE POOL
# ────────────────────────────────────────────────────────────────────────

def fetch_candidates(date_iso, games, cfg=None):
    """
    Build the Phase 1 flat leg pool across all 5 markets:
      1. Pull projection rows at REQUIRED_MODEL_VERSION.
      2. Apply per-market projected_prob sanity floor (MARKET_MIN_PROB).
      3. Build leg dicts with game/player context + odds + decimal_odds.
      4. Enrich each leg via _enrich_leg (eligibility + primary_signal + EV).
         Disqualified legs drop out.
      5. Log signal distribution across UNIQUE BATTERS (legs of one batter
         share primary_signal — counting per leg would skew the histogram).
      6. Sort by (primary_signal DESC, edge DESC).

    Returns the qualified leg list, ready for combo generation.
    """
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}
    season = datetime.now(timezone.utc).year

    markets = list(MARKET_MIN_PROB.keys())
    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, "
                "edge, best_american_odds, best_book, model_version") \
        .in_("game_id", game_ids) \
        .in_("market", markets) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .not_.is_("best_american_odds", "null") \
        .execute()
    projections = proj_res.data or []
    if not projections:
        return []

    # Player metadata
    player_ids = list({p["player_id"] for p in projections if p.get("player_id")})
    players_res = sb.table("players").select("id, name, mlbam_id, team_id, position") \
        .in_("id", player_ids).execute()
    players_by_id = {p["id"]: p for p in (players_res.data or [])}

    # ── Apply per-market prob floor + assemble raw leg dicts ──
    floor_passed = []
    floor_dropped = 0
    for p in projections:
        floor = MARKET_MIN_PROB.get(p["market"])
        if floor is None:
            continue
        prob = float(p.get("projected_prob") or 0)
        if prob < floor:
            floor_dropped += 1
            continue

        player = players_by_id.get(p["player_id"])
        if not player:
            continue
        game = games_by_id.get(p["game_id"])
        if not game:
            continue

        american = int(p["best_american_odds"])
        decimal = american_to_decimal(american)
        if decimal is None or decimal <= 1:
            continue

        # Parse line from market string for storage
        line = None
        if p["market"].startswith("h_r_rbi_"):
            try:
                line = float(p["market"].split("_")[-1])
            except (ValueError, IndexError):
                line = None
        elif p["market"] == "hr_anytime":
            line = 0.5

        is_home = player["team_id"] == game["home_team_id"]
        own = (game["home_team"] if is_home else game["away_team"]) or {}
        opp = (game["away_team"] if is_home else game["home_team"]) or {}

        floor_passed.append({
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
            "edge":            float(p.get("edge") or 0),
            "book":            p.get("best_book") or "draftkings",
            "model_version":   p.get("model_version"),
        })

    log.info("After per-market prob floor: %d legs (dropped %d)",
             len(floor_passed), floor_dropped)
    if not floor_passed:
        return []

    # ── Phase 1 input prefetch (only for batters that cleared the floor) ──
    qualifying_player_ids = list({c["player_id"] for c in floor_passed})
    log.info("Fetching Phase 1 inputs for %d candidates...", len(qualifying_player_ids))

    batter_stats_l14 = fetch_batter_l14_stats(qualifying_player_ids, season)
    log.info("  L14 stats:    %d rows", len(batter_stats_l14))
    batter_stats_l7 = fetch_batter_l7_stats(qualifying_player_ids, season)
    log.info("  L7 stats:     %d rows", len(batter_stats_l7))
    batter_stats_season = fetch_batter_season_stats(qualifying_player_ids, season)
    log.info("  Season stats: %d rows", len(batter_stats_season))

    starter_by_game_team = fetch_starting_pitcher_for_game(games)
    pitcher_ids = list({pid for pid in starter_by_game_team.values() if pid})
    log.info("  Starting pitchers: %d", len(pitcher_ids))
    pitcher_arsenals = fetch_pitcher_arsenals(pitcher_ids, season)
    log.info("  Pitcher arsenals:  %d", len(pitcher_arsenals))

    bvp_pairs = fetch_bvp(qualifying_player_ids, pitcher_ids)
    log.info("  BvP pairs: %d", len(bvp_pairs))

    # ── Phase 1 per-leg enrichment ──
    qualified = []
    dropped_count = 0
    errors_by_type = {}
    for cand in floor_passed:
        try:
            ok = _enrich_leg(
                cand, games_by_id, players_by_id,
                batter_stats_l14, batter_stats_l7, batter_stats_season,
                pitcher_arsenals, bvp_pairs, starter_by_game_team, cfg=cfg,
            )
        except Exception as e:
            etype = type(e).__name__
            errors_by_type[etype] = errors_by_type.get(etype, 0) + 1
            log.warning("Phase 1 enrich skipped player_id=%s market=%s: %s: %s",
                        cand.get("player_id"), cand.get("market"), etype, e)
            continue
        if ok:
            qualified.append(cand)
        else:
            dropped_count += 1

    if errors_by_type:
        log.warning("Phase 1: %d candidate(s) skipped due to enrichment errors: %s",
                    sum(errors_by_type.values()), errors_by_type)
    log.info("Phase 1 enrich: %d qualified, %d dropped (hard excl / ineligible / EV disqualify)",
             len(qualified), dropped_count)

    # ── Signal distribution log (per unique batter, not per leg) ──
    seen_batters = set()
    unique_batter_view = []
    for c in qualified:
        pid = c.get("player_id")
        if pid in seen_batters:
            continue
        seen_batters.add(pid)
        unique_batter_view.append(c)
    _log_signal_distribution(unique_batter_view, cfg=cfg)

    # ── Sort legs by (primary_signal DESC, edge DESC) ──
    qualified.sort(
        key=lambda c: ((c.get("primary_signal") or 0.0), c["edge"]),
        reverse=True,
    )
    return qualified


# ────────────────────────────────────────────────────────────────────────
# COMBINATION SCORING
# ────────────────────────────────────────────────────────────────────────

def correlation_penalty(legs):
    """
    Additive correlation penalty (capped at 0.40) to subtract from the naive
    product-of-probs. Same shape as v0.4.0; the penalty constants are tuned to
    discourage SGPs which the books usually price tightly.
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
    Parlay math. Returns {combined_prob, decimal_odds, american_odds,
    implied_prob, edge, ev_per_dollar, correlation_penalty}.
    """
    if not legs:
        return None

    raw_combined = 1.0
    for leg in legs:
        raw_combined *= leg["projected_prob"]
    penalty = correlation_penalty(legs)
    combined_prob = raw_combined * (1 - penalty)

    parlay_decimal = 1.0
    for leg in legs:
        parlay_decimal *= leg["decimal_odds"]

    parlay_american = decimal_to_american(parlay_decimal)
    implied = implied_from_decimal(parlay_decimal)

    edge = combined_prob - implied
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
    Objective function (Phase 1):
      score = ev_per_dollar * 100 + sum(leg.primary_signal) * 5

    EV (×100) dominates. The sum of per-leg primary_signal (each in ~[0,1])
    breaks ties — a card whose legs all clear with strong matchup signals
    scores higher than one with weak matchup signals at similar EV.
    """
    if not math or math["ev_per_dollar"] is None:
        return -999
    sig_sum = sum(float(l.get("primary_signal") or 0) for l in legs)
    return math["ev_per_dollar"] * 100 + sig_sum * 5


def _worst_ev_action(legs):
    """Worst-of: full < drop < warn_drop. Used for card-level stake tier."""
    actions = {l.get("ev_action") for l in legs}
    if "warn_drop" in actions:
        return "warn_drop"
    if "drop" in actions:
        return "drop"
    return "full"


def make_combo_record(legs, tier, slate_quality, cfg=None):
    """
    Bundle a combination's math + legs + tier into a finalized record.

    Phase 1 card-level fields:
      primary_signal       = mean(leg.primary_signal)
      suggested_stake_tier = suggested_stake_tier_for(card_primary_signal,
                                                       worst_ev_action_across_legs)
      phase1_metadata      = {per_leg: [...], card_primary_signal,
                              worst_ev_action, card_suggested_stake_tier}

    Per locked back-compat decision, primary_signal is ALSO assigned to
    confidence_score on both card and per-leg writes (in insert_card).
    """
    math = parlay_math(legs)
    if not math:
        return None

    score = score_combination(math, legs)
    stake = STAKE_REC[tier]
    profit_if_hit = round(stake * (math["decimal_odds"] - 1), 2)

    leg_sigs = [float(l.get("primary_signal") or 0.0) for l in legs]
    card_primary_signal = round(sum(leg_sigs) / len(leg_sigs), 5) if leg_sigs else 0.0
    worst_action = _worst_ev_action(legs)
    card_stake_tier = suggested_stake_tier_for(card_primary_signal, worst_action, cfg)

    phase1_metadata = {
        "per_leg": [
            {
                "player_id": l.get("player_id"),
                "player_name": l.get("player_name"),
                "market": l.get("market"),
                "gate": l.get("gate"),
                "ev_action": l.get("ev_action"),
                "primary_signal": l.get("primary_signal"),
                "primary_signal_source": l.get("primary_signal_source"),
            }
            for l in legs
        ],
        "card_primary_signal": card_primary_signal,
        "worst_ev_action": worst_action,
        "card_suggested_stake_tier": card_stake_tier,
    }

    return {
        "tier":          tier,
        "leg_count":     len(legs),
        "legs":          legs,
        "math":          math,
        "score":         score,
        "stake_rec":     stake,
        "payout_if_hit": profit_if_hit,
        "label":         label_for(legs, tier, math),
        "primary_signal":       card_primary_signal,
        "suggested_stake_tier": card_stake_tier,
        "phase1_metadata":      phase1_metadata,
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

def generate_combos(candidates, leg_count, max_keep=200, cfg=None):
    """
    Generate scored combinations of `leg_count` legs.

    Candidates arrive pre-sorted by (primary_signal DESC, edge DESC) from
    fetch_candidates. Pool sizes (20/15/12) unchanged from v0.4.0 — those
    bounds control combinatorial blow-up, not selection quality.

    Combos are returned ordered by card primary_signal DESC then objective
    score DESC; select_cards applies the EV eligibility gate during selection.
    """
    if leg_count == 2:
        pool = candidates[:20]
    elif leg_count == 3:
        pool = candidates[:15]
    elif leg_count == 4:
        pool = candidates[:12]
    else:
        pool = candidates

    combos = []
    for combo in combinations(pool, leg_count):
        player_ids = [l["player_id"] for l in combo]
        if len(set(player_ids)) != leg_count:
            continue
        rec = make_combo_record(list(combo), tier_for_legs(leg_count), None, cfg=cfg)
        if rec and rec["score"] > -999:
            combos.append(rec)

    combos.sort(
        key=lambda r: ((r.get("primary_signal") or 0.0), r["score"]),
        reverse=True,
    )
    return combos[:max_keep]


def tier_for_legs(n):
    return {2: "two_leg", 3: "three_leg", 4: "four_leg"}.get(n, "straight")


# ────────────────────────────────────────────────────────────────────────
# DEDUP / SELECTION
# ────────────────────────────────────────────────────────────────────────

def select_cards(all_combos_by_tier):
    """
    Greedy selection across tiers with overlap penalties + global exposure caps.

    Two-stage selection:
      1. Eligibility gate: each combo must clear its tier's EV threshold
         (EV_GATES[tier]).
      2. Ranking: among eligible combos, walks in primary_signal DESC order
         (combos arrive pre-sorted from generate_combos).

    Order: 2-leggers first, then 3-leggers (≤1 leg shared with any 2-legger),
    then 4-leggers (≤2 legs shared with anything selected). Global caps:
    any player ≤ MAX_PLAYER_APPEARANCES cards, ≤ MAX_SAME_GAME_CARDS fully
    same-game cards.
    """
    selected = {"two_leg": [], "three_leg": [], "four_leg": []}

    global_player_counts = {}
    same_game_card_count = 0

    def player_caps_ok(legs):
        for leg in legs:
            pid = leg["player_id"]
            if global_player_counts.get(pid, 0) >= MAX_PLAYER_APPEARANCES:
                return False
        return True

    def is_same_game_card(legs):
        return len(set(l["game_id"] for l in legs)) == 1

    def commit(combo, tier_bucket):
        tier_bucket.append(combo)
        for leg in combo["legs"]:
            pid = leg["player_id"]
            global_player_counts[pid] = global_player_counts.get(pid, 0) + 1

    # ── Two-leggers ──────────────────────────────────────────────────
    two_legs = all_combos_by_tier.get("two_leg", [])
    used_players_2 = set()
    for combo in two_legs:
        if len(selected["two_leg"]) >= CARD_CAPS["two_leg"]:
            break
        if combo["math"]["ev_per_dollar"] < EV_GATES["two_leg"]:
            continue
        legs = combo["legs"]
        player_ids = set(l["player_id"] for l in legs)
        if player_ids & used_players_2:
            continue
        if not player_caps_ok(legs):
            continue
        if is_same_game_card(legs) and same_game_card_count >= MAX_SAME_GAME_CARDS:
            continue
        commit(combo, selected["two_leg"])
        used_players_2.update(player_ids)
        if is_same_game_card(legs):
            same_game_card_count += 1

    # ── Three-leggers ────────────────────────────────────────────────
    three_legs = all_combos_by_tier.get("three_leg", [])
    used_players_3 = set()
    for combo in three_legs:
        if len(selected["three_leg"]) >= CARD_CAPS["three_leg"]:
            break
        if combo["math"]["ev_per_dollar"] < EV_GATES["three_leg"]:
            continue
        legs = combo["legs"]
        player_ids = set(l["player_id"] for l in legs)
        if player_ids & used_players_3:
            continue
        if not player_caps_ok(legs):
            continue
        if is_same_game_card(legs) and same_game_card_count >= MAX_SAME_GAME_CARDS:
            continue
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

    # ── Four-leggers ─────────────────────────────────────────────────
    four_legs = all_combos_by_tier.get("four_leg", [])
    for combo in four_legs:
        if len(selected["four_leg"]) >= CARD_CAPS["four_leg"]:
            break
        if combo["math"]["ev_per_dollar"] < EV_GATES["four_leg"]:
            continue
        legs = combo["legs"]
        player_ids = set(l["player_id"] for l in legs)
        if not player_caps_ok(legs):
            continue
        if is_same_game_card(legs) and same_game_card_count >= MAX_SAME_GAME_CARDS:
            continue
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

    # ── Market diversification: ensure ≥ MIN_NON_HR_CARDS in menu ────
    enforce_non_hr_mandate(selected, all_combos_by_tier)
    return selected


def is_non_hr_card(combo):
    """A card is 'non-HR' if NONE of its legs are hr_anytime."""
    return all(leg["market"] != "hr_anytime" for leg in combo["legs"])


def enforce_non_hr_mandate(selected, all_combos_by_tier):
    """
    Post-selection: ensure ≥ MIN_NON_HR_CARDS cards use only non-HR markets.
    Evicts the lowest-EV HR-heavy card in a tier and swaps in the highest-EV
    non-HR alternative from the same tier that also clears its EV gate.
    Respects global exposure caps; mutates `selected` in place.
    """
    def count_non_hr():
        total = 0
        for tier_list in selected.values():
            for combo in tier_list:
                if is_non_hr_card(combo):
                    total += 1
        return total

    def all_selected_tier_pairs():
        for tier_key, tier_list in selected.items():
            for combo in tier_list:
                yield tier_key, combo

    deficit = MIN_NON_HR_CARDS - count_non_hr()
    if deficit <= 0:
        return

    log.info("  market diversification: %d non-HR card%s short, attempting swap",
             deficit, "" if deficit == 1 else "s")

    non_hr_pool = []
    for tier_key, combos in all_combos_by_tier.items():
        gate = EV_GATES.get(tier_key, 0.05)
        for combo in combos:
            if is_non_hr_card(combo) and combo["math"]["ev_per_dollar"] >= gate:
                non_hr_pool.append((tier_key, combo))
    non_hr_pool.sort(key=lambda x: x[1]["math"]["ev_per_dollar"], reverse=True)

    if not non_hr_pool:
        log.warning("  no non-HR alternatives clear EV gates — slate is HR-only tonight")
        return

    swaps_done = 0
    swaps_needed = deficit
    seen_combo_ids = set()

    for tier_key, non_hr_combo in non_hr_pool:
        if swaps_done >= swaps_needed:
            break
        combo_id = id(non_hr_combo)
        if combo_id in seen_combo_ids:
            continue
        seen_combo_ids.add(combo_id)

        non_hr_player_ids = set(l["player_id"] for l in non_hr_combo["legs"])

        evictable = []
        for sel_tier_key, sel_combo in all_selected_tier_pairs():
            if sel_tier_key != tier_key:
                continue
            if is_non_hr_card(sel_combo):
                continue
            evictable.append(sel_combo)
        if not evictable:
            continue
        evictable.sort(key=lambda c: c["math"]["ev_per_dollar"])
        victim = evictable[0]

        proj_player_counts = {}
        for sel_tier_key, sel_combo in all_selected_tier_pairs():
            if sel_combo is victim:
                continue
            for leg in sel_combo["legs"]:
                pid = leg["player_id"]
                proj_player_counts[pid] = proj_player_counts.get(pid, 0) + 1
        violates_cap = False
        for pid in non_hr_player_ids:
            new_count = proj_player_counts.get(pid, 0) + 1
            if new_count > MAX_PLAYER_APPEARANCES:
                violates_cap = True
                break
        if violates_cap:
            continue

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
    """
    Delete today's PENDING cards (cascades to legs) before re-picking.
    Settled cards (win/loss/void) are PRESERVED — they're permanent receipts.
    """
    res = sb.table("cards").delete() \
        .eq("card_date", date_iso) \
        .eq("status", "pending") \
        .execute()
    count = len(res.data or [])
    log.info("  wiped %d pending cards for %s (settled cards preserved)",
             count, date_iso)


def combo_fingerprint(legs):
    """
    Deterministic fingerprint: 'p:1234|m:hr_anytime|l:0.5;p:5678|m:h_r_rbi_1.5|l:1.5'.
    Two combos with the same (player_id, market, line) tuples produce the same
    fingerprint regardless of leg ordering. Used to dedup against settled cards
    already in the DB on re-runs.
    """
    parts = []
    for leg in sorted(legs, key=lambda l: (l["player_id"], l["market"], l.get("line") or 0)):
        pid = leg["player_id"]
        mkt = leg["market"]
        line = leg.get("line") if leg.get("line") is not None else 0.5
        parts.append(f"p:{pid}|m:{mkt}|l:{line}")
    return ";".join(parts)


def fetch_existing_fingerprints(date_iso):
    """
    Fingerprints of all cards already in DB for the given date. Returns a set.
    Used to prevent re-inserting duplicates of already-settled combos when the
    picker re-runs on the same slate.
    """
    cards_res = sb.table("cards").select("id").eq("card_date", date_iso).execute()
    card_ids = [c["id"] for c in (cards_res.data or [])]
    if not card_ids:
        return set()

    legs_res = sb.table("card_legs") \
        .select("card_id, player_id, market, line") \
        .in_("card_id", card_ids) \
        .execute()
    legs_by_card = {}
    for leg in (legs_res.data or []):
        legs_by_card.setdefault(leg["card_id"], []).append(leg)

    fingerprints = set()
    for cid, legs in legs_by_card.items():
        fingerprints.add(combo_fingerprint(legs))
    return fingerprints


def insert_card(date_iso, combo):
    """
    Insert one card + its legs.

    Phase 1 columns written on cards + card_legs:
      primary_signal, primary_signal_source (NULL on card; per-leg on legs),
      suggested_stake_tier, phase1_metadata.

    Back-compat dual-write: primary_signal → confidence_score on BOTH cards
    and card_legs so the existing frontend renders the new picks until UI
    cuts over to reading primary_signal explicitly. confidence_tier left NULL.

    v1/v2 columns left unset (NULL): tier1_hits, tier2_hits, tier_score,
    stake_modifier, tier_metadata, confidence_tier, market_context,
    avg_stake_modifier.
    """
    math = combo["math"]
    card_sig = combo.get("primary_signal")
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
        # Phase 1
        "primary_signal":       card_sig,
        "suggested_stake_tier": combo.get("suggested_stake_tier"),
        "phase1_metadata":      combo.get("phase1_metadata"),
        # Back-compat dual write
        "confidence_score":     card_sig,
        "status":               "pending",
    }
    card_res = sb.table("cards").insert(card_payload).execute()
    if not card_res.data:
        log.error("  failed to insert card: %s", card_payload)
        return None
    card_id = card_res.data[0]["id"]

    legs_payload = []
    for i, leg in enumerate(combo["legs"], 1):
        leg_sig = leg.get("primary_signal")
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
            # Phase 1
            "primary_signal":        leg_sig,
            "primary_signal_source": leg.get("primary_signal_source"),
            "suggested_stake_tier":  leg.get("suggested_stake_tier"),
            "phase1_metadata":       leg.get("phase1_metadata"),
            # Back-compat dual write
            "confidence_score":      leg_sig,
        })
    sb.table("card_legs").insert(legs_payload).execute()
    return card_id


# ────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────

def main():
    today = get_today_iso()
    log.info("🧅 Card picker — slate %s (Phase 1: matchup-first leg pool)", today)

    # ── Idempotency gate ────────────────────────────────────────────────
    existing = sb.table("cards").select("id, tier") \
        .eq("card_date", today) \
        .limit(1) \
        .execute()
    if existing.data:
        log.info("Cards already locked for %s. Skipping.", today)
        return

    # ── Threshold cache ─────────────────────────────────────────────────
    try:
        cfg = load_thresholds(sb)
        configure(cfg)
        log.info("Loaded %d thresholds from model_thresholds.", len(cfg))
    except Exception as e:
        cfg = {}
        log.warning("model_thresholds load failed (%s) — using _cfg_num defaults.", e)

    games = fetch_today_games(today)
    if not games:
        log.warning("No games for %s — skipping", today)
        return

    # ── BvP coverage pre-flight (uses the same player set fetch_candidates
    #    will, so we recompute it here for the warning before enrichment) ──
    game_ids = [g["id"] for g in games]
    season = datetime.now(timezone.utc).year
    proj_ids_res = sb.table("projections") \
        .select("player_id") \
        .in_("game_id", game_ids) \
        .in_("market", list(MARKET_MIN_PROB.keys())) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .execute()
    all_proj_player_ids = list({p["player_id"] for p in (proj_ids_res.data or [])})
    if all_proj_player_ids:
        starter_by_game_team = fetch_starting_pitcher_for_game(games)
        pitcher_ids = list({pid for pid in starter_by_game_team.values() if pid})
        bvp_pairs_preflight = fetch_bvp(all_proj_player_ids, pitcher_ids)
        bvp_batter_count = len({k[0] for k in bvp_pairs_preflight})
        coverage_pct = bvp_batter_count / len(all_proj_player_ids) * 100
        log.info("BvP coverage pre-flight: %d/%d batters (%.1f%%)",
                 bvp_batter_count, len(all_proj_player_ids), coverage_pct)
        if coverage_pct < 20:
            log.warning(
                "Low bvp coverage for today's slate (%d/%d batters, %.1f%%) — "
                "Gate B may rarely fire.",
                bvp_batter_count, len(all_proj_player_ids), coverage_pct,
            )

    # ── Build candidate pool ────────────────────────────────────────────
    candidates = fetch_candidates(today, games, cfg)
    log.info("Candidate pool: %d legs qualifying Phase 1 gates", len(candidates))

    if len(candidates) < 2:
        log.warning("Too few candidates (need >=2 for 2-leggers) — no cards today")
        wipe_today(today)
        return

    # ── Slate quality gauge (informational; keyed on EV not edge) ──────
    strong_plays = [c for c in candidates if c["edge"] >= 0.05]
    if len(strong_plays) >= 5:
        slate_quality = "strong"
    elif len(strong_plays) >= 3:
        slate_quality = "medium"
    else:
        slate_quality = "light"
    log.info("Slate quality: %s (%d plays with 5%%+ edge)",
             slate_quality, len(strong_plays))

    # ── Generate combinations per tier ──────────────────────────────────
    all_combos_by_tier = {}
    for leg_count in [2, 3, 4]:
        if len(candidates) < leg_count:
            all_combos_by_tier[tier_for_legs(leg_count)] = []
            continue
        combos = generate_combos(candidates, leg_count, cfg=cfg)
        all_combos_by_tier[tier_for_legs(leg_count)] = combos
        log.info("  %d-leg combos generated: %d", leg_count, len(combos))

    # ── Select cards with dedup ─────────────────────────────────────────
    selected = select_cards(all_combos_by_tier)
    total_selected = sum(len(v) for v in selected.values())
    if total_selected == 0:
        log.info("No combinations cleared EV gates — no cards today")
        wipe_today(today)
        return

    log.info("Selected: %d two-leg, %d three-leg, %d four-leg",
             len(selected["two_leg"]), len(selected["three_leg"]),
             len(selected["four_leg"]))

    # ── Wipe today's pending cards, dedup vs settled, insert ────────────
    wipe_today(today)
    existing_fingerprints = fetch_existing_fingerprints(today)
    if existing_fingerprints:
        log.info("  found %d existing card fingerprints from earlier runs — will dedup",
                 len(existing_fingerprints))

    inserted_count = 0
    skipped_dups = 0
    for tier in ["two_leg", "three_leg", "four_leg"]:
        for combo in selected[tier]:
            fp = combo_fingerprint(combo["legs"])
            if fp in existing_fingerprints:
                skipped_dups += 1
                log.info("  ⊘ skipped %s [%s] — fingerprint already in DB (settled earlier)",
                         combo["tier"], combo["label"])
                continue

            cid = insert_card(today, combo)
            if cid is None:
                continue
            existing_fingerprints.add(fp)
            inserted_count += 1

            math = combo["math"]
            card_sig = combo.get("primary_signal") or 0.0
            log.info("  ✓ %s [%s] EV=%.3f edge=%.3f odds=%s card_sig=%.3f stake=%s (id=%s)",
                     combo["tier"], combo["label"],
                     math["ev_per_dollar"], math["edge"],
                     "+%d" % math["american_odds"] if math["american_odds"] >= 0
                                                  else str(math["american_odds"]),
                     card_sig,
                     combo.get("suggested_stake_tier") or "—",
                     cid)
            for i, leg in enumerate(combo["legs"], 1):
                sig = leg.get("primary_signal")
                sig_str = f"{sig:.3f}" if sig is not None else "—"
                src = leg.get("primary_signal_source") or "—"
                gate = leg.get("gate") or "—"
                ev_a = leg.get("ev_action") or "—"
                log.info("    leg%d: %s %s @ %s (proj=%.2f%%, edge=+%.2f%%)  sig=%s [%s, gate %s]  ev=%s",
                         i, leg["player_name"], leg["market"],
                         "+%d" % leg["american_odds"] if leg["american_odds"] >= 0
                                                       else str(leg["american_odds"]),
                         leg["projected_prob"] * 100,
                         leg["edge"] * 100,
                         sig_str, src, gate, ev_a)

    log.info("✓ Cards picker complete — inserted=%d, skipped_dups=%d",
             inserted_count, skipped_dups)


if __name__ == "__main__":
    main()
