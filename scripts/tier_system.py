"""
tier_system.py — shared tier evaluator used by pick_pod.py and pick_cards.py.

Encapsulates the tier-based qualification framework that replaces the old
multiplicative combined_score math (edge × contact × heat).

FRAMEWORK
═════════
Tier 1 (dominant gates):
  HR market:
    T1A: barrel% L14 ≥ t1_hr_barrel_min
    T1B: xSLG L14 ≥ t1_hr_xslg_min
    T1C: HR rate vs pitcher's primary pitch ≥ t1_hr_hrvspitch_min
  HRR market:
    T1A: hit rate L14 ≥ t1_hrr_hitrate_min (hit_per_pa)
    T1B: xBA L14 ≥ t1_hrr_xba_min
    T1C: BvP positive (career BA ≥ t1_hrr_bvp_ba_min, min t1_hrr_bvp_pa_min PA)

Tier 2 (confirming signals — capped bonus):
  T2A: heat tier ∈ t2_heat_tiers_qualifying (per batter_trends.combined_tier)
  T2B: HH% L14 ≥ t2_hh_pct_min
  T2C: contact score ≥ t2_contact_min
  T2D: BvP positive (career BA ≥ t2_bvp_ba_min, min t2_bvp_pa_min PA)

Tier 3 (tiebreakers — used after tier_score): edge, EV per dollar, platoon.
Tier 4 (informational stake modifier — NOT pick selection): park × weather.

SCORING
═══════
Qualifies if: (a) ≥2 Tier 1, OR (b) Stowers rule: exactly 1 Tier 1 AND
≥stowers_tier2_required Tier 2. Base by path: triple/standard/stowers. Plus
tier2_bonus_per_hit per T2 hit, capped at tier2_bonus_cap.

THRESHOLD SOURCING  (v2 — no hardcoded thresholds)
══════════════════════════════════════════════════
All thresholds now live in the `model_thresholds` table. The pickers call
`configure(load_thresholds(sb))` ONCE per run; this module caches the dict and
every evaluator reads from it via in-memory lookups (no per-candidate queries).

Resolution precedence in each function:
    explicit `cfg=` argument  >  module-cached cfg (configure())  >  _DEFAULTS

_DEFAULTS mirrors the migration-23 seed values and exists ONLY as a last-resort
fallback (e.g. model_thresholds unreachable, or a not-yet-configured caller).
When it is used, a one-time WARNING is logged. model_thresholds is authoritative.
Unit tests inject a `cfg` dict directly.
"""

import logging
from typing import Optional

log = logging.getLogger("tier_system")


# ─── Fallback defaults (mirror sql/23 model_thresholds seed) ───────────────────
# Authoritative source is the model_thresholds table; this is resilience only.
_DEFAULTS = {
    # Tier 1 · HR
    "t1_hr_barrel_min":      12.0,
    "t1_hr_xslg_min":        0.600,
    "t1_hr_hrvspitch_min":   8.0,
    # Tier 1 · HRR
    "t1_hrr_hitrate_min":    0.30,
    "t1_hrr_xba_min":        0.280,
    "t1_hrr_bvp_ba_min":     0.280,
    "t1_hrr_bvp_pa_min":     8,
    # Tier 2
    "t2_heat_tiers_qualifying": {"HOT", "BLAZING"},
    "t2_hh_pct_min":         45.0,
    "t2_contact_min":        70.0,
    "t2_bvp_ba_min":         0.280,
    "t2_bvp_pa_min":         8,
    # Scoring
    "score_base_triple":     1.10,
    "score_base_standard":   1.00,
    "score_base_stowers":    0.85,
    "tier2_bonus_per_hit":   0.05,
    "tier2_bonus_cap":       0.15,
    "stowers_tier2_required": 3,
    # Tier 4 stake modifier clamps
    "stake_mod_floor":       0.7,
    "stake_mod_ceil":        1.3,
}

# Module-level cache, populated by configure().
_active_cfg: Optional[dict] = None
_warned_fallback = False


# ─── Config loading / caching ──────────────────────────────────────────────────

def load_thresholds(sb) -> dict:
    """
    Read the entire model_thresholds table in ONE query and return a
    {key: value} dict. 'set'-unit rows are parsed into a Python set from the
    comma-joined text_value; everything else takes num_value as a float.

    Call this once per run, then pass the result to configure().
    """
    res = sb.table("model_thresholds").select(
        "key, num_value, text_value, unit"
    ).execute()
    cfg: dict = {}
    for r in (res.data or []):
        key = r["key"]
        if r.get("unit") == "set":
            raw = r.get("text_value") or ""
            cfg[key] = {x.strip() for x in raw.split(",") if x.strip()}
        else:
            nv = r.get("num_value")
            cfg[key] = float(nv) if nv is not None else None
    return cfg


def configure(cfg: dict) -> None:
    """Set the process-wide threshold cache (call once after load_thresholds)."""
    global _active_cfg
    _active_cfg = dict(cfg)


def _resolve(cfg: Optional[dict]) -> dict:
    """Resolve the active config: explicit arg > module cache > _DEFAULTS."""
    global _warned_fallback
    if cfg is not None:
        return cfg
    if _active_cfg is not None:
        return _active_cfg
    if not _warned_fallback:
        log.warning(
            "tier_system thresholds not configured — falling back to _DEFAULTS. "
            "Call configure(load_thresholds(sb)) at startup; model_thresholds is "
            "authoritative."
        )
        _warned_fallback = True
    return _DEFAULTS


def _num(cfg: Optional[dict], key: str) -> float:
    """Numeric threshold lookup with default fallback."""
    val = _resolve(cfg).get(key)
    return _DEFAULTS[key] if val is None else val


def _set(cfg: Optional[dict], key: str) -> set:
    """Set-valued threshold lookup (e.g. qualifying heat tiers)."""
    val = _resolve(cfg).get(key)
    return _DEFAULTS[key] if val is None else val


# ─── Tier 1 evaluators ────────────────────────────────────────────────────────

def evaluate_tier1_hr(
    batter_stats_l14: Optional[dict],
    pitcher_primary_pitch_type: Optional[str],
    season_by_pitch: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> tuple[int, dict]:
    """
    Evaluate HR-market Tier 1 thresholds.

    Args:
        batter_stats_l14: row from batter_stats (window_type='l14', vs_hand='A')
            with fields: barrel_pct, xslg. Used for T1A and T1B.
        pitcher_primary_pitch_type: pitch_type string (e.g. '4SM') for pitcher's
            highest-usage pitch.
        season_by_pitch: SEASON-window by_pitch_type dict for this batter, used
            for T1C (HR vs primary pitch). Falls back to L14's by_pitch_type if
            None (which is noisier).
        cfg: optional threshold dict (defaults to module cache / _DEFAULTS).

    Returns:
        (hits_count, detail_dict)
        hits_count: 0-3 — number of Tier 1 thresholds passed
        detail_dict: { 'barrel': {'value': X, 'passed': bool},
                       'xslg':   {'value': X, 'passed': bool},
                       'hr_vs_pitch': {'value': X, 'passed': bool, 'pitch_type': str} }
    """
    detail = {
        "barrel":      {"value": None, "passed": False},
        "xslg":        {"value": None, "passed": False},
        "hr_vs_pitch": {"value": None, "passed": False, "pitch_type": pitcher_primary_pitch_type},
    }

    # T1A: barrel% (requires L14 row)
    if batter_stats_l14:
        barrel = batter_stats_l14.get("barrel_pct")
        if barrel is not None:
            detail["barrel"]["value"] = float(barrel)
            if float(barrel) >= _num(cfg, "t1_hr_barrel_min"):
                detail["barrel"]["passed"] = True

        # T1B: xSLG (requires L14 row)
        xslg = batter_stats_l14.get("xslg")
        if xslg is not None:
            detail["xslg"]["value"] = float(xslg)
            if float(xslg) >= _num(cfg, "t1_hr_xslg_min"):
                detail["xslg"]["passed"] = True

    # T1C: HR rate vs primary pitch (use season by_pitch if available; falls
    # back to L14 by_pitch_type only if season not provided OR empty — L14
    # has very few PAs per pitch type so the rate is noisy, but better than
    # nothing).
    if pitcher_primary_pitch_type:
        by_pitch = season_by_pitch if season_by_pitch else None
        if not by_pitch:
            by_pitch = (batter_stats_l14 or {}).get("by_pitch_type") or {}
        pitch_row = (by_pitch or {}).get(pitcher_primary_pitch_type)
        if pitch_row and pitch_row.get("hr_pct") is not None:
            hr_vs = float(pitch_row["hr_pct"])
            detail["hr_vs_pitch"]["value"] = hr_vs
            if hr_vs >= _num(cfg, "t1_hr_hrvspitch_min"):
                detail["hr_vs_pitch"]["passed"] = True

    hits = sum(1 for v in detail.values() if v["passed"])
    return (hits, detail)


def evaluate_tier1_hrr(
    batter_stats_l14: Optional[dict],
    bvp_row: Optional[dict],
    cfg: Optional[dict] = None,
) -> tuple[int, dict]:
    """
    Evaluate HRR-market Tier 1 thresholds.

    Args:
        batter_stats_l14: batter_stats L14 row with hit_per_pa, xba
        bvp_row: row from bvp_history for this batter × this pitcher
            (avg, pa). None if no BvP history exists.
        cfg: optional threshold dict (defaults to module cache / _DEFAULTS).

    Returns:
        (hits_count, detail_dict)
    """
    detail = {
        "hit_rate": {"value": None, "passed": False},
        "xba":      {"value": None, "passed": False},
        "bvp":      {"value": None, "passed": False, "pa": None},
    }
    if not batter_stats_l14:
        # Even without L14, BvP might still apply — keep evaluating it below.
        pass
    else:
        # T1A: hit rate
        hr_per_pa_field = batter_stats_l14.get("hit_per_pa")
        if hr_per_pa_field is not None:
            detail["hit_rate"]["value"] = float(hr_per_pa_field)
            if float(hr_per_pa_field) >= _num(cfg, "t1_hrr_hitrate_min"):
                detail["hit_rate"]["passed"] = True

        # T1B: xBA
        xba = batter_stats_l14.get("xba")
        if xba is not None:
            detail["xba"]["value"] = float(xba)
            if float(xba) >= _num(cfg, "t1_hrr_xba_min"):
                detail["xba"]["passed"] = True

    # T1C: BvP positive
    if bvp_row:
        pa = bvp_row.get("pa") or 0
        avg = bvp_row.get("avg")
        detail["bvp"]["pa"] = int(pa)
        if avg is not None:
            detail["bvp"]["value"] = float(avg)
            if int(pa) >= int(_num(cfg, "t1_hrr_bvp_pa_min")) and \
               float(avg) >= _num(cfg, "t1_hrr_bvp_ba_min"):
                detail["bvp"]["passed"] = True

    hits = sum(1 for v in detail.values() if v["passed"])
    return (hits, detail)


# ─── Tier 2 evaluator ─────────────────────────────────────────────────────────

def evaluate_tier2(
    batter_stats_l14: Optional[dict],
    heat_tier: Optional[str],
    contact_score: Optional[float],
    bvp_row: Optional[dict],
    cfg: Optional[dict] = None,
) -> tuple[int, dict]:
    """
    Evaluate Tier 2 confirming signals. Applies to both HR and HRR markets.

    Args:
        batter_stats_l14: batter_stats L14 row (for hard_hit_pct)
        heat_tier: 'BLAZING'|'HOT'|'WARM'|'FLAT'|'COOL'|'COLD'|'FROZEN' or None
        contact_score: 0-100 composite from compute_contact_score, or None
        bvp_row: bvp_history row for this batter × this pitcher, or None
        cfg: optional threshold dict (defaults to module cache / _DEFAULTS).

    Returns:
        (hits_count, detail_dict) — hits 0-4
    """
    detail = {
        "heat":    {"value": heat_tier, "passed": False},
        "hh_pct":  {"value": None,      "passed": False},
        "contact": {"value": float(contact_score) if contact_score is not None else None,
                    "passed": False},
        "bvp":     {"value": None,      "passed": False, "pa": None},
    }

    # T2A: heat tier
    if heat_tier in _set(cfg, "t2_heat_tiers_qualifying"):
        detail["heat"]["passed"] = True

    # T2B: hard hit %
    if batter_stats_l14:
        hh = batter_stats_l14.get("hard_hit_pct")
        if hh is not None:
            detail["hh_pct"]["value"] = float(hh)
            if float(hh) >= _num(cfg, "t2_hh_pct_min"):
                detail["hh_pct"]["passed"] = True

    # T2C: contact score
    if contact_score is not None and float(contact_score) >= _num(cfg, "t2_contact_min"):
        detail["contact"]["passed"] = True

    # T2D: BvP positive
    if bvp_row:
        pa = bvp_row.get("pa") or 0
        avg = bvp_row.get("avg")
        detail["bvp"]["pa"] = int(pa)
        if avg is not None:
            detail["bvp"]["value"] = float(avg)
            if int(pa) >= int(_num(cfg, "t2_bvp_pa_min")) and \
               float(avg) >= _num(cfg, "t2_bvp_ba_min"):
                detail["bvp"]["passed"] = True

    hits = sum(1 for v in detail.values() if v["passed"])
    return (hits, detail)


# ─── Scoring ──────────────────────────────────────────────────────────────────

def score_candidate(tier1_hits: int, tier2_hits: int, cfg: Optional[dict] = None) -> Optional[float]:
    """
    Compute final tier_score for a candidate. Returns None if disqualified.

    Qualification paths:
      - 3-of-3 Tier 1            → base score_base_triple
      - 2-of-3 Tier 1 (standard) → base score_base_standard
      - 1-of-3 Tier 1 + ≥N T2    → base score_base_stowers (Stowers rule)
      - otherwise                → disqualified

    Tier 2 bonus: tier2_bonus_per_hit per T2 hit, capped at tier2_bonus_cap.
    """
    stowers_req = int(_num(cfg, "stowers_tier2_required"))
    if tier1_hits >= 3:
        base = _num(cfg, "score_base_triple")
    elif tier1_hits >= 2:
        base = _num(cfg, "score_base_standard")
    elif tier1_hits == 1 and tier2_hits >= stowers_req:
        base = _num(cfg, "score_base_stowers")
    else:
        return None   # disqualified

    bonus = min(tier2_hits * _num(cfg, "tier2_bonus_per_hit"), _num(cfg, "tier2_bonus_cap"))
    return round(base + bonus, 3)


def qualification_path(tier1_hits: int, tier2_hits: int, cfg: Optional[dict] = None) -> Optional[str]:
    """Returns 'triple' | 'standard' | 'stowers' | None (disqualified)."""
    stowers_req = int(_num(cfg, "stowers_tier2_required"))
    if tier1_hits >= 3:
        return "triple"
    if tier1_hits >= 2:
        return "standard"
    if tier1_hits == 1 and tier2_hits >= stowers_req:
        return "stowers"
    return None


# ─── Stake modifier ───────────────────────────────────────────────────────────

def stake_modifier_for(park_factor: float, weather_factor: float = 1.0,
                       cfg: Optional[dict] = None) -> float:
    """
    Combine park × weather into informational stake modifier, clamped to
    [stake_mod_floor, stake_mod_ceil] so we never recommend wild stake swings.
    """
    raw = float(park_factor or 1.0) * float(weather_factor or 1.0)
    floor = _num(cfg, "stake_mod_floor")
    ceil = _num(cfg, "stake_mod_ceil")
    return round(max(floor, min(ceil, raw)), 3)


# ─── Pitcher primary pitch helper ─────────────────────────────────────────────

def primary_pitch_type(pitcher_arsenal_rows: list) -> Optional[str]:
    """
    Pick the pitch type the pitcher throws most. Returns None if no arsenal.
    """
    if not pitcher_arsenal_rows:
        return None
    best = None
    best_usage = -1.0
    for row in pitcher_arsenal_rows:
        u = row.get("usage_pct")
        if u is not None and float(u) > best_usage:
            best_usage = float(u)
            best = row.get("pitch_type")
    return best


# ─── Tier 3 tiebreaker key ────────────────────────────────────────────────────

def tier3_key(candidate: dict) -> tuple:
    """
    Sort key for breaking ties among candidates with equal tier_score.
    Order (descending, higher-is-better): edge, ev_per_dollar, tier1_hits, tier2_hits.
    """
    return (
        candidate.get("edge") or -999,
        candidate.get("ev_per_dollar") or -999,
        candidate.get("tier1_hits") or 0,
        candidate.get("tier2_hits") or 0,
    )


# ════════════════════════════════════════════════════════════════════════════
# v2 patch layer — shared by pick_pod.py AND pick_cards.py
# ════════════════════════════════════════════════════════════════════════════
# Picker-agnostic functions for the v2 framework (catcher boost, environment,
# primary signal, near-miss boost, continuous confidence + tier letter, market
# context). Thresholds load once per run via load_thresholds()/configure(); the
# v2 functions below read them via _cfg_num (config-with-fallback: cfg value
# wins, else the documented default — resilience if model_thresholds is
# unreachable). DB-touching fetchers stay in the pickers (orchestration).

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
# of truth; seeding them separately would risk the pickers drifting from the
# projections they score. If compute_projections.py changes these, change them
# here too. (league_hr_per_barrel, by contrast, IS tunable → model_thresholds row.)
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
    HR only. For HRR, primary_signal is hit_per_pa per the v1 framework (see the
    pickers' enrichment).
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
