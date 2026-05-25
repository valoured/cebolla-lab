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
