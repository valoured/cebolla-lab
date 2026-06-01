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

def legacy_primary_pitch_type(pitcher_arsenal_rows: list) -> Optional[str]:
    """
    PRE-Phase-2 behaviour: highest-usage pitch across BOTH stances, no usage
    gate. Retained ONLY for A/B forensics (phase1_metadata.matchup_diagnostics
    .primary_pitch_old_argmax) so the 14-day audit can attribute signal changes
    to the stance fix. Not used in the live signal path.
    """
    best = None
    best_usage = -1.0
    for row in (pitcher_arsenal_rows or []):
        u = row.get("usage_pct")
        if u is not None and float(u) > best_usage:
            best_usage = float(u)
            best = row.get("pitch_type")
    return best


def primary_pitch_type(
    pitcher_arsenal_rows: list,
    bats: Optional[str] = None,
    pitcher_throws: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> tuple[Optional[str], Optional[float], str]:
    """
    Stance-aware pitcher primary pitch. Returns (pitch_type, usage_pct, reason)
    with reason ∈ {'ok', 'no_arsenal', 'no_stance_rows', 'gated'}.

    usage_pct in pitcher_arsenals is computed PER STANCE (denominator = pitches
    vs that stance), so a batter only sees the pitcher's mix vs his own stance.
    Effective stance:
      bats 'L'/'R'                          → that stance
      bats 'S' (switch)                     → opposite of pitcher_throws
                                              (S vs RHP → 'L', S vs LHP → 'R')
      bats unknown, or 'S' w/ unknown throws → stance-agnostic argmax across all
                                              rows (legacy graceful-degrade so a
                                              missing handedness never zeroes the
                                              signal).

    Gate: if the top pitch's usage_pct < primary_pitch_usage_min (default 25%),
    the pitcher has no meaningful "primary" (true pitch-mix arm) → (None, usage,
    'gated'); the signal then falls back to its other components.
    """
    rows = pitcher_arsenal_rows or []
    if not rows:
        return (None, None, "no_arsenal")

    usage_gate = _cfg_num(cfg, "primary_pitch_usage_min", 25.0)

    eff = None
    if bats in ("L", "R"):
        eff = bats
    elif bats == "S" and pitcher_throws in ("L", "R"):
        eff = "L" if pitcher_throws == "R" else "R"

    stance_rows = []
    if eff is not None:
        stance_rows = [r for r in rows
                       if r.get("vs_stance") == eff and r.get("usage_pct") is not None]
    # Fall back to stance-agnostic when stance is unknown OR the pitcher has no
    # rows vs this stance (sparse one-sided sample) — better an approximate
    # primary than a zeroed signal.
    if not stance_rows:
        stance_rows = [r for r in rows if r.get("usage_pct") is not None]
    if not stance_rows:
        return (None, None, "no_stance_rows")

    best = max(stance_rows, key=lambda r: float(r["usage_pct"]))
    usage = float(best["usage_pct"])
    if usage < usage_gate:
        return (None, usage, "gated")
    return (best.get("pitch_type"), usage, "ok")


def compute_matchup_boost(
    by_pitch_season: Optional[dict],
    pitcher_primary_pitch_type: Optional[str],
    cfg: Optional[dict] = None,
) -> tuple[float, dict]:
    """
    Phase 2 — asymmetric (boost-only) multiplier for the recent_power_form arm,
    rewarding a batter whose barrel rate vs the pitcher's primary-pitch FAMILY
    clears that family's league barrel baseline:

        excess = clamp(brl_pct_vs_family − _LEAGUE_BRL_BY_FAMILY[family], 0, cap_pp)
        boost  = 1.0 + _MATCH_BOOST_K * excess          # floor 1.0 → never dampens

    brl_pct_vs_family is the PA-weighted mean of by_pitch_season[*].brl_pct over
    labels in the primary's family. Gate: family-summed PA ≥ primary_pitch_type_
    pa_min (default 20) AND a weighted brl_pct is computable; otherwise boost is
    1.0 (no effect). brl_pct is PERCENT (0-100), so baselines/cap are pp units.

    Returns (boost, detail) where detail carries the forensics fields:
      {match_boost, family_pa, batter_brl_pct_vs_family, league_brl_baseline}.
    NOTE: detail['match_boost'] mirrors the returned boost (the multiplier this
    matchup would apply); the picker overrides it to 1.0 in phase1_metadata when
    there is no power-form arm for it to actually multiply.
    """
    family = pitch_family_for(pitcher_primary_pitch_type)
    detail = {
        "match_boost": 1.0,
        "family_pa": None,
        "batter_brl_pct_vs_family": None,
        "league_brl_baseline": _LEAGUE_BRL_BY_FAMILY.get(family) if family else None,
    }
    if not family or not by_pitch_season:
        return (1.0, detail)

    pa_min = _cfg_num(cfg, "primary_pitch_type_pa_min", 20)
    family_pa = 0
    wsum = 0.0
    wtot = 0
    for label, e in by_pitch_season.items():
        if not isinstance(e, dict) or pitch_family_for(label) != family:
            continue
        try:
            pa = int(e.get("pa") or 0)
        except (TypeError, ValueError):
            continue
        family_pa += pa
        br = e.get("brl_pct")
        if br is not None and pa > 0:
            try:
                wsum += float(br) * pa
                wtot += pa
            except (TypeError, ValueError):
                pass
    detail["family_pa"] = family_pa
    brl = (wsum / wtot) if wtot > 0 else None
    detail["batter_brl_pct_vs_family"] = round(brl, 2) if brl is not None else None

    baseline = _LEAGUE_BRL_BY_FAMILY.get(family)
    if family_pa < pa_min or brl is None or baseline is None:
        return (1.0, detail)

    excess = max(0.0, min(_MATCH_BOOST_CAP_PP, brl - baseline))
    boost = round(1.0 + _MATCH_BOOST_K * excess, 5)
    detail["match_boost"] = boost
    return (boost, detail)


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

# Coarse pitch-type → family map. Both batter_stats.by_pitch_type (pull_savant.py)
# and pitcher_arsenals.pitch_type (pull_arsenals.py) write CANONICAL labels —
# raw Statcast codes (FF/FA/FT/FC/ST) are normalized at ingest. Canonical labels
# observed in production data (audit 2026-05-28, 535 batters with non-null
# by_pitch_type): 4SM SI SL CH CT SW CU FS KC SV (KN absent — no current MLB
# knuckleballer). Raw Statcast codes are retained as belt-and-suspenders against
# an ingest-side mapping regression.
_PITCH_FAMILY = {
    # Fastballs
    "4SM": "fastball",  # 4-seam     (528 batters)
    "SI":  "fastball",  # sinker     (449 batters)
    "CT":  "fastball",  # cutter     (391 batters)  ← was missing pre-Phase 1
    "FF":  "fastball",  # raw 4-seam fallback
    "FA":  "fastball",  # raw 4-seam fallback
    "FT":  "fastball",  # raw 2-seam fallback
    "FC":  "fastball",  # raw cutter fallback
    # Breaking
    "SL":  "breaking",  # slider     (441 batters)
    "SW":  "breaking",  # sweeper    (368 batters)  ← was missing pre-Phase 1
    "CU":  "breaking",  # curve      (325 batters)
    "KC":  "breaking",  # knuckle curve (79 batters)
    "SV":  "breaking",  # slurve      (2 batters)
    "ST":  "breaking",  # raw sweeper fallback
    "SC":  "breaking",  # raw screwball fallback (no MLB use)
    # Offspeed
    "CH":  "offspeed",  # change     (427 batters)
    "FS":  "offspeed",  # splitter   (223 batters)
    "KN":  "offspeed",  # knuckle    (0 batters this season; map for safety)
    "FO":  "offspeed",  # raw forkball fallback
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


# ════════════════════════════════════════════════════════════════════════════
# Phase 1 — matchup-first eligibility + primary signal + EV screen
# ════════════════════════════════════════════════════════════════════════════
# Replaces the v1 tier-1/tier-2/Stowers gating + v2 confidence_score derivation
# with: two-gate eligibility (A: opportunity, B: matchup exception), a
# primary_signal in ~[0,1] taken as the max of three matchup-anchored components,
# and an EV demote screen. Heat is removed from the picker. The v1/v2 functions
# above (evaluate_tier1_*, evaluate_tier2, score_candidate, calculate_confidence,
# apply_catcher_boost, etc.) are LEFT IN PLACE so Phase 1 can be reverted by
# reverting the pick_pod/pick_cards changes alone — no rollback of this file.
#
# Thresholds live in model_thresholds (seeded by sql/28). Every function below
# uses _cfg_num for cfg→default fallback, matching the v2 patch layer pattern.

# Stake-tier ordering best→worst, used by suggested_stake_tier_for() to bump
# one step worse on an EV "drop" / "warn_drop".
_PHASE1_STAKE_TIER_ORDER = ["lock", "safe", "risky", "lottery", "donation"]

# ─── Pitcher-batter matchup boost (Phase 2) ───────────────────────────────────
# Asymmetric (boost-only) multiplier on the recent_power_form arm of
# primary_signal_v3, rewarding a batter whose barrel rate vs the pitcher's
# primary-pitch FAMILY clears that family's league barrel baseline. Hard-coded
# constants this pass (NOT model_thresholds rows yet) per the Phase 2 plan.
#
# UNITS: brl_pct is stored as PERCENT (0-100) in batter_stats.by_pitch_type, so
# the baselines and cap are in PERCENTAGE-POINT units and k scales pp→multiplier.
# Family baselines are the per-family p75 of barrel-vs-primary-family from the
# Phase 1 slate audit (2026-06-01): FB 14.6, BB 13.4, OS 6.6 (offspeed's
# distribution is tight so its p75 sits near its mean). Anchoring at p75 — not
# the family mean — reserves the boost for roughly the top quartile of hitters
# per family, so only genuine elites-in-matchup are rewarded.
_LEAGUE_BRL_BY_FAMILY = {"fastball": 14.6, "breaking": 13.4, "offspeed": 6.6}
_MATCH_BOOST_K = 0.05          # multiplier per percentage-point of excess barrel
_MATCH_BOOST_CAP_PP = 6.0      # cap on excess (pp) → max boost 1.0 + 0.05*6 = 1.30


def pitch_family_for(pitch_type: Optional[str]) -> Optional[str]:
    """Public wrapper around _PITCH_FAMILY. Returns 'fastball'|'breaking'|'offspeed' or None."""
    if not pitch_type:
        return None
    return _PITCH_FAMILY.get(pitch_type)


def evaluate_eligibility(
    bstats_season: Optional[dict],
    by_pitch_season: Optional[dict],
    pitcher_primary_pitch_type: Optional[str],
    bvp_row: Optional[dict],
    cfg: Optional[dict] = None,
) -> tuple[bool, Optional[str], dict]:
    """
    Phase 1 two-gate eligibility.

    HARD EXCLUSION  season_pa < eligibility_season_pa_hard_min → (False, None, {hard_excluded: True})
    GATE A (opp.)   season_pa >= gate_a_season_pa_min
                    AND family-summed pitch_type_pa (across by_pitch_season
                        labels whose pitch_family_for(...) == primary family)
                        >= gate_a_pitch_type_pa_min
    GATE B (match.) bvp.ab >= gate_b_bvp_ab_min
                    AND (bvp.hr >= gate_b_bvp_hr_min
                         OR (bvp.avg >= gate_b_bvp_avg_min
                             AND bvp.hr >= gate_b_bvp_hr_alt_min))
                    AND (season barrel_pct >= gate_b_barrel_pct_min
                         OR season xslg >= gate_b_xslg_min)

    Returns (passed, gate, detail). `gate` ∈ {"A","B",None}; "A" wins ties
    (short-circuit; B only checked when A fails).
    """
    detail = {
        "season_pa": None,
        "pitch_type_pa": None,
        "bvp_ab": None,
        "bvp_hr": None,
        "bvp_avg": None,
        "season_barrel_pct": None,
        "season_xslg": None,
        "pitcher_primary_pitch_type": pitcher_primary_pitch_type,
        "pitcher_primary_family": pitch_family_for(pitcher_primary_pitch_type),
        "hard_excluded": False,
    }

    hard_min = _cfg_num(cfg, "eligibility_season_pa_hard_min", 50)
    season_pa = None
    if bstats_season and bstats_season.get("pa") is not None:
        try:
            season_pa = int(bstats_season["pa"])
        except (TypeError, ValueError):
            season_pa = None
    detail["season_pa"] = season_pa

    if season_pa is None or season_pa < hard_min:
        detail["hard_excluded"] = True
        return (False, None, detail)

    # ── Gate A — opportunity ────────────────────────────────────────────
    gate_a_pa_min = _cfg_num(cfg, "eligibility_gate_a_season_pa_min", 120)
    gate_a_pt_min = _cfg_num(cfg, "eligibility_gate_a_pitch_type_pa_min", 20)
    primary_family = detail["pitcher_primary_family"]

    pvp = by_pitch_season or {}
    family_pa = 0
    if primary_family:
        # Sum PA across labels in pvp whose family matches. Iterate pvp keys
        # (not _PITCH_FAMILY keys) so we never double-count canonical + raw
        # fallback entries — and so labels missing from the family map are
        # silently excluded rather than raising.
        for label, entry in pvp.items():
            if pitch_family_for(label) != primary_family:
                continue
            if not isinstance(entry, dict):
                continue
            try:
                family_pa += int(entry.get("pa") or 0)
            except (TypeError, ValueError):
                continue
    detail["pitch_type_pa"] = family_pa

    if season_pa >= gate_a_pa_min and family_pa >= gate_a_pt_min:
        return (True, "A", detail)

    # ── Gate B — matchup exception ──────────────────────────────────────
    bvp_ab_min     = _cfg_num(cfg, "eligibility_gate_b_bvp_ab_min", 8)
    bvp_hr_min     = _cfg_num(cfg, "eligibility_gate_b_bvp_hr_min", 2)
    bvp_avg_min    = _cfg_num(cfg, "eligibility_gate_b_bvp_avg_min", 0.300)
    bvp_hr_alt_min = _cfg_num(cfg, "eligibility_gate_b_bvp_hr_alt_min", 1)
    brl_min        = _cfg_num(cfg, "eligibility_gate_b_barrel_pct_min", 8.0)
    xslg_min       = _cfg_num(cfg, "eligibility_gate_b_xslg_min", 0.430)

    bvp_ab = bvp_hr = None
    bvp_avg = None
    if bvp_row:
        try:
            bvp_ab = int(bvp_row.get("ab") or 0)
        except (TypeError, ValueError):
            bvp_ab = None
        try:
            bvp_hr = int(bvp_row.get("hr") or 0)
        except (TypeError, ValueError):
            bvp_hr = None
        v = bvp_row.get("avg")
        if v is not None:
            try:
                bvp_avg = float(v)
            except (TypeError, ValueError):
                bvp_avg = None
    detail["bvp_ab"] = bvp_ab
    detail["bvp_hr"] = bvp_hr
    detail["bvp_avg"] = bvp_avg

    season_brl = season_xslg = None
    if bstats_season:
        v = bstats_season.get("barrel_pct")
        if v is not None:
            try:
                season_brl = float(v)
            except (TypeError, ValueError):
                pass
        v = bstats_season.get("xslg")
        if v is not None:
            try:
                season_xslg = float(v)
            except (TypeError, ValueError):
                pass
    detail["season_barrel_pct"] = season_brl
    detail["season_xslg"] = season_xslg

    bvp_sample_ok = bvp_ab is not None and bvp_ab >= bvp_ab_min
    bvp_perf_ok = False
    if bvp_sample_ok:
        if bvp_hr is not None and bvp_hr >= bvp_hr_min:
            bvp_perf_ok = True
        elif (bvp_avg is not None and bvp_avg >= bvp_avg_min
              and bvp_hr is not None and bvp_hr >= bvp_hr_alt_min):
            bvp_perf_ok = True
    power_ok = (
        (season_brl is not None and season_brl >= brl_min)
        or (season_xslg is not None and season_xslg >= xslg_min)
    )

    if bvp_sample_ok and bvp_perf_ok and power_ok:
        return (True, "B", detail)

    return (False, None, detail)


def compute_recency_dampener(
    player_id,
    game_log_by_player: Optional[dict],
    cfg: Optional[dict] = None,
) -> float:
    """
    Recency dampener in [floor, 1.0], applied to the recent_power_form component
    of primary_signal_v3. Compares the batter's ACTUAL slugging over his last 3
    games vs his last 7 games (from batter_game_log total_bases / ab):

        slg_last3 = Σ total_bases(last 3 games) / Σ ab(last 3 games)
        slg_last7 = Σ total_bases(last 7 games) / Σ ab(last 7 games)
        dampener  = clamp(slg_last3 / slg_last7, floor, 1.0)

    A hitter whose most-recent 3 games are colder than his 7-game baseline gets
    scaled DOWN. The 1.0 ceiling means a still-hot hitter is never scaled UP, so
    this can only suppress — it patches the stale-L7-xSLG problem where a 3-game
    barrage keeps the signal pinned high after the bat has gone cold. Unlike
    L7 xSLG (balls-in-play only, blind to strikeouts), actual SLG counts Ks via
    the AB denominator, so a strikeout-heavy slump is captured.

    `game_log_by_player` is {player_id: [game rows]} pre-sorted MOST-RECENT FIRST,
    each row carrying total_bases + ab. Edge cases all return 1.0 (no dampening):
      - fewer than 3 recent games (call-up / injury return / sparse log)
      - last-3 AB == 0 (no contact data to dampen with)
      - last-7 SLG == 0 / None (no total bases in window; picker won't pick anyway)
    The floor (default 0.4) prevents over-correction on a noisy 3-game sample.
    """
    floor = _cfg_num(cfg, "recency_dampener_floor", 0.4)
    ceil  = _cfg_num(cfg, "recency_dampener_ceiling", 1.0)
    rows = (game_log_by_player or {}).get(player_id) or []
    if len(rows) < 3:
        return 1.0

    def _slg(games):
        tb = sum(int(g.get("total_bases") or 0) for g in games)
        ab = sum(int(g.get("ab") or 0) for g in games)
        return (tb / ab) if ab > 0 else None

    slg3 = _slg(rows[:3])
    slg7 = _slg(rows[:7])           # rows[:7] takes all if the batter has 3-6 games
    if slg3 is None:                # last-3 AB == 0 → nothing to dampen with
        return 1.0
    if not slg7:                    # None or 0.0 → no baseline ratio possible
        return 1.0
    return round(max(floor, min(ceil, slg3 / slg7)), 4)


def compute_primary_signal_v3(
    bvp_row: Optional[dict],
    by_pitch_season: Optional[dict],
    pitcher_primary_pitch_type: Optional[str],
    l7_stats: Optional[dict],
    l14_stats: Optional[dict],
    cfg: Optional[dict] = None,
    recency_dampener: float = 1.0,
) -> tuple[float, Optional[str], dict]:
    """
    Phase 1 ranking signal — max of three components. Returns
    (signal, source, boost_detail); boost_detail is the Phase 2
    compute_matchup_boost() forensics dict (match_boost reported as 1.0 when no
    power-form arm existed for it to multiply).

      observed_vs_pitcher    = bvp.hr / bvp.ab            if bvp.ab >= primary_bvp_ab_min
        source = "bvp_observed"

      observed_vs_pitch_type = by_pitch[primary].hr_pct / 100
        Reliability gate: FAMILY-SUMMED PA across by_pitch_season entries whose
        pitch_family_for(label) == family-of-primary >= primary_pitch_type_pa_min.
        Value uses the SPECIFIC primary pitch's hr_pct (NOT family-averaged) —
        family-summed PA only gates whether we trust the sample.
        Returns 0.0 contribution (no candidate added) if the SPECIFIC pitch is
        missing from by_pitch — we don't synthesize from family averages.
        source = "pitch_type_observed"

      recent_power_form      = (l7.xslg / primary_l7_xslg_divisor) * recency_dampener * match_boost
        match_boost (Phase 2, >= 1.0) from compute_matchup_boost — rewards a
        batter who barrels the pitcher's primary-pitch family above its league
        baseline. Boost-only (never < 1.0), applied to THIS arm only.
        If l7.pa >= primary_l7_pa_min and l7.xslg present → source "l7_power_form".
        Else fall back to l14.xslg / primary_l7_xslg_divisor (any L14 PA, just
        needs xslg present) → source "l14_power_form".
        recency_dampener (default 1.0 = no-op) is the [0.4,1.0] factor from
        compute_recency_dampener — applied BEFORE the max() so a cooling bat can
        be knocked out of winning the signal. Callers without game-log data pass
        the 1.0 default and behave exactly as before.

    All three unavailable → (0.0, None, boost_detail). Final value clamped to [0,1].
    """
    divisor   = _cfg_num(cfg, "primary_l7_xslg_divisor", 2.0)
    bvp_ab_min = _cfg_num(cfg, "primary_bvp_ab_min", 8)
    pt_pa_min  = _cfg_num(cfg, "primary_pitch_type_pa_min", 20)
    l7_pa_min  = _cfg_num(cfg, "primary_l7_pa_min", 10)
    damp = float(recency_dampener) if recency_dampener is not None else 1.0

    # Phase 2 matchup boost — asymmetric multiplier applied ONLY to the
    # recent_power_form arm (not BvP, not pitch_type_observed), before the max()
    # and the final [0,1] clamp. boost is 1.0 unless the batter clears the
    # family-barrel gate, so a bad matchup never dampens.
    boost, boost_detail = compute_matchup_boost(
        by_pitch_season, pitcher_primary_pitch_type, cfg
    )
    power_form_added = False

    candidates: list[tuple[float, str]] = []

    # ── observed_vs_pitcher ─────────────────────────────────────────────
    if bvp_row:
        try:
            ab = int(bvp_row.get("ab") or 0)
            hr = int(bvp_row.get("hr") or 0)
        except (TypeError, ValueError):
            ab = hr = 0
        if ab >= bvp_ab_min and ab > 0:
            candidates.append((hr / ab, "bvp_observed"))

    # ── observed_vs_pitch_type ──────────────────────────────────────────
    pvp = by_pitch_season or {}
    if pitcher_primary_pitch_type and pvp:
        entry = pvp.get(pitcher_primary_pitch_type)
        family = pitch_family_for(pitcher_primary_pitch_type)
        if entry and isinstance(entry, dict) and family is not None:
            # Family-summed PA reliability gate
            family_pa = 0
            for label, sub in pvp.items():
                if pitch_family_for(label) != family or not isinstance(sub, dict):
                    continue
                try:
                    family_pa += int(sub.get("pa") or 0)
                except (TypeError, ValueError):
                    continue
            if family_pa >= pt_pa_min and entry.get("hr_pct") is not None:
                try:
                    hr_pct = float(entry["hr_pct"])  # stored 0-100
                    candidates.append((hr_pct / 100.0, "pitch_type_observed"))
                except (TypeError, ValueError):
                    pass

    # ── recent_power_form (boost applied here, before max + clamp) ───────
    used_l7 = False
    if l7_stats:
        l7_pa = l7_stats.get("pa")
        l7_xslg = l7_stats.get("xslg")
        if l7_pa is not None and l7_xslg is not None:
            try:
                if int(l7_pa) >= l7_pa_min and divisor:
                    candidates.append((float(l7_xslg) / divisor * damp * boost, "l7_power_form"))
                    used_l7 = True
                    power_form_added = True
            except (TypeError, ValueError):
                pass
    if not used_l7 and l14_stats:
        l14_xslg = l14_stats.get("xslg")
        if l14_xslg is not None and divisor:
            try:
                candidates.append((float(l14_xslg) / divisor * damp * boost, "l14_power_form"))
                power_form_added = True
            except (TypeError, ValueError):
                pass

    # The boost only multiplies the power-form arm; if no power-form arm exists
    # there was nothing to boost — report 1.0 in the forensics so the audit
    # doesn't credit a boost that never touched the signal.
    if not power_form_added:
        boost_detail["match_boost"] = 1.0

    if not candidates:
        return (0.0, None, boost_detail)
    value, source = max(candidates, key=lambda x: x[0])
    # Clamp to [0, 1] at the single computation site. Components are nominally
    # in [0, 1] but recent_power_form (l7.xslg / 2.0) can exceed 1.0 on an
    # extreme hot streak (xSLG > 2.0 → component > 1.0). The signal is
    # dual-written into confidence_score (pods/card_legs/cards), which carries
    # a CHECK [0, 1] constraint — an unclamped value violates it and crashes
    # both pickers. Clamp the winning max (not each candidate) so the
    # primary_signal_source label still reflects which component actually won.
    value = min(1.0, max(0.0, value))
    return (round(value, 5), source, boost_detail)


def apply_ev_screen(edge: Optional[float], cfg: Optional[dict] = None) -> tuple[str, bool]:
    """
    Phase 1 EV demote — returns (action, warning_flag).
      action ∈ {"full","drop","warn_drop","disqualify"}
      warning_flag True iff action == "warn_drop"

    edge >= ev_edge_full_floor   (default 0.03) → "full"
    edge >= ev_edge_drop_floor   (default 0.0)  → "drop"
    edge >= ev_edge_warn_floor   (default -0.10)→ "warn_drop"
    else / None                                 → "disqualify"
    """
    if edge is None:
        return ("disqualify", False)
    try:
        e = float(edge)
    except (TypeError, ValueError):
        return ("disqualify", False)
    if e >= _cfg_num(cfg, "ev_edge_full_floor", 0.03):
        return ("full", False)
    if e >= _cfg_num(cfg, "ev_edge_drop_floor", 0.0):
        return ("drop", False)
    if e >= _cfg_num(cfg, "ev_edge_warn_floor", -0.10):
        return ("warn_drop", True)
    return ("disqualify", False)


def suggested_stake_tier_for(
    primary_signal: float,
    ev_action: str,
    cfg: Optional[dict] = None,
) -> str:
    """
    Phase 1 advisory stake tier (display-only — not applied to stake yet).

      primary_signal >= stake_tier_lock_min    (0.65) → "lock"
                     >= stake_tier_safe_min    (0.50) → "safe"
                     >= stake_tier_risky_min   (0.30) → "risky"
                     >= stake_tier_lottery_min (0.15) → "lottery"
                                                 else → "donation"

    If ev_action in {"drop","warn_drop"}, bump one step worse along
    _PHASE1_STAKE_TIER_ORDER (lock→safe→risky→lottery→donation; donation
    is the floor — can't bump further).
    """
    try:
        s = float(primary_signal or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    if s >= _cfg_num(cfg, "stake_tier_lock_min", 0.65):
        base = "lock"
    elif s >= _cfg_num(cfg, "stake_tier_safe_min", 0.50):
        base = "safe"
    elif s >= _cfg_num(cfg, "stake_tier_risky_min", 0.30):
        base = "risky"
    elif s >= _cfg_num(cfg, "stake_tier_lottery_min", 0.15):
        base = "lottery"
    else:
        base = "donation"

    if ev_action in ("drop", "warn_drop"):
        try:
            idx = _PHASE1_STAKE_TIER_ORDER.index(base)
        except ValueError:
            return base
        bumped = min(idx + 1, len(_PHASE1_STAKE_TIER_ORDER) - 1)
        return _PHASE1_STAKE_TIER_ORDER[bumped]
    return base
