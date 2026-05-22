"""
pick_cards.py — AI-built Cebolla Cards (tier system v0.4.0).

Generates daily parlay cards from today's projection pool. Runs after
pick_pod.py in the 2:45 AM ET POD lock window.

═══ TIER SYSTEM INTEGRATION (v0.4.0) ═══════════════════════════════════
  Each candidate leg is evaluated through the same Tier 1/2 framework
  used by pick_pod.py (see tier_system.py). A leg qualifies if:
    - HR market:  ≥2-of-3 Tier 1 hits (barrel%, xSLG, HR vs primary pitch)
                  OR Stowers path (1 T1 + ≥3 T2 hits)
    - HRR/Hits:   ≥2-of-3 Tier 1 hits (hit_per_pa, xBA, BvP positive)
                  OR Stowers path

  Disqualified candidates are dropped from the pool BEFORE combo generation.
  No more "carry leg" cards — every leg must independently clear tier gates.

  Each leg carries:
    tier1_hits, tier2_hits, tier_score (0.85-1.25), stake_modifier,
    tier_metadata (JSONB structure same as pods)

  Each card carries:
    avg_stake_modifier = mean(leg.stake_modifier)

CARD TIERS (variable per slate quality):
  two_leg   — up to 6 cards, ev_per_dollar > 0.05
  three_leg — up to 4 cards, ev_per_dollar > 0.08
  four_leg  — up to 2 cards, ev_per_dollar > 0.10 AND 4+ strong candidates

STAKE RECOMMENDATIONS (canonical, frontend can scale):
  two_leg=$10, three_leg=$5, four_leg=$1

MATH:
  combined_prob   = ∏(leg_prob) × (1 - correlation_penalty)
  parlay_decimal  = ∏(leg_decimal)
  parlay_american = decimal_to_american(parlay_decimal)
  implied_prob    = 1 / parlay_decimal
  edge            = combined_prob - implied_prob
  ev_per_dollar   = combined_prob × (parlay_decimal - 1) - (1 - combined_prob)

CORRELATION PENALTIES (unchanged):
  same game     -12%  (SGPs usually have negative correlation + juice)
  same team     -15%  (lineup state shared)
  same player   -15%  (same player good day correlates across markets)

CANDIDATE FLOORS (per-market, applied BEFORE tier evaluation):
  hr_anytime   projected_prob >= 0.08, edge >= 0.02
  h_r_rbi_1.5  projected_prob >= 0.40, edge >= 0.02
  h_r_rbi_2.5  projected_prob >= 0.20, edge >= 0.02
  hits_yes     projected_prob >= 0.55, edge >= 0.02
  rbi_yes      projected_prob >= 0.35, edge >= 0.02

  Floors exist to filter obvious noise BEFORE the more expensive tier
  evaluation. A candidate must pass BOTH the per-market floor AND the
  tier gates to become a viable leg.

OBJECTIVE FUNCTION (v0.4.0):
  score = ev_per_dollar * 100 + sum(leg_tier_scores) * 5

  EV is the primary driver (scaled by 100). The sum of leg tier_scores
  (each 0.85-1.25) breaks ties — a card whose legs all qualify "triple"
  (3-of-3 T1) sums to ~3.5-5.0, vs a card with "standard" legs at ~3.0-4.0.
  At scale ×5 that's a 5-10 point tiebreaker — meaningful when EVs are
  close, never enough to overturn a clear EV winner.

  Heat is NO LONGER a separate bonus — heat is now baked into Tier 2A
  (heat ≥ HOT counts as a T2 hit), so it influences tier_score directly.

DEDUP / EXPOSURE (unchanged):
  After scoring, greedy selection: take highest-scoring combo per tier,
  exclude its players from subsequent same-tier picks. 3-leg can share
  ≤1 leg with any 2-leg. 4-leg can share ≤2 legs with anything.
  Global caps: any player ≤3 cards, ≤1 fully-same-game card.

MARKET DIVERSIFICATION (unchanged):
  Mandate ≥2 non-HR cards in the menu. Post-selection swap if short.
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from itertools import combinations

from supabase import create_client
from dotenv import load_dotenv

# Tier system shared with pick_pod.py
from tier_system import (
    evaluate_tier1_hr,
    evaluate_tier1_hrr,
    evaluate_tier2,
    score_candidate,
    qualification_path,
    primary_pitch_type,
    stake_modifier_for,
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

# Picker reads projections from this model_version only. Must stay in sync
# with compute_projections.py:MODEL_VERSION and pick_pod.py:REQUIRED_MODEL_VERSION.
REQUIRED_MODEL_VERSION = "v0.4.0"

# Market floors — each market needs its own min projected_prob because
# the markets have wildly different base rates. HR is ~10% baseline,
# Hits 0.5 is ~70% baseline — same floor would be nonsense.
#
# min_edge lowered from 0.03 → 0.02 on 2026-05-21:
#   Cards are a research menu, not a single-best-bet pick. The 3% floor
#   was producing too-thin candidate pools on slates where the slate is
#   small (e.g., 7 games) or where books are sharp. Lowering to 2% roughly
#   doubles the candidate pool which lets the picker build the full 6/4/2
#   menu. The EV gates (5%/8%/10% per tier) still enforce that no card
#   ships with negative expected value — the only thing that changed is
#   which BATTERS are eligible to be legs, not what combos clear.
MARKET_FLOORS = {
    "hr_anytime":  {"min_prob": 0.08, "min_edge": 0.02},
    "h_r_rbi_1.5": {"min_prob": 0.40, "min_edge": 0.02},
    "h_r_rbi_2.5": {"min_prob": 0.20, "min_edge": 0.02},
    "hits_yes":    {"min_prob": 0.55, "min_edge": 0.02},
    "rbi_yes":     {"min_prob": 0.35, "min_edge": 0.02},
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
# Expanded from 3/2/1 to give users more research surface per slate.
# Real watch: cards 4-6 in a tier will naturally have lower EV than 1-3.
# That's fine — they're still +EV (passing the EV gate) but show variety.
CARD_CAPS = {
    "two_leg":   6,
    "three_leg": 4,
    "four_leg":  2,
}

# Correlation penalties applied to combined_prob.
# same_game (SGP) penalty intentionally HIGHER than initial design — SGPs
# usually have unfavorable correlation (when one leg loses, the other tends
# to also lose), and sportsbooks juice the prices accordingly. We make them
# harder to qualify so they appear less often. They still surface when the
# math genuinely supports it (rare but real).
CORRELATION_PENALTIES = {
    "same_game":   0.12,   # bumped from 0.08 — discourage SGP frequency
    "same_team":   0.15,   # bumped from 0.12
    "same_player": 0.15,
}

# ── Heat integration (v0.4.0) ──
#
# In v0.3.0, cards had a separate HEAT_BONUS_BY_TIER lookup table that
# adjusted the score by ±0.3 to ±1.2 per leg. In v0.4.0 this is GONE —
# heat is now baked into Tier 2A of tier_system.py:
#
#   Tier 2A:  heat_tier ∈ {HOT, BLAZING}  → +1 T2 hit
#   Tier 2A:  heat_tier ∈ {WARM, FLAT}    → 0 T2 hits
#   Tier 2A:  heat_tier ∈ {COOL, COLD}    → 0 T2 hits
#   FROZEN (combined_trend ≤ -50%)        → filtered before tier eval
#
# So a HOT batter naturally accumulates a higher tier_score (T2 contributes
# +0.05 per hit), which feeds into the card-level score. No separate
# bonus table needed.

# Combined trend threshold below which we filter the candidate entirely.
# Matches the FROZEN tier (≤ -50%) from useTrends.js / compute_batter_trends.
HEAT_FROZEN_THRESHOLD = -0.50

# Max age (in days) for a fallback heat snapshot. If today has no rows and
# the most-recent fallback is older than this, we degrade to no-heat rather
# than use ancient data.
HEAT_MAX_STALE_DAYS = 3

# Dedup: max shared legs between selected cards across tiers
SHARING_LIMITS = {
    "three_vs_two": 1,   # 3-leggers can share at most 1 leg with any 2-legger
    "four_vs_any":  2,   # 4-legger can share at most 2 legs with anything
}

# Global exposure caps (across the entire daily card menu, all tiers combined)
# Scaled up with the expanded CARD_CAPS so the picker doesn't bottleneck on
# player availability. SGP cap intentionally kept at 1 even with bigger menu
# — SGPs are usually bad juju (negative correlation, juiced lines) and we
# want them as a rare lottery shot, not a regular feature.
MAX_PLAYER_APPEARANCES = 3   # scaled from 2 — bigger menu needs more headroom
MAX_SAME_GAME_CARDS    = 1   # KEPT at 1 — SGPs stay rare regardless of menu size

# Market diversification: mandate at least this many cards use ONLY non-HR
# markets (Hits / RBI / HRR). Prevents the menu from being entirely
# dependent on HR variance, which is the noisiest market we cover.
# A "non-HR card" has zero legs with market='hr_anytime'.
# Scaled to 2 to match the bigger menu — more cards = more diversification needed.
MIN_NON_HR_CARDS = 2

def fetch_starting_pitcher_for_game(games):
    """
    Build {(game_id, team_id): pitcher_id} from games table.
    Keyed by the team the pitcher pitches FOR — to find opposing pitcher,
    look up the batter's OPPONENT team in this dict.

    Mirror of the same function in pick_pod.py.
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
    """L14 batter_stats. Returns {player_id: row}. Mirror of pick_pod."""
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, hit_per_pa, barrel_pct, hard_hit_pct, "
                "xslg, xba") \
        .eq("season", season) \
        .eq("window_type", "l14") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_batter_season_by_pitch(player_ids, season):
    """Season by_pitch_type per batter. Mirror of pick_pod."""
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, by_pitch_type") \
        .eq("season", season) \
        .eq("window_type", "season") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: (r.get("by_pitch_type") or {}) for r in (res.data or [])}


def fetch_pitcher_arsenals(pitcher_ids, season):
    """Pitcher arsenals across both stances. Mirror of pick_pod."""
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
    """BvP history. Returns {(batter_id, pitcher_id): row}. Mirror of pick_pod."""
    if not batter_ids or not pitcher_ids:
        return {}
    res = sb.table("bvp_history") \
        .select("batter_id, pitcher_id, pa, avg, ops") \
        .in_("batter_id", batter_ids) \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    return {(r["batter_id"], r["pitcher_id"]): r for r in (res.data or [])}


CONTACT_WEIGHTS = {"barrel_pct": 0.40, "hard_hit_pct": 0.30, "xslg": 0.30}
CONTACT_MIN_PA = 30
NEUTRAL_PERCENTILE = 50.0


def _percentile_rank(value, pool):
    """Percentile rank: % of pool strictly below value, + 0.5 × equal.
       Mirror of pick_pod._percentile_rank."""
    if not pool or value is None:
        return None
    sorted_pool = sorted(p for p in pool if p is not None)
    if not sorted_pool:
        return None
    below = sum(1 for p in sorted_pool if p < value)
    equal = sum(1 for p in sorted_pool if p == value)
    return (below + 0.5 * equal) / len(sorted_pool) * 100.0


def _build_contact_pools(stat_rows):
    pools = {"barrel_pct": [], "hard_hit_pct": [], "xslg": []}
    for r in stat_rows:
        for k in pools:
            v = r.get(k)
            if v is not None:
                try:
                    pools[k].append(float(v))
                except (TypeError, ValueError):
                    pass
    return pools


def _contact_score(stats, pools):
    """Composite percentile-ranked contact score. Returns None if stats
       lack PA threshold or no real values were found."""
    if not stats:
        return None
    pa = stats.get("pa")
    try:
        pa_v = float(pa) if pa is not None else 0
    except (TypeError, ValueError):
        return None
    if pa_v < CONTACT_MIN_PA:
        return None
    weighted_sum = 0.0
    total_weight = 0.0
    had_real_value = False
    for key, weight in CONTACT_WEIGHTS.items():
        v = stats.get(key)
        pool = pools.get(key, [])
        pct = None
        if v is not None and len(pool) >= 3:
            try:
                pct = _percentile_rank(float(v), pool)
                if pct is not None:
                    had_real_value = True
            except (TypeError, ValueError):
                pass
        if pct is None:
            pct = NEUTRAL_PERCENTILE
        weighted_sum += pct * weight
        total_weight += weight
    if not had_real_value:
        return None
    if total_weight == 0:
        return None
    return round(weighted_sum / total_weight, 1)


def fetch_contact_scores(player_ids, season):
    """
    Build contact scores for each player using the same percentile-rank
    method as pick_pod. Returns {player_id: contact_score 0-100 or None}.
    """
    if not player_ids:
        return {}
    pool_res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, hard_hit_pct, xslg") \
        .eq("season", season) \
        .eq("window_type", "l14") \
        .eq("vs_hand", "A") \
        .gte("pa", CONTACT_MIN_PA) \
        .execute()
    pool_rows = pool_res.data or []
    pools = _build_contact_pools(pool_rows)
    by_batter = {r["batter_id"]: r for r in pool_rows if r.get("batter_id") is not None}
    out = {}
    for pid in player_ids:
        row = by_batter.get(pid)
        out[pid] = _contact_score(row, pools) if row else None
    return out


# ────────────────────────────────────────────────────────────────────────
# TIER EVALUATION (per candidate)
# ────────────────────────────────────────────────────────────────────────

def _market_class(market):
    """Map projection market → tier evaluator class.
       HR uses tier1_hr; everything else (HRR, hits, rbi) uses tier1_hrr."""
    return "hr" if market == "hr_anytime" else "hrr"


def _evaluate_leg_tiers(
    candidate, market_class,
    games_by_id, players_by_id,
    contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs,
    starter_by_game_team, batter_season_by_pitch,
):
    """
    Evaluate one candidate through the tier system. Mutates candidate
    in place with tier fields. Returns False if disqualified, True if
    qualified (tier_score populated).

    Disqualified means: doesn't pass 2-of-3 T1 AND doesn't pass Stowers
    (1 T1 + ≥3 T2). Disqualified candidates should not be used as legs.
    """
    game = games_by_id.get(candidate["game_id"])
    player = players_by_id.get(candidate["player_id"])
    if not game or not player:
        return False

    # Find opposing pitcher
    is_home = player.get("team_id") == game.get("home_team_id")
    opposing_team_id = game.get("away_team_id") if is_home else game.get("home_team_id")
    opposing_pitcher_id = starter_by_game_team.get((candidate["game_id"], opposing_team_id))

    bstats = batter_stats_l14.get(candidate["player_id"])
    bvp_row = bvp_pairs.get((candidate["player_id"], opposing_pitcher_id)) if opposing_pitcher_id else None

    # Pitcher primary pitch (only needed for HR T1C)
    pitcher_primary = None
    if opposing_pitcher_id:
        arsenal = pitcher_arsenals.get(opposing_pitcher_id) or []
        pitcher_primary = primary_pitch_type(arsenal)

    # Tier 1
    if market_class == "hr":
        sbp = (batter_season_by_pitch or {}).get(candidate["player_id"])
        t1_hits, t1_detail = evaluate_tier1_hr(bstats, pitcher_primary, season_by_pitch=sbp)
    else:
        t1_hits, t1_detail = evaluate_tier1_hrr(bstats, bvp_row)

    # Tier 2
    heat = heat_by_player.get(candidate["player_id"]) if heat_by_player else None
    heat_tier = heat.get("combined_tier") if heat else None
    contact = contact_by_player.get(candidate["player_id"])
    t2_hits, t2_detail = evaluate_tier2(bstats, heat_tier, contact, bvp_row)

    # Score
    score = score_candidate(t1_hits, t2_hits)
    if score is None:
        return False  # disqualified
    qpath = qualification_path(t1_hits, t2_hits)

    # Stake modifier (informational)
    # Park comes from games.hr_factor_overall (for HR) or team.park_ba_factor
    # via projection.park_adj which compute_projections already wrote.
    # We just read the leg's stored park_adj/weather_adj from the projection row.
    park = candidate.get("park_adj") or 1.0
    weather = candidate.get("weather_adj") or 1.0
    smod = stake_modifier_for(park, weather)

    # Mutate candidate
    candidate["tier1_hits"]     = t1_hits
    candidate["tier2_hits"]     = t2_hits
    candidate["tier_score"]     = score
    candidate["stake_modifier"] = smod
    candidate["tier_metadata"]  = {
        "tier1": t1_detail,
        "tier2": t2_detail,
        "qualification_path": qpath,
        "stake_modifier": smod,
    }
    return True


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
    """Pull today's games + team abbrevs + probable pitchers."""
    res = sb.table("games") \
        .select("id, away_team_id, home_team_id, "
                "home_pitcher_id, away_pitcher_id, "
                "away_team:teams!games_away_team_id_fkey(abbrev), "
                "home_team:teams!games_home_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .execute()
    return res.data or []


def fetch_batter_heat(player_ids, date_iso):
    """
    Pull the latest batter_trends snapshot for the candidate batters.
    Returns dict { player_id: {combined_trend, combined_tier, ...} }.

    Strategy: try date_iso first (today's snapshot). If that returns nothing
    (cron may have failed, or we're running off-cycle), fall back to the
    most-recent available snapshot per batter — staleness < freshness-loss,
    and a day-old heat read is still useful information. We refuse fallback
    snapshots older than HEAT_MAX_STALE_DAYS to prevent using ancient data
    after extended cron outages.

    On any DB error (table doesn't exist, migration not applied), returns
    an empty dict so card selection falls back to pre-heat behavior.
    """
    if not player_ids:
        return {}
    columns = ("batter_id, trend_date, combined_trend, combined_tier, " +
               "hr_trend, hits_trend, barrel_trend, iso_trend, pa_l14")
    try:
        # Primary: today's snapshot
        res = sb.table("batter_trends") \
            .select(columns) \
            .in_("batter_id", player_ids) \
            .eq("trend_date", date_iso) \
            .execute()
        rows = res.data or []

        # Fallback: if no rows for today, pull the most-recent snapshot per
        # batter via descending order and dedupe to first-seen (most recent).
        if not rows:
            fb_res = sb.table("batter_trends") \
                .select(columns) \
                .in_("batter_id", player_ids) \
                .order("trend_date", desc=True) \
                .execute()
            seen = set()
            rows_candidate = []
            for r in fb_res.data or []:
                bid = r["batter_id"]
                if bid in seen:
                    continue
                seen.add(bid)
                rows_candidate.append(r)
            # Staleness guard: reject snapshots older than HEAT_MAX_STALE_DAYS.
            # Heat data 4+ days old isn't reflecting recent form.
            today_dt = datetime.fromisoformat(date_iso).date()
            for r in rows_candidate:
                td = r.get("trend_date")
                if not td:
                    continue
                try:
                    snap_dt = datetime.fromisoformat(td).date() if isinstance(td, str) else td
                    age_days = (today_dt - snap_dt).days
                    # Require 0 <= age_days <= HEAT_MAX_STALE_DAYS.
                    # Negative age = snapshot dated in the future (timezone glitch);
                    # treat as suspicious and skip.
                    if 0 <= age_days <= HEAT_MAX_STALE_DAYS:
                        rows.append(r)
                except (ValueError, TypeError):
                    continue
            if rows:
                log.info("  heat fallback: using snapshots up to %d days old (no rows for %s)",
                         HEAT_MAX_STALE_DAYS, date_iso)

        out = {}
        for r in rows:
            bid = r["batter_id"]
            # Cast numeric fields explicitly — Supabase returns NUMERIC as str
            ct = r.get("combined_trend")
            out[bid] = {
                "combined_trend": float(ct) if ct is not None else None,
                "combined_tier":  r.get("combined_tier"),
                "hr_trend":     float(r["hr_trend"])     if r.get("hr_trend")     is not None else None,
                "hits_trend":   float(r["hits_trend"])   if r.get("hits_trend")   is not None else None,
                "barrel_trend": float(r["barrel_trend"]) if r.get("barrel_trend") is not None else None,
                "iso_trend":    float(r["iso_trend"])    if r.get("iso_trend")    is not None else None,
                "pa_l14":       r.get("pa_l14"),
                "trend_date":   r.get("trend_date"),
            }
        return out
    except Exception as e:
        log.warning("fetch_batter_heat failed (degrading to no-heat): %s", e)
        return {}


def fetch_candidates(date_iso, games):
    """
    Build the unified candidate pool across all markets, applying:
      1. Per-market floors (cheap filter)
      2. FROZEN heat filter
      3. Tier system gates (HR T1≥2, or HRR T1≥2, or Stowers 1+3)

    Each candidate is enriched with tier1_hits, tier2_hits, tier_score,
    stake_modifier, tier_metadata before returning.

    Returns a flat list of qualified candidate dicts, sorted by tier_score
    descending (ties broken by edge).
    """
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}
    season = datetime.now(timezone.utc).year

    markets = list(MARKET_FLOORS.keys())
    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, "
                "edge, best_american_odds, best_book, "
                "park_adj, weather_adj, stake_modifier, model_version") \
        .in_("game_id", game_ids) \
        .in_("market", markets) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
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

    # ── Heat lookup ──
    # Used for the FROZEN filter AND for Tier 2A. With tier system, heat
    # informs T2 directly — no more separate score bonus.
    heat_by_player = fetch_batter_heat(player_ids, date_iso)
    if heat_by_player:
        log.info("Heat snapshot: %d batters with combined_trend rows for %s",
                 len(heat_by_player), date_iso)
    else:
        log.info("Heat snapshot: empty (degrading to no-heat scoring)")

    # ── Per-market floors + FROZEN filter (cheap filters first) ──
    floor_passed = []
    frozen_filtered = 0
    for p in projections:
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

        # FROZEN filter
        heat = heat_by_player.get(p["player_id"])
        if heat and heat.get("combined_trend") is not None:
            if heat["combined_trend"] <= HEAT_FROZEN_THRESHOLD:
                frozen_filtered += 1
                continue

        # Resolve team + opponent abbrevs
        is_home = player["team_id"] == game["home_team_id"]
        own = (game["home_team"] if is_home else game["away_team"]) or {}
        opp = (game["away_team"] if is_home else game["home_team"]) or {}

        american = int(p["best_american_odds"])
        decimal = american_to_decimal(american)
        if decimal is None or decimal <= 1:
            continue

        # Parse line from market string
        line = None
        if p["market"].startswith("h_r_rbi_"):
            try:
                line = float(p["market"].split("_")[-1])
            except (ValueError, IndexError):
                line = None
        elif p["market"] == "hr_anytime":
            line = 0.5

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
            "edge":            edge,
            "book":            p.get("best_book") or "draftkings",
            "park_adj":        float(p.get("park_adj") or 1.0),
            "weather_adj":     float(p.get("weather_adj") or 1.0),
            "combined_trend":  heat.get("combined_trend") if heat else None,
            "combined_tier":   heat.get("combined_tier")  if heat else None,
        })

    if frozen_filtered:
        log.info("Filtered %d FROZEN candidates (combined_trend <= %.0f%%)",
                 frozen_filtered, HEAT_FROZEN_THRESHOLD * 100)
    log.info("After floors + FROZEN: %d candidates", len(floor_passed))

    if not floor_passed:
        return []

    # ── Tier system data prefetch ──
    qualifying_player_ids = list({c["player_id"] for c in floor_passed})
    log.info("Fetching tier-system data for %d candidates...", len(qualifying_player_ids))

    batter_stats_l14 = fetch_batter_l14_stats(qualifying_player_ids, season)
    log.info("  L14 stats: %d rows", len(batter_stats_l14))

    batter_season_by_pitch = fetch_batter_season_by_pitch(qualifying_player_ids, season)
    log.info("  Season by_pitch_type: %d batters", len(batter_season_by_pitch))

    starter_by_game_team = fetch_starting_pitcher_for_game(games)
    pitcher_ids = list({pid for pid in starter_by_game_team.values() if pid})
    log.info("  Starting pitchers: %d", len(pitcher_ids))

    pitcher_arsenals = fetch_pitcher_arsenals(pitcher_ids, season)
    log.info("  Pitcher arsenals: %d", len(pitcher_arsenals))

    bvp_pairs = fetch_bvp(qualifying_player_ids, pitcher_ids)
    log.info("  BvP pairs: %d", len(bvp_pairs))

    contact_by_player = fetch_contact_scores(qualifying_player_ids, season)
    log.info("  Contact scores: %d players", sum(1 for v in contact_by_player.values() if v is not None))

    # ── Tier evaluation per candidate ──
    qualified = []
    disqualified_count = 0
    for cand in floor_passed:
        ok = _evaluate_leg_tiers(
            cand, _market_class(cand["market"]),
            games_by_id, players_by_id,
            contact_by_player, heat_by_player,
            batter_stats_l14, pitcher_arsenals, bvp_pairs,
            starter_by_game_team, batter_season_by_pitch,
        )
        if ok:
            qualified.append(cand)
        else:
            disqualified_count += 1

    log.info("Tier evaluation: %d qualified, %d disqualified (failed T1 ≥2 or Stowers)",
             len(qualified), disqualified_count)

    # Sort by tier_score DESC, then edge DESC as tiebreaker
    qualified.sort(key=lambda c: (c["tier_score"], c["edge"]), reverse=True)
    return qualified


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
    Objective function: EV primary + sum-of-leg-tier-scores tiebreaker.

      score = ev_per_dollar * 100 + sum(leg_tier_scores) * 5

    EV (×100) is the dominant signal. The sum of per-leg tier_scores
    (each in 0.85-1.25 range) breaks ties — a 2-leg with both legs in
    "triple" path sums ~2.4, vs both legs in "Stowers" sums ~1.7.
    At scale ×5, that's a 3-4 point movement — meaningful only when EVs
    are similar, never enough to overturn meaningful EV gaps.

    Heat is already inside tier_score via Tier 2A — no separate bonus.
    """
    if not math or math["ev_per_dollar"] is None:
        return -999
    tier_sum = sum(float(l.get("tier_score") or 0) for l in legs)
    return math["ev_per_dollar"] * 100 + tier_sum * 5


def make_combo_record(legs, tier, slate_quality):
    """Bundle a combination's math + legs + tier into a finalized record."""
    math = parlay_math(legs)
    if not math:
        return None

    score = score_combination(math, legs)
    stake = STAKE_REC[tier]
    profit_if_hit = round(stake * (math["decimal_odds"] - 1), 2)

    # Average stake_modifier across legs (informational, for "conditions" badge)
    smods = [float(l.get("stake_modifier") or 1.0) for l in legs]
    avg_smod = round(sum(smods) / len(smods), 3) if smods else 1.0

    return {
        "tier":          tier,
        "leg_count":     len(legs),
        "legs":          legs,
        "math":          math,
        "score":         score,
        "stake_rec":     stake,
        "payout_if_hit": profit_if_hit,
        "label":         label_for(legs, tier, math),
        "avg_stake_modifier": avg_smod,
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
    many candidates we restrict to the top tier-scored candidates first.

    candidates is pre-sorted by (tier_score DESC, edge DESC) so the top-N
    slice is "the N highest-conviction plays". Within those, combo scoring
    picks the best EV combinations.
    """
    # Cap pool by tier to keep computation reasonable
    if leg_count == 2:
        pool = candidates[:20]   # top 20 by tier_score
    elif leg_count == 3:
        pool = candidates[:15]   # top 15 by tier_score
    elif leg_count == 4:
        pool = candidates[:12]   # top 12 by tier_score
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
    """
    Delete today's PENDING cards (and cascade to legs) before re-picking.

    Settled cards (status IN win/loss/void) are PRESERVED. This is critical
    for the historical ledger — once a card settles, those results are
    permanent receipts and re-running the picker must never erase them.

    Result:
      - Re-running pick_cards mid-day adds fresh pending cards alongside
        already-settled ones from earlier in the same day.
      - Settled cards continue to show in Card History.
      - Today's Menu still only displays the newly picked pending cards.
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
    Deterministic fingerprint for a set of legs. Two combos with the
    same (player_id, market, line) tuples produce the same fingerprint
    regardless of leg ordering or other metadata.

    Used to prevent inserting duplicate cards when pick_cards re-runs
    (e.g. manual dispatch) on the same slate. The combinatorial output
    is deterministic given identical inputs — so if odds haven't shifted,
    the same combos will be generated again. We refuse to re-insert any
    combo whose fingerprint is already present in today's cards.

    Format: 'p:1234|m:hr_anytime|l:0.5;p:5678|m:h_r_rbi_1.5|l:1.5'
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
    Fetch fingerprints of all cards already in DB for the given date.
    Returns a set of fingerprint strings for fast lookup.

    Pulls every card_leg from today's cards to reconstruct each card's
    fingerprint, regardless of whether the card is pending or settled.
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
        # legs here come from DB which doesn't have player_mlbam_id etc.
        # but combo_fingerprint only needs player_id/market/line — same shape works
        fingerprints.add(combo_fingerprint(legs))
    return fingerprints


def insert_card(date_iso, combo):
    """Insert one card + its legs with tier-system fields."""
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
        "avg_stake_modifier": combo.get("avg_stake_modifier"),
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
            # ── Tier system fields ──
            "tier1_hits":      leg.get("tier1_hits"),
            "tier2_hits":      leg.get("tier2_hits"),
            "tier_score":      leg.get("tier_score"),
            "stake_modifier":  leg.get("stake_modifier"),
            # tier_metadata is JSONB — pass raw dict, supabase-py serializes.
            "tier_metadata":   leg.get("tier_metadata"),
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
    log.info("Candidate pool: %d legs qualifying tier system (T1 ≥2 or Stowers)", len(candidates))

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

    # Wipe today's PENDING cards before inserting new ones.
    # Settled cards (already-graded results) are preserved — they're permanent
    # receipts. See wipe_today() docstring.
    wipe_today(today)

    # Fetch fingerprints of any cards STILL in DB (i.e. settled cards we
    # preserved from earlier runs). Used to prevent re-inserting duplicates
    # of already-settled combos when the picker re-runs on the same slate.
    existing_fingerprints = fetch_existing_fingerprints(today)
    if existing_fingerprints:
        log.info("  found %d existing card fingerprints from earlier runs — will dedup",
                 len(existing_fingerprints))

    # Insert all selected cards (skip any whose fingerprint is already present)
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
            existing_fingerprints.add(fp)   # prevent within-run duplicates too
            inserted_count += 1

            math = combo["math"]
            avg_smod = combo.get("avg_stake_modifier") or 1.0
            log.info("  ✓ %s [%s] EV=%.3f edge=%.3f odds=%s avg_stake_mod=%.2f (id=%s)",
                     combo["tier"], combo["label"],
                     math["ev_per_dollar"], math["edge"],
                     "+%d" % math["american_odds"] if math["american_odds"] >= 0
                                                  else str(math["american_odds"]),
                     avg_smod, cid)
            for i, leg in enumerate(combo["legs"], 1):
                # Tier marker — show conviction signal for each leg
                t1 = leg.get("tier1_hits", 0) or 0
                t2 = leg.get("tier2_hits", 0) or 0
                tscore = leg.get("tier_score", 0) or 0
                qpath = (leg.get("tier_metadata") or {}).get("qualification_path", "?")
                tier_marker = f"  [T1={t1}/3 T2={t2}/4 score={tscore:.2f} {qpath}]"
                # Heat marker (optional, when present)
                heat_marker = ""
                if leg.get("combined_tier"):
                    ct = leg.get("combined_trend")
                    ct_str = f"{ct*100:+.0f}%" if ct is not None else "?"
                    heat_marker = f"  [heat={leg['combined_tier']} {ct_str}]"
                log.info("    leg%d: %s %s @ %s (proj=%.2f%%, edge=+%.2f%%)%s%s",
                         i, leg["player_name"], leg["market"],
                         "+%d" % leg["american_odds"] if leg["american_odds"] >= 0
                                                       else str(leg["american_odds"]),
                         leg["projected_prob"] * 100,
                         leg["edge"] * 100,
                         tier_marker, heat_marker)

    log.info("✓ Cards picker complete — inserted=%d, skipped_dups=%d",
             inserted_count, skipped_dups)


if __name__ == "__main__":
    main()
