"""
pick_pod.py — Daily Play of the Day selection (HR + HRR markets).

TIER-BASED SELECTION (v0.4.0)
═════════════════════════════
Selects ONE pick per market_class per slate, using the tier framework in
tier_system.py. Replaces the previous multiplicative combined_score math.

Selection algorithm:
  1. Fetch projections for today's games (HR + HRR markets)
  2. For each candidate:
     - Evaluate Tier 1 (gates): barrel%/xSLG/HR-vs-pitch for HR;
       hit-rate/xBA/BvP for HRR
     - Evaluate Tier 2 (confirmers): heat, HH%, contact, BvP
     - Compute tier_score via tier_system.score_candidate()
  3. Filter: must qualify (≥2 T1 OR Stowers rule 1T1+3T2)
  4. Rank by tier_score, break ties with Tier 3 (edge, EV)
  5. Pick highest. Publish nothing if zero qualifiers.

Safety floors retained:
  - HR market: projected_prob ≥ HR_MIN_PROJECTED_PROB, edge ≥ HR_MIN_EDGE
  - HRR market: edge ≥ HRR_MIN_EDGE
  - FROZEN heat tier (combined_trend ≤ -50%) blocked

Stake modifier (Tier 4):
  Park × weather composite from projections.stake_modifier, persisted to
  pods.stake_modifier as informational. NOT applied to projection or stake.

Persisted columns (added in 21_tier_system.sql):
  tier1_hits, tier2_hits, tier_score, stake_modifier, tier_metadata

Run order:
  pull_schedule → pull_savant → compute_projections → compute_batter_trends → pick_pod
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client
from dotenv import load_dotenv

from tier_system import (
    evaluate_tier1_hr,
    evaluate_tier1_hrr,
    evaluate_tier2,
    score_candidate,
    qualification_path,
    stake_modifier_for,
    primary_pitch_type,
    tier3_key,
    load_thresholds,
    configure,
)

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pick_pod")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
if not (SUPABASE_URL and SUPABASE_KEY):
    log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY env vars.")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── Market definitions & safety floors ───────────────────────────────────────

# Picker reads projections from this model_version. Since compute_projections
# uses (game_id, player_id, market, model_version) as the upsert conflict key,
# old version rows coexist with new ones — must filter here so we don't
# evaluate the same batter twice (once with park baked in, once without).
#
# CRITICAL: this string MUST match compute_projections.py's MODEL_VERSION.
# When bumping the projection model version, update BOTH places:
#   1. scripts/compute_projections.py:66    MODEL_VERSION = "..."
#   2. scripts/pick_pod.py:75 (this line)   REQUIRED_MODEL_VERSION = "..."
# Mismatch causes picker to find ZERO candidates → publish nothing (safe but
# silent failure mode — check logs for "No projections" if picks vanish).
REQUIRED_MODEL_VERSION = "v0.4.0"

HR_MARKET = "hr_anytime"

# Safety floors retained from pre-tier era. The tier system itself is strong
# enough that these floors are mostly belt-and-suspenders.
HR_MIN_PROJECTED_PROB = 0.15
HR_MIN_EDGE = 0.03
HRR_MIN_EDGE = 0.03

HRR_MARKETS = ["h_r_rbi_1.5", "h_r_rbi_2.5", "h_r_rbi_3.5"]
HRR_MIN_PROB_BY_LINE = {
    "h_r_rbi_1.5": 0.40,
    "h_r_rbi_2.5": 0.20,
    "h_r_rbi_3.5": 0.07,
}

HEAT_FROZEN_THRESHOLD = -0.50
HEAT_MAX_STALE_DAYS = 3


# ─── Contact score (preserved infrastructure) ─────────────────────────────────

CONTACT_WEIGHTS = {"barrel_pct": 0.40, "hard_hit_pct": 0.30, "xslg": 0.30}
CONTACT_MIN_PA  = 30
NEUTRAL_PERCENTILE = 50.0


# ─── v2 framework: config accessor + shared constants ─────────────────────────
# Thresholds load once per run from model_thresholds (main → configure()). The
# tier_system evaluators read their own thresholds from the configured cache;
# the pick_pod-local patch functions below read via _cfg_num (same
# config-with-fallback pattern: cfg value wins, else the documented default —
# resilience if model_thresholds is unreachable).

def _cfg_num(cfg, key, default):
    """Numeric threshold from cfg, falling back to `default` if missing/None."""
    if cfg:
        v = cfg.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return default


def _cfg_required(cfg, key):
    """
    Numeric threshold from cfg with NO fallback — raises if absent. Used for
    values that must not be silently defaulted: e.g. league_hr_per_barrel. If
    model_thresholds was unreachable AND this row is missing, the Patch 4 base
    prediction cannot be computed, so we abort rather than guess a rate.
    """
    if cfg:
        v = cfg.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    raise RuntimeError(
        f"Required threshold '{key}' is missing from model_thresholds and has no "
        f"code fallback. Seed it (sql/26_league_constants.sql) before running."
    )


# Patch 4 pitcher-factor constants. These MIRROR compute_projections.py's pitcher
# factor (hr_per_9 / 1.15, clamped [0.75, 1.40]) — INTERNAL invariants of the
# projection model, kept here only to avoid importing compute_projections.
# Deliberately NOT in model_thresholds: the projection model is the single source
# of truth; seeding them separately would risk pick_pod drifting from the
# projections it scores. If compute_projections.py changes these, change them here
# too. (league_hr_per_barrel, by contrast, IS tunable → model_thresholds row.)
LEAGUE_HR_PER_9 = 1.15
PITCHER_FACTOR_MIN = 0.75
PITCHER_FACTOR_MAX = 1.40

# Coarse pitch-type → family map for Patch 4 per-family barrel lookup in pa_vs_pitch.
_PITCH_FAMILY = {
    "4SM": "fastball", "FF": "fastball", "FT": "fastball", "SI": "fastball", "FC": "fastball",
    "SL": "breaking", "CU": "breaking", "KC": "breaking", "ST": "breaking", "SV": "breaking", "SC": "breaking",
    "CH": "offspeed", "FS": "offspeed", "FO": "offspeed", "KN": "offspeed",
}

# Confidence → letter ordering (Patch 9), best→worst, for the Patch 7 tier floor.
_TIER_ORDER = ["A+", "A", "A-", "B+", "B", "C+", "C"]
_TIER_RANK = {t: i for i, t in enumerate(_TIER_ORDER)}  # lower index = better


def _percentile_rank(value, pool):
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


# ─── Shared fetchers ──────────────────────────────────────────────────────────

def get_today_iso():
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


def existing_pod_for(date_iso, market_class):
    res = sb.table("pods").select("id") \
        .eq("pod_date", date_iso) \
        .eq("market_class", market_class) \
        .execute()
    return bool(res.data)


def fetch_today_games(date_iso):
    """Today's games not yet started/final, with team abbrevs and probable pitchers."""
    res = sb.table("games") \
        .select("id, home_team_id, away_team_id, home_pitcher_id, away_pitcher_id, "
                "home_team:teams!games_home_team_id_fkey(abbrev), "
                "away_team:teams!games_away_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .not_.in_("status", ["Final", "Game Over", "Completed Early", "In Progress"]) \
        .execute()
    return res.data or []


def fetch_batter_l14_stats(player_ids, season):
    """L14 batter_stats for all candidate players. Returns {player_id: row}.

    L14 used for rate stats (barrel%, xSLG, HH%, hit_per_pa, xBA).
    For Tier 1 T1C (HR vs primary pitch), we use season-window by_pitch_type
    via fetch_batter_season_by_pitch — L14's by_pitch_type has too few PAs
    per pitch type (often <5) to be reliable for that check.
    """
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
    """
    Season-window by_pitch_type for each batter. Returns {player_id: by_pitch_dict}.

    The season window has many more PAs per pitch type than L14 — for the
    "HR vs primary pitch ≥ 8%" check we want season-level reliability,
    not the noisy L14 sample.
    """
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
    """
    Pitcher arsenals across both stances. Returns {pitcher_id: [arsenal_rows]}.

    Note: pitcher_arsenals stores separate rows per vs_stance ('L' / 'R') —
    there is no 'A' (all) row. We pull both stances and let primary_pitch_type()
    sum usage across stances to find the overall primary pitch.

    Note: pull_arsenals.py only writes window_type='season' (not l50g/l30g).
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
    """BvP history per (batter, pitcher). Returns {(batter_id, pitcher_id): row}."""
    if not batter_ids or not pitcher_ids:
        return {}
    res = sb.table("bvp_history") \
        .select("batter_id, pitcher_id, pa, avg, ops") \
        .in_("batter_id", batter_ids) \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    return {(r["batter_id"], r["pitcher_id"]): r for r in (res.data or [])}


def fetch_starting_pitcher_for_game(games):
    """
    Build {(game_id, team_id): pitcher_id} from the games table.

    The dict is keyed by the TEAM the pitcher pitches FOR, not the team they
    pitch AGAINST. So:
       starter_by_game_team[(game_id, home_team_id)] = home_pitcher_id

    To find the pitcher OPPOSING a batter: look up the batter's OPPONENT
    team in this dict.

    The games table carries home_pitcher_id and away_pitcher_id directly —
    these are the probable starters. NB: earlier draft tried to read
    lineups.pitcher_id which does not exist — lineups table only has batters.
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


def fetch_contact_scores(player_ids, season):
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
    log.info("Contact pool: %d barrel, %d hard-hit, %d xslg values",
             len(pools["barrel_pct"]), len(pools["hard_hit_pct"]), len(pools["xslg"]))
    by_batter = {r["batter_id"]: r for r in pool_rows if r.get("batter_id") is not None}
    out = {}
    for pid in player_ids:
        row = by_batter.get(pid)
        out[pid] = _contact_score(row, pools) if row else None
    return out


def fetch_batter_heat(player_ids, date_iso):
    if not player_ids:
        return {}
    columns = "batter_id, trend_date, combined_trend, combined_tier"
    try:
        res = sb.table("batter_trends").select(columns) \
            .in_("batter_id", player_ids) \
            .eq("trend_date", date_iso) \
            .execute()
        rows = res.data or []
        if not rows:
            fb_res = sb.table("batter_trends").select(columns) \
                .in_("batter_id", player_ids) \
                .order("trend_date", desc=True) \
                .execute()
            seen = set()
            today_dt = datetime.fromisoformat(date_iso).date()
            for r in fb_res.data or []:
                bid = r["batter_id"]
                if bid in seen:
                    continue
                seen.add(bid)
                td = r.get("trend_date")
                if not td:
                    continue
                try:
                    snap_dt = datetime.fromisoformat(td).date() if isinstance(td, str) else td
                    age_days = (today_dt - snap_dt).days
                    if 0 <= age_days <= HEAT_MAX_STALE_DAYS:
                        rows.append(r)
                except (ValueError, TypeError):
                    continue
            if rows:
                log.info("  heat fallback: using snapshots up to %d days old",
                         HEAT_MAX_STALE_DAYS)
        out = {}
        for r in rows:
            bid = r["batter_id"]
            ct = r.get("combined_trend")
            out[bid] = {
                "combined_trend": float(ct) if ct is not None else None,
                "combined_tier":  r.get("combined_tier"),
            }
        return out
    except Exception as e:
        log.warning("fetch_batter_heat failed (degrading to no-heat): %s", e)
        return {}


# ─── v2 net-new fetchers ──────────────────────────────────────────────────────

def fetch_pitcher_hr9_factors(pitcher_ids, season):
    """
    {pitcher_id: hr9_factor} from pitcher_stats.hr_per_9 (Patch 4 input).

    factor = clamp(hr_per_9 / LEAGUE_HR_PER_9, [MIN, MAX]) — mirrors
    compute_projections.py. Missing row / NULL hr_per_9 → 1.0 (neutral). Prefers
    a window_type='season' row when multiple windows exist.
    """
    if not pitcher_ids:
        return {}
    res = sb.table("pitcher_stats") \
        .select("pitcher_id, hr_per_9, window_type") \
        .eq("season", season) \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    chosen = {}
    for r in res.data or []:
        pid = r["pitcher_id"]
        if pid not in chosen or r.get("window_type") == "season":
            chosen[pid] = r
    out = {}
    for pid, r in chosen.items():
        hr9 = r.get("hr_per_9")
        if hr9 is None:
            out[pid] = 1.0
        else:
            f = float(hr9) / LEAGUE_HR_PER_9
            out[pid] = round(max(PITCHER_FACTOR_MIN, min(PITCHER_FACTOR_MAX, f)), 3)
    return out


def fetch_near_miss_counts(player_ids, as_of_date, cfg=None):
    """
    {batter_id: count} of qualifying near-miss events in the trailing window
    (Patch 6 input). ONE batch query — never per-candidate. near_miss_events is
    empty until ingestion is wired, so this returns {} today (inert).
    """
    if not player_ids:
        return {}
    lookback = int(_cfg_num(cfg, "near_miss_lookback_days", 5))
    ev_floor = _cfg_num(cfg, "near_miss_ev_floor_mph", 100)
    cutoff = (datetime.fromisoformat(as_of_date).date() - timedelta(days=lookback)).isoformat()
    try:
        res = sb.table("near_miss_events") \
            .select("batter_id") \
            .in_("batter_id", player_ids) \
            .gte("game_date", cutoff) \
            .gte("exit_velocity_mph", ev_floor) \
            .execute()
    except Exception as e:
        log.warning("fetch_near_miss_counts failed (degrading to no boost): %s", e)
        return {}
    counts = {}
    for r in res.data or []:
        counts[r["batter_id"]] = counts.get(r["batter_id"], 0) + 1
    return counts


def fetch_user_flags(date_iso, market_class):
    """
    user_flags for the pick_date scoped to this market (market_class NULL = any).
    Empty table → [] (inert today, Patch 7).
    """
    try:
        res = sb.table("user_flags") \
            .select("batter_id, conviction, market_class, note") \
            .eq("pick_date", date_iso).execute()
    except Exception as e:
        log.warning("fetch_user_flags failed (degrading to none): %s", e)
        return []
    out = []
    for r in res.data or []:
        mc = r.get("market_class")
        if mc is None or mc == market_class:
            out.append(r)
    return out


def fetch_projections_for_players(game_ids, markets, player_ids):
    """
    Projection rows for specific players (Patch 7 lottery construction needs
    market/odds for unsurfaced flagged batters). Returns {player_id: best row}.
    Only called when user flags exist.
    """
    if not (game_ids and player_ids):
        return {}
    res = sb.table("projections").select(
        "game_id, player_id, market, projected_prob, no_vig_prob, edge, "
        "best_american_odds, best_book, model_version"
    ).in_("game_id", game_ids).in_("market", markets) \
     .eq("model_version", REQUIRED_MODEL_VERSION) \
     .in_("player_id", player_ids).execute()
    out = {}
    for p in res.data or []:
        bid = p["player_id"]
        if bid not in out or (p.get("edge") or -9) > (out[bid].get("edge") or -9):
            out[bid] = p
    return out


# ─── v2 patch functions (net-new; unit-tested) ────────────────────────────────

def apply_catcher_boost(position, bstats, cfg=None):
    """
    Patch 1 — catcher promotion. CATCHERS ONLY (position == 'C'). If the batter
    clears ANY of three elite-power gates (OR), grant a tier promotion (+1 to the
    tier-hit count used for scoring) AND a confidence bonus. Never excludes; a
    catcher clearing none — or any non-catcher — gets (0, 0.0).

    Gates (OR): season_hr_pace   >= catcher_promote_hr_pace_min
                barrel_rank_mlb  <= catcher_promote_barrel_rank_max
                xslg (L14)       >= catcher_promote_xslg_l14_min

    UNPOPULATED = NO EFFECT: season_hr_pace / barrel_rank_mlb aren't written by
    pull_savant yet, so today the only live gate is xslg (already populated) —
    catchers without elite xSLG pass through unboosted (v1-equivalent).

    Returns (tier_boost:int in {0,1}, confidence_bonus:float).
    """
    if position != "C" or not bstats:
        return (0, 0.0)
    hr_pace = bstats.get("season_hr_pace")
    barrel_rank = bstats.get("barrel_rank_mlb")
    xslg = bstats.get("xslg")
    passed = (
        (hr_pace is not None and float(hr_pace) >= _cfg_num(cfg, "catcher_promote_hr_pace_min", 15)) or
        (barrel_rank is not None and float(barrel_rank) <= _cfg_num(cfg, "catcher_promote_barrel_rank_max", 50)) or
        (xslg is not None and float(xslg) >= _cfg_num(cfg, "catcher_promote_xslg_l14_min", 0.450))
    )
    if passed:
        return (1, _cfg_num(cfg, "conf_catcher_bonus", 0.10))
    return (0, 0.0)


def calculate_game_environment(park_adj, humidity_pct=None, elevation_ft=None, cfg=None):
    """
    Patch 2 (Option B) — informational stake environment multiplier.

    park_adj ALREADY contains park × wind × temp from pull_weather.py (the live
    wind/temp computation). We ONLY add humidity and elevation, multiplicatively:

        final = clamp(park_adj * humidity_mult * elevation_mult, [floor, ceil])

    The wind/temp coefficient rows in model_thresholds are REFERENCE-ONLY
    (documented magnitudes); this function does NOT read them — see pull_weather.py
    for the live wind/temp math. NEVER filters — only modifies stake_modifier
    (not confidence, not the projection).

    UNPOPULATED = NO EFFECT: there is no humidity source column on games and no
    elevation column on teams yet, so both multipliers default to 1.0 → env ==
    clamped park_adj (v1-equivalent) until a source is wired.
    """
    base = float(park_adj) if park_adj is not None else 1.0
    # humidity/elevation default to 1.0 when their source value is None. There is
    # no humidity column on games and no elevation column on teams yet, so BOTH
    # multipliers are 1.0 today → Patch 2 is v1-EQUIVALENT on these two axes until
    # pull_weather.py (or similar) starts populating them. Wiring them later needs
    # no change here — just pass real humidity_pct / elevation_ft.
    humidity_mult = 1.0
    if humidity_pct is not None and float(humidity_pct) >= _cfg_num(cfg, "humidity_threshold_pct", 70):
        humidity_mult = _cfg_num(cfg, "humidity_boost", 1.02)
    elevation_mult = 1.0
    if elevation_ft is not None and float(elevation_ft) >= _cfg_num(cfg, "elevation_threshold_ft", 4000):
        elevation_mult = _cfg_num(cfg, "elevation_boost", 1.05)
    raw = base * humidity_mult * elevation_mult
    floor = _cfg_num(cfg, "stake_mod_floor", 0.7)
    ceil = _cfg_num(cfg, "stake_mod_ceil", 1.3)
    return round(max(floor, min(ceil, raw)), 3)


def calculate_primary_signal(bstats, season_by_pitch, pitcher_primary,
                             pitcher_hr9_factor, cfg=None):
    """
    Patch 4 — blended HR-vs-primary-pitch rate (fraction 0-1), feeding the
    confidence base. Blend observed and predicted by sample size:

        observed_weight = min(1.0, sample_size / 50)
        signal = w*observed + (1-w)*predicted

      observed  = season by_pitch_type[primary].hr_pct / 100   (PERCENT→fraction)
      sample    = season by_pitch_type[primary].pa
      predicted = (career_barrel_for_family / 100) * league_hr_per_barrel
                  * pitcher_hr9_factor

    Per-family barrel comes from pa_vs_pitch (decision #2); career_barrel_pct
    scalar is the overall fallback.

    Expected pa_vs_pitch JSONB structure (contract for the future ingestion
    script that populates batter_stats.pa_vs_pitch):
        {
          "4SM":      {"pa": int, "barrel_pct": float (0-100)},  # by pitch type
          "fastball": {"pa": int, "barrel_pct": float (0-100)},  # family fallback
          ...
        }
    This function reads two key forms — the exact pitch type (e.g. "4SM") first,
    then the pitch family (e.g. "fastball" via _PITCH_FAMILY). barrel_pct is
    stored as PERCENT (0-100), matching the site-wide convention
    (batter_stats.barrel_pct, etc.).

    league_hr_per_barrel is REQUIRED from model_thresholds (no code fallback —
    raises if absent). pitcher_hr9_factor mirrors compute_projections.py's
    pitcher factor (see module constants).

    FALLBACKS (decision #3, "unpopulated = no effect"):
      per-family barrel → overall career_barrel_pct → L14 barrel_pct;
      predicted unavailable → observed only; observed unavailable → predicted
      only; neither → 0.0 (no base confidence contribution).
    """
    observed = None
    sample = 0.0
    if pitcher_primary and season_by_pitch:
        row = season_by_pitch.get(pitcher_primary) or {}
        hr_pct = row.get("hr_pct")
        if hr_pct is not None:
            observed = float(hr_pct) / 100.0
            sample = float(row.get("pa") or 0)

    barrel_for_family = None
    if bstats:
        pvp = bstats.get("pa_vs_pitch") or {}
        if pitcher_primary and isinstance(pvp, dict):
            fam = _PITCH_FAMILY.get(pitcher_primary)
            entry = pvp.get(pitcher_primary) or (pvp.get(fam) if fam else None)
            if isinstance(entry, dict) and entry.get("barrel_pct") is not None:
                barrel_for_family = float(entry["barrel_pct"])
        if barrel_for_family is None and bstats.get("career_barrel_pct") is not None:
            barrel_for_family = float(bstats["career_barrel_pct"])
        if barrel_for_family is None and bstats.get("barrel_pct") is not None:
            barrel_for_family = float(bstats["barrel_pct"])

    predicted = None
    if barrel_for_family is not None:
        # league_hr_per_barrel is REQUIRED (no fallback) — raises/aborts if the
        # model_thresholds row is missing and cfg is empty (decision #4).
        league_hr_per_barrel = _cfg_required(cfg, "league_hr_per_barrel")
        predicted = (barrel_for_family / 100.0) * league_hr_per_barrel * float(pitcher_hr9_factor or 1.0)

    if observed is not None and predicted is not None:
        w = min(1.0, sample / 50.0)
        return round(w * observed + (1.0 - w) * predicted, 5)
    if observed is not None:
        return round(observed, 5)
    if predicted is not None:
        return round(predicted, 5)
    return 0.0


def get_near_miss_boost(near_miss_count, cfg=None):
    """
    Patch 6 — recent-hard-contact boost. PURE (count → boost) so it's unit
    testable; the count is pre-fetched once (fetch_near_miss_counts).

    Returns near_miss_boost (0.05) if count >= near_miss_min_events (2), else 0.0.
    UNPOPULATED = NO EFFECT: near_miss_events is empty until ingestion is wired,
    so counts are 0 and this returns 0.0 today.
    """
    min_events = int(_cfg_num(cfg, "near_miss_min_events", 2))
    if near_miss_count and near_miss_count >= min_events:
        return _cfg_num(cfg, "near_miss_boost", 0.05)
    return 0.0


def calculate_confidence(primary_signal, tier2_hits, flag_bonus_total=0.0, cfg=None):
    """
    Patch 9 — continuous confidence in [0,1], the v2 ranking signal.

        base  = min(primary_signal * conf_primary_signal_mult, conf_base_cap)
        tier2 = min(tier2_hits * conf_tier2_signal_weight, conf_tier2_cap)
        final = clamp(base + tier2 + flag_bonus_total, 0, 1)

    flag_bonus_total is the SUM of applicable flag bonuses (catcher, near-miss,
    stack [cards], user-flag conviction). Environment does NOT contribute to
    confidence (only to stake). Returns (confidence_score, breakdown_dict).

    NOTE: the Patch 4 sample-size blend that produces primary_signal applies to
    HR only. For HRR, primary_signal is hit_per_pa per the v1 framework (see
    _enrich_with_tiers).
    """
    mult = _cfg_num(cfg, "conf_primary_signal_mult", 4.0)
    base_cap = _cfg_num(cfg, "conf_base_cap", 0.40)
    t2_weight = _cfg_num(cfg, "conf_tier2_signal_weight", 0.06)
    t2_cap = _cfg_num(cfg, "conf_tier2_cap", 0.30)

    base = min(float(primary_signal or 0.0) * mult, base_cap)
    tier2 = min(int(tier2_hits or 0) * t2_weight, t2_cap)
    final = max(0.0, min(1.0, base + tier2 + float(flag_bonus_total or 0.0)))
    breakdown = {
        "base_score": round(base, 4),
        "tier2_contribution": round(tier2, 4),
        "flag_bonus_total": round(float(flag_bonus_total or 0.0), 4),
        "final_confidence": round(final, 4),
    }
    return (round(final, 3), breakdown)


def confidence_to_tier(score, cfg=None):
    """Patch 9 — map confidence_score → letter via configurable breakpoints."""
    if score is None:
        return None
    s = float(score)
    if s >= _cfg_num(cfg, "confidence_tier_a_plus", 0.75):  return "A+"
    if s >= _cfg_num(cfg, "confidence_tier_a", 0.65):       return "A"
    if s >= _cfg_num(cfg, "confidence_tier_a_minus", 0.55): return "A-"
    if s >= _cfg_num(cfg, "confidence_tier_b_plus", 0.45):  return "B+"
    if s >= _cfg_num(cfg, "confidence_tier_b", 0.35):       return "B"
    if s >= _cfg_num(cfg, "confidence_tier_c_plus", 0.25):  return "C+"
    return "C"


def _apply_tier_floor(tier, floor="B+"):
    """Raise `tier` up to `floor` if below it (Patch 7 surfaced-flag floor)."""
    if tier is None:
        return floor
    if tier not in _TIER_RANK:
        log.warning("Unknown tier %r passed to _apply_tier_floor; "
                    "defaulting to floor %s", tier, floor)
        return floor
    if _TIER_RANK[tier] > _TIER_RANK.get(floor, 99):
        return floor
    return tier


def _market_context(edge, ev_per_dollar, cfg=None):
    """
    Patch 3 — DISPLAY-ONLY market context. cebolla_edge and ev_chart are NO
    LONGER in ranking/stake/confidence math; they live here for the frontend.
    The edge < -0.25 warning is just a string (no behavior).
    """
    warning = None
    try:
        if edge is not None and float(edge) < -0.25:
            warning = "Model edge strongly negative (< -0.25) — market disagrees sharply."
    except (TypeError, ValueError):
        pass
    return {
        "cebolla_edge": float(edge) if edge is not None else None,
        "ev_chart": float(ev_per_dollar) if ev_per_dollar is not None else None,
        "edge_warning": warning,
    }


def apply_user_flags(candidates, flags, projections_by_player, players_by_id,
                     games_by_id, market_class, cfg=None):
    """
    Patch 7 — honor manual user_flags for the slate (post-ranking).

      · Batter ALREADY surfaced → add conviction confidence bonus + enforce a
        'B+' tier floor (never drops below B+).
      · NOT surfaced → ADD as a 'C+' lottery candidate with stake_modifier =
        user_flag_lottery_stake (0.4) + conviction bonus. Requires a projection
        row for market/odds; if none, log and skip (can't manufacture a price).

    Conviction → confidence: gut conf_user_flag_gut, matchup conf_user_flag_matchup,
    hot_streak conf_user_flag_hot_streak.

    UNPOPULATED = NO EFFECT: user_flags is empty until populated → no-op today.
    Returns the (possibly modified/extended) candidate list, re-ranked.
    """
    if not flags:
        return candidates

    conv_bonus = {
        "gut": _cfg_num(cfg, "conf_user_flag_gut", 0.07),
        "matchup": _cfg_num(cfg, "conf_user_flag_matchup", 0.10),
        "hot_streak": _cfg_num(cfg, "conf_user_flag_hot_streak", 0.08),
    }
    lottery_stake = _cfg_num(cfg, "user_flag_lottery_stake", 0.4)
    by_player = {c["player_id"]: c for c in candidates}

    for f in flags:
        bid = f.get("batter_id")
        conviction = f.get("conviction")
        bonus = conv_bonus.get(conviction, 0.0)
        if bid in by_player:
            c = by_player[bid]
            new_conf = max(0.0, min(1.0, (c.get("confidence_score") or 0.0) + bonus))
            c["confidence_score"] = round(new_conf, 3)
            c["confidence_tier"] = _apply_tier_floor(confidence_to_tier(new_conf, cfg), "B+")
            meta = c.setdefault("tier_metadata", {})
            meta["user_flag"] = {"conviction": conviction, "bonus": bonus, "floor": "B+"}
        else:
            proj = projections_by_player.get(bid)
            player = players_by_id.get(bid)
            if not proj or not player:
                log.warning("  user_flag: batter %s flagged but no projection/player — "
                            "cannot add lottery, skipping", bid)
                continue
            base_conf = max(0.0, min(1.0, bonus))  # conviction only; no framework signal
            # Resolve team context so the lottery pick displays correctly on
            # cebolla.live AND is eligible for pick_cards stack detection (which
            # groups by team). Mirrors the home/away logic in _enrich_with_tiers.
            own_abbrev = opp_abbrev = None
            game = games_by_id.get(proj.get("game_id"))
            if game:
                is_home = player.get("team_id") == game.get("home_team_id")
                home_ab = (game.get("home_team") or {}).get("abbrev")
                away_ab = (game.get("away_team") or {}).get("abbrev")
                own_abbrev = home_ab if is_home else away_ab
                opp_abbrev = away_ab if is_home else home_ab
            candidates.append({
                "game_id": proj.get("game_id"),
                "player_id": bid,
                "player_mlbam_id": player.get("mlbam_id"),
                "player_name": player.get("name"),
                "team_abbrev": own_abbrev,
                "opponent_abbrev": opp_abbrev,
                "market": proj.get("market"),
                "projected_prob": proj.get("projected_prob"),
                "no_vig_prob": proj.get("no_vig_prob"),
                "edge": proj.get("edge"),
                "ev_per_dollar": None,
                "american_odds": proj.get("best_american_odds"),
                "book": proj.get("best_book"),
                "model_version": proj.get("model_version"),
                "contact_score": None,
                "tier1_hits": 0,
                "tier2_hits": 0,
                "tier_score": _cfg_num(cfg, "score_base_stowers", 0.85),
                "qualification_path": "user_flag_lottery",
                "stake_modifier": lottery_stake,
                "confidence_score": round(base_conf, 3),
                "confidence_tier": "C+",
                "tier_metadata": {"user_flag": {"conviction": conviction, "bonus": bonus,
                                                "lottery": True}},
                "market_context": _market_context(proj.get("edge"), None, cfg=cfg),
                "combined_tier": None,
                "combined_trend": None,
            })
            by_player[bid] = candidates[-1]

    candidates.sort(key=lambda c: ((c.get("confidence_score") or 0.0), *tier3_key(c)), reverse=True)
    return candidates


def _apply_flags_for_market(candidates, date_iso, market_class, markets,
                            game_ids, players_by_id, games_by_id, cfg):
    """Fetch user flags for this market and apply them (Patch 7). No-op if none."""
    flags = fetch_user_flags(date_iso, market_class)
    if not flags:
        return candidates
    surfaced_ids = {c["player_id"] for c in candidates}
    unsurfaced_ids = [f["batter_id"] for f in flags if f.get("batter_id") not in surfaced_ids]
    proj_by_player = (fetch_projections_for_players(game_ids, markets, unsurfaced_ids)
                      if unsurfaced_ids else {})
    log.info("  [%s] user flags: %d (%d unsurfaced)",
             market_class.upper(), len(flags), len(unsurfaced_ids))
    return apply_user_flags(candidates, flags, proj_by_player, players_by_id,
                            games_by_id, market_class, cfg=cfg)


# ─── Tier-aware candidate construction ────────────────────────────────────────

def _enrich_with_tiers(
    p, market_class,
    games_by_id, players_by_id,
    contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
    batter_season_by_pitch=None,
    cfg=None, near_miss_counts=None, pitcher_hr9_by_id=None,
):
    """
    Evaluate a projection through the tier system + v2 confidence. None if
    disqualified.

    v2 (patches 1,2,3,4,6,9): catcher promotion, multiplicative environment
    (stake only), display-only market_context, blended primary signal,
    near-miss boost, and a continuous confidence score + tier letter (the v2
    ranking signal). Every v2 input degrades to no-effect when unpopulated, so
    picks are v1-equivalent today.
    """
    game = games_by_id.get(p["game_id"])
    player = players_by_id.get(p["player_id"])
    if not game or not player:
        return None

    # FROZEN heat filter (safety net)
    heat = heat_by_player.get(p["player_id"]) if heat_by_player else None
    if heat and heat.get("combined_trend") is not None:
        if heat["combined_trend"] <= HEAT_FROZEN_THRESHOLD:
            return None

    # Find opposing pitcher
    is_home = player.get("team_id") == game.get("home_team_id")
    opposing_team_id = game.get("away_team_id") if is_home else game.get("home_team_id")
    opposing_pitcher_id = starter_by_game_team.get((p["game_id"], opposing_team_id))

    bstats = batter_stats_l14.get(p["player_id"])
    sbp = (batter_season_by_pitch or {}).get(p["player_id"]) if batter_season_by_pitch else None

    pitcher_primary = None
    if opposing_pitcher_id:
        arsenal_rows = pitcher_arsenals.get(opposing_pitcher_id) or []
        pitcher_primary = primary_pitch_type(arsenal_rows)

    bvp_row = bvp_pairs.get((p["player_id"], opposing_pitcher_id)) if opposing_pitcher_id else None

    # Tier 1 / Tier 2 (thresholds resolved from configured model_thresholds)
    if market_class == "hr":
        t1_hits, t1_detail = evaluate_tier1_hr(bstats, pitcher_primary, season_by_pitch=sbp, cfg=cfg)
    else:
        t1_hits, t1_detail = evaluate_tier1_hrr(bstats, bvp_row, cfg=cfg)
    heat_tier = heat.get("combined_tier") if heat else None
    contact = contact_by_player.get(p["player_id"])
    t2_hits, t2_detail = evaluate_tier2(bstats, heat_tier, contact, bvp_row, cfg=cfg)

    # Patch 1 — catcher promotion. tier_boost promotes the T1 count used for
    # scoring/qualification (v1 tier_score axis); the confidence bonus is applied
    # separately below (v2 axis). Orthogonal per decision #4c.
    tier_boost, catcher_conf_bonus = apply_catcher_boost(player.get("position"), bstats, cfg=cfg)
    effective_t1 = t1_hits + tier_boost

    # Qualification + score (uses the promoted T1 count)
    score = score_candidate(effective_t1, t2_hits, cfg=cfg)
    if score is None:
        return None
    qpath = qualification_path(effective_t1, t2_hits, cfg=cfg)

    # EV per dollar (display + Tier 3 tiebreak)
    edge_val = p.get("edge")
    projected = p.get("projected_prob")
    american = p.get("best_american_odds")
    ev_per_dollar = None
    if projected is not None and american is not None:
        try:
            pp = float(projected)
            am = int(american)
            decimal = (am / 100.0 + 1.0) if am > 0 else (100.0 / abs(am) + 1.0)
            ev_per_dollar = pp * (decimal - 1.0) - (1.0 - pp)
        except (TypeError, ValueError):
            pass

    # Patch 2 — environment multiplier (Option B). park_adj already carries
    # park×wind×temp; humidity/elevation have no source column yet → 1.0 (inert).
    stake_mod = calculate_game_environment(
        p.get("park_adj"), humidity_pct=None, elevation_ft=None, cfg=cfg
    )

    # Patch 4 — primary signal feeding the confidence base.
    pitcher_hr9_factor = (pitcher_hr9_by_id or {}).get(opposing_pitcher_id, 1.0)
    if market_class == "hr":
        primary_signal = calculate_primary_signal(bstats, sbp, pitcher_primary,
                                                  pitcher_hr9_factor, cfg=cfg)
    else:
        # HRR analog: hit_per_pa is the core HRR rate (calculate_primary_signal is
        # HR-vs-pitch specific). FLAGGED in the v2 diff — spec only defined Patch 4
        # for HR; confirm desired HRR signal.
        primary_signal = (float(bstats["hit_per_pa"])
                          if (bstats and bstats.get("hit_per_pa") is not None) else 0.0)

    # Patch 6 — near-miss boost (pre-fetched counts; 0 until ingestion wired)
    nm_count = (near_miss_counts or {}).get(p["player_id"], 0)
    near_miss_bonus = get_near_miss_boost(nm_count, cfg=cfg)

    # Patch 9 — confidence (environment does NOT contribute; user-flag conviction
    # bonus is applied later in apply_user_flags)
    flag_bonus_total = catcher_conf_bonus + near_miss_bonus
    confidence_score, conf_breakdown = calculate_confidence(
        primary_signal, t2_hits, flag_bonus_total, cfg=cfg
    )
    confidence_tier = confidence_to_tier(confidence_score, cfg=cfg)

    own_abbrev = (game["home_team"] if is_home else game["away_team"])["abbrev"]
    opp_abbrev = (game["away_team"] if is_home else game["home_team"])["abbrev"]

    # tier_metadata = framework internals + the required per-pick log fields
    metadata = {
        "tier1": t1_detail,
        "tier2": t2_detail,
        "qualification_path": qpath,
        "stake_modifier": stake_mod,
        # Required per-pick logging (constraint): signals, gate, scores, letter.
        "tier1_signals": t1_hits,
        "tier2_signals": t2_hits,
        "catcher_tier_boost": tier_boost,
        "gate_path": qpath,
        "primary_signal": round(primary_signal, 5),
        "base_score": conf_breakdown["base_score"],
        "tier2_contribution": conf_breakdown["tier2_contribution"],
        "flag_bonus_total": conf_breakdown["flag_bonus_total"],
        "near_miss_count": nm_count,
        "near_miss_bonus": near_miss_bonus,
        "catcher_conf_bonus": catcher_conf_bonus,
        "environment_multiplier": stake_mod,
        "final_confidence": confidence_score,
        "tier_letter": confidence_tier,
    }

    return {
        "game_id": p["game_id"],
        "player_id": p["player_id"],
        "player_mlbam_id": player.get("mlbam_id"),
        "player_name": player["name"],
        "team_abbrev": own_abbrev,
        "opponent_abbrev": opp_abbrev,
        "market": p["market"],
        "projected_prob": p["projected_prob"],
        "no_vig_prob": p["no_vig_prob"],
        "edge": edge_val,
        "ev_per_dollar": ev_per_dollar,
        "american_odds": p["best_american_odds"],
        "book": p["best_book"],
        "model_version": p["model_version"],
        "contact_score": contact,
        "tier1_hits": t1_hits,
        "tier2_hits": t2_hits,
        "tier_score": score,
        "qualification_path": qpath,
        "stake_modifier": stake_mod,
        "tier_metadata": metadata,
        "confidence_score": confidence_score,
        "confidence_tier": confidence_tier,
        "market_context": _market_context(edge_val, ev_per_dollar, cfg=cfg),
        "combined_tier": heat_tier,
        "combined_trend": heat.get("combined_trend") if heat else None,
    }


def fetch_hr_candidates(
    date_iso, games, players_by_id, contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
    batter_season_by_pitch=None,
    cfg=None, near_miss_counts=None, pitcher_hr9_by_id=None,
):
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}

    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, edge, "
                "best_american_odds, best_book, model_version, park_adj, weather_adj, "
                "stake_modifier") \
        .in_("game_id", game_ids) \
        .eq("market", HR_MARKET) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .gte("projected_prob", HR_MIN_PROJECTED_PROB) \
        .gte("edge", HR_MIN_EDGE) \
        .not_.is_("best_american_odds", "null") \
        .execute()

    enriched = []
    errors_by_type = {}
    for p in proj_res.data or []:
        try:
            cand = _enrich_with_tiers(
                p, "hr", games_by_id, players_by_id, contact_by_player, heat_by_player,
                batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
                batter_season_by_pitch=batter_season_by_pitch,
                cfg=cfg, near_miss_counts=near_miss_counts, pitcher_hr9_by_id=pitcher_hr9_by_id,
            )
        except Exception as e:
            # A patch threw at the candidate level → skip THIS candidate (the
            # "never publish if any patch throws" contract). Caught at the call
            # site, NOT inside _enrich_with_tiers, so real bugs in patch logic
            # still surface per-candidate instead of being hidden. Exception
            # (not BaseException) so Ctrl+C / SystemExit still kill the process.
            etype = type(e).__name__
            errors_by_type[etype] = errors_by_type.get(etype, 0) + 1
            log.warning("HR enrich skipped player_id=%s: %s: %s",
                        p.get("player_id"), etype, e)
            continue
        if cand:
            enriched.append(cand)

    if errors_by_type:
        log.warning("HR: %d candidate(s) skipped due to enrichment errors: %s",
                    sum(errors_by_type.values()), errors_by_type)

    # Patch 9 — rank by confidence_score (v2 signal); tier3_key breaks ties.
    enriched.sort(key=lambda c: ((c.get("confidence_score") or 0.0), *tier3_key(c)), reverse=True)
    return enriched


def fetch_hrr_candidates(
    date_iso, games, players_by_id, contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
    batter_season_by_pitch=None,
    cfg=None, near_miss_counts=None, pitcher_hr9_by_id=None,
):
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}

    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, edge, "
                "best_american_odds, best_book, model_version, park_adj, weather_adj, "
                "stake_modifier") \
        .in_("game_id", game_ids) \
        .in_("market", HRR_MARKETS) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .gte("edge", HRR_MIN_EDGE) \
        .not_.is_("best_american_odds", "null") \
        .execute()

    by_batter = {}
    for p in proj_res.data or []:
        floor = HRR_MIN_PROB_BY_LINE.get(p["market"])
        if floor is None:
            continue
        if (p["projected_prob"] or 0) < floor:
            continue
        bid = p["player_id"]
        by_batter.setdefault(bid, []).append(p)

    enriched = []
    errors_by_type = {}
    for player_id, projs in by_batter.items():
        best = max(projs, key=lambda x: (float(x["edge"] or 0),
                                         float(x["projected_prob"] or 0)))
        try:
            cand = _enrich_with_tiers(
                best, "hrr", games_by_id, players_by_id, contact_by_player, heat_by_player,
                batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
                batter_season_by_pitch=batter_season_by_pitch,
                cfg=cfg, near_miss_counts=near_miss_counts, pitcher_hr9_by_id=pitcher_hr9_by_id,
            )
        except Exception as e:
            # See fetch_hr_candidates: per-candidate skip on a patch throw, caught
            # at the call site, Exception (not BaseException).
            etype = type(e).__name__
            errors_by_type[etype] = errors_by_type.get(etype, 0) + 1
            log.warning("HRR enrich skipped player_id=%s: %s: %s",
                        player_id, etype, e)
            continue
        if cand:
            enriched.append(cand)

    if errors_by_type:
        log.warning("HRR: %d candidate(s) skipped due to enrichment errors: %s",
                    sum(errors_by_type.values()), errors_by_type)

    enriched.sort(key=lambda c: ((c.get("confidence_score") or 0.0), *tier3_key(c)), reverse=True)
    return enriched


# ─── Insertion ────────────────────────────────────────────────────────────────

def insert_pod(pick, date_iso, market_class):
    sb.table("pods").insert({
        "pod_date": date_iso,
        "market_class": market_class,
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
        "contact_score": pick["contact_score"],
        # Keep combined_score for backward compat with any panels still reading it.
        # tier_score is 0.85-1.25; scale to stay within original 0-10000 range.
        # 1.25 × 8000 = 10000 max; 0.85 × 8000 = 6800 min.
        "combined_score": round(pick["tier_score"] * 8000, 1),
        # Tier system fields
        "tier1_hits": pick["tier1_hits"],
        "tier2_hits": pick["tier2_hits"],
        "tier_score": pick["tier_score"],
        "stake_modifier": pick["stake_modifier"],
        # tier_metadata is JSONB — pass raw dict, supabase-py serializes
        # automatically. Do NOT json.dumps() or it gets stored as a
        # string-valued JSON, breaking ->> queries.
        "tier_metadata": pick["tier_metadata"],
        # v2 — Patch 9 (confidence) + Patch 3 (display-only market context)
        "confidence_score": pick.get("confidence_score"),
        "confidence_tier": pick.get("confidence_tier"),
        "market_context": pick.get("market_context"),
        "stake": 10.00,
        "status": "pending",
    }).execute()


def log_top3(label, candidates):
    log.info("Top %s candidates by confidence_score (then edge/EV):", label)
    for i, c in enumerate(candidates[:3], 1):
        cs = c.get("contact_score")
        cs_str = f"{cs:.0f}" if cs is not None else "—"
        heat_str = f" heat {c['combined_tier']}" if c.get("combined_tier") else ""
        sm = c.get("stake_modifier") or 1.0
        sm_str = f" [stake_mod {sm:.2f}]" if abs(sm - 1.0) > 0.02 else ""
        ev = c.get("ev_per_dollar")
        ev_str = f" ev/${ev:+.3f}" if ev is not None else ""
        conf = c.get("confidence_score")
        conf_str = f"{conf:.3f}" if conf is not None else "—"
        proj = c.get("projected_prob")
        proj_str = f"{100 * float(proj):.1f}%" if proj is not None else "—"
        odds = c.get("american_odds")
        odds_str = f"{int(odds):+d}" if odds is not None else "—"
        edge = c.get("edge")
        edge_str = f"{float(edge):.3f}" if edge is not None else "—"
        log.info(
            "  #%d  %s (%s vs %s)  %s  conf %s/%s  proj %s  odds %s  edge %s%s  "
            "T1=%d/3 T2=%d/4 path=%s  tier_score=%.3f  contact %s%s%s",
            i, c.get("player_name"), c.get("team_abbrev") or "?", c.get("opponent_abbrev") or "?",
            c.get("market"),
            c.get("confidence_tier") or "—", conf_str,
            proj_str, odds_str, edge_str, ev_str,
            c.get("tier1_hits") or 0, c.get("tier2_hits") or 0, c.get("qualification_path"),
            c.get("tier_score") or 0.0, cs_str, heat_str, sm_str,
        )


def pick_for_market(date_iso, market_class, candidates):
    if existing_pod_for(date_iso, market_class):
        log.info("[%s] POD already exists for %s. Skipping.", market_class.upper(), date_iso)
        return
    if not candidates:
        log.warning("[%s] No tier-qualifying candidates for %s. Publishing nothing (conviction signal).",
                    market_class.upper(), date_iso)
        return
    pick = candidates[0]
    insert_pod(pick, date_iso, market_class)
    odds = pick.get("american_odds")
    odds_str = f"{int(odds):+d}" if odds is not None else "—"
    edge = pick.get("edge")
    edge_str = f"{float(edge):.3f}" if edge is not None else "—"
    log.info(
        "[%s] ✓ POD locked for %s: %s @ %s  conf=%s/%.3f  (edge %s, T1=%d/3 T2=%d/4 tier_score=%.3f path=%s)",
        market_class.upper(), date_iso, pick.get("player_name"), odds_str,
        pick.get("confidence_tier") or "—", pick.get("confidence_score") or 0.0,
        edge_str, pick.get("tier1_hits") or 0, pick.get("tier2_hits") or 0,
        pick.get("tier_score") or 0.0, pick.get("qualification_path"),
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = get_today_iso()
    log.info("🧅 POD picker — slate %s (HR + HRR, tier system v0.4.0)", today)

    # ─── Idempotency gate ────────────────────────────────────────────────
    # Exit cleanly if today's POD is already locked for this model version.
    # This lets the Cloudflare Worker's 3:30 AM run AND the GitHub 3:43 AM
    # backup both fire safely — whichever runs second sees the picks already
    # exist and skips. Without this, both would race and we'd get duplicates.
    existing = sb.table("pods").select("id, market_class") \
        .eq("pod_date", today) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .execute()
    if existing.data:
        markets = {row.get("market_class", "hr") for row in existing.data}
        log.info(
            "POD already locked for %s (model %s, markets=%s). Skipping.",
            today, REQUIRED_MODEL_VERSION, sorted(markets),
        )
        return

    # Load tunable thresholds ONCE; cache in tier_system. On a query failure,
    # tier_system evaluators and the patch functions fall back to their
    # documented defaults (resilience over purity — decision #1).
    try:
        cfg = load_thresholds(sb)
        configure(cfg)
        log.info("Loaded %d thresholds from model_thresholds.", len(cfg))
    except Exception as e:
        cfg = {}
        log.warning("model_thresholds load failed (%s) — using _DEFAULTS fallbacks.", e)

    games = fetch_today_games(today)
    if not games:
        log.warning("No games scheduled for %s.", today)
        return

    game_ids = [g["id"] for g in games]
    season = datetime.now(timezone.utc).year

    proj_ids_res = sb.table("projections") \
        .select("player_id, market, projected_prob, edge, best_american_odds") \
        .in_("game_id", game_ids) \
        .in_("market", [HR_MARKET] + HRR_MARKETS) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .not_.is_("edge", "null") \
        .execute()
    all_proj_player_ids = list({p["player_id"] for p in (proj_ids_res.data or [])})
    if not all_proj_player_ids:
        log.warning("No projections with edge data for %s.", today)
        return

    player_res = sb.table("players").select("id, mlbam_id, name, team_id, position") \
        .in_("id", all_proj_player_ids).execute()
    players_by_id = {p["id"]: p for p in (player_res.data or [])}

    log.info("Fetching tier-system data for %d candidate players...", len(all_proj_player_ids))
    batter_stats_l14 = fetch_batter_l14_stats(all_proj_player_ids, season)
    log.info("  L14 stats: %d rows", len(batter_stats_l14))

    batter_season_by_pitch = fetch_batter_season_by_pitch(all_proj_player_ids, season)
    log.info("  Season by_pitch_type: %d batters", len(batter_season_by_pitch))

    starter_by_game_team = fetch_starting_pitcher_for_game(games)
    pitcher_ids = list({pid for pid in starter_by_game_team.values() if pid})
    log.info("  Starting pitchers identified: %d", len(pitcher_ids))

    pitcher_arsenals = fetch_pitcher_arsenals(pitcher_ids, season)
    log.info("  Arsenals: %d pitchers with arsenal data", len(pitcher_arsenals))

    bvp_pairs = fetch_bvp(all_proj_player_ids, pitcher_ids)
    log.info("  BvP pairs: %d", len(bvp_pairs))

    contact_by_player = fetch_contact_scores(all_proj_player_ids, season)
    heat_by_player = fetch_batter_heat(all_proj_player_ids, today)
    if heat_by_player:
        log.info("  Heat: %d batters", len(heat_by_player))
    else:
        log.info("  Heat: empty (degrading to no-heat)")

    # v2 inputs
    pitcher_hr9_by_id = fetch_pitcher_hr9_factors(pitcher_ids, season)
    log.info("  Pitcher HR/9 factors: %d", len(pitcher_hr9_by_id))
    near_miss_counts = fetch_near_miss_counts(all_proj_player_ids, today, cfg)
    log.info("  Near-miss qualifying batters: %d (empty until ingestion wired)", len(near_miss_counts))
    games_by_id = {g["id"]: g for g in games}

    # ── HR POD ──
    hr_candidates = fetch_hr_candidates(
        today, games, players_by_id, contact_by_player, heat_by_player,
        batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
        batter_season_by_pitch=batter_season_by_pitch,
        cfg=cfg, near_miss_counts=near_miss_counts, pitcher_hr9_by_id=pitcher_hr9_by_id,
    )
    hr_candidates = _apply_flags_for_market(
        hr_candidates, today, "hr", [HR_MARKET], game_ids, players_by_id, games_by_id, cfg
    )
    log.info("HR candidates qualifying tier system: %d", len(hr_candidates))
    log_top3("HR", hr_candidates)
    pick_for_market(today, "hr", hr_candidates)

    # ── HRR POD ──
    hrr_candidates = fetch_hrr_candidates(
        today, games, players_by_id, contact_by_player, heat_by_player,
        batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
        batter_season_by_pitch=batter_season_by_pitch,
        cfg=cfg, near_miss_counts=near_miss_counts, pitcher_hr9_by_id=pitcher_hr9_by_id,
    )
    hrr_candidates = _apply_flags_for_market(
        hrr_candidates, today, "hrr", HRR_MARKETS, game_ids, players_by_id, games_by_id, cfg
    )
    log.info("HRR candidates qualifying tier system: %d", len(hrr_candidates))
    log_top3("HRR", hrr_candidates)
    pick_for_market(today, "hrr", hrr_candidates)

    log.info("🧅 POD picker complete")


if __name__ == "__main__":
    main()
