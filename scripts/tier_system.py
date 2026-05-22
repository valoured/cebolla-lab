"""
tier_system.py — shared tier evaluator used by pick_pod.py and pick_cards.py.

Encapsulates the tier-based qualification framework that replaces the old
multiplicative combined_score math (edge × contact × heat).

FRAMEWORK
═════════
Tier 1 (dominant gates):
  HR market:
    T1A: barrel% L14 ≥ 12
    T1B: xSLG L14 ≥ 0.600
    T1C: HR rate vs pitcher's primary pitch ≥ 8%
  HRR market:
    T1A: hit rate L14 ≥ 0.30 (hit_per_pa)
    T1B: xBA L14 ≥ 0.280
    T1C: BvP positive (career BA ≥ .280 vs this pitcher, min 8 PA)

Tier 2 (confirming signals — capped +0.15 bonus):
  T2A: heat tier ≥ HOT (per batter_trends.combined_tier)
  T2B: HH% L14 ≥ 45
  T2C: contact score ≥ 70
  T2D: BvP positive (same definition as T1C-HRR — gives BvP double weight in HRR)

Tier 3 (tiebreakers — used after tier_score):
  T3A: platoon advantage (LHB vs RHP / RHB vs LHP)
  T3B: edge (projected_prob - no_vig)
  T3C: ev_per_dollar
  T3D: CLV signal (future, once we have rolling CLV data)

Tier 4 (informational stake modifier — NOT pick selection):
  Park × weather → stake_modifier, displayed as "Conditions: favorable +X%"

SCORING
═══════
A batter qualifies if:
  (a) standard path: hits ≥2 Tier 1 thresholds, OR
  (b) Stowers rule:  hits exactly 1 Tier 1 threshold AND ≥3 Tier 2 thresholds

Base score by qualification path:
  3-of-3 Tier 1 (full triple)       → 1.10
  2-of-3 Tier 1 (standard)          → 1.00
  Stowers rule (1-of-3 + 3+ Tier 2) → 0.85

Tier 2 bonus on top of base: +0.05 per hit, capped +0.15.

Final tier_score range: [0.85, 1.25].

Selection: filter to qualifiers, rank by tier_score, break ties with Tier 3
(prefer higher edge, then higher ev_per_dollar).

If no qualifiers: publish nothing (this is the conviction signal).
"""

from typing import Optional


# ─── Tier 1 thresholds ────────────────────────────────────────────────────────

T1_HR_BARREL_MIN   = 12.0      # barrel% (stored as 0-100 in batter_stats)
T1_HR_XSLG_MIN     = 0.600
T1_HR_HRVSPITCH_MIN = 8.0      # hr_pct (0-100) on pitcher's primary pitch

T1_HRR_HITRATE_MIN = 0.30      # hit_per_pa (0-1)
T1_HRR_XBA_MIN     = 0.280
T1_HRR_BVP_BA_MIN  = 0.280     # career BA vs this pitcher
T1_HRR_BVP_PA_MIN  = 8         # minimum sample size for BvP

# ─── Tier 2 thresholds ────────────────────────────────────────────────────────

T2_HEAT_TIERS_QUALIFYING = {"HOT", "BLAZING"}
T2_HH_PCT_MIN     = 45.0       # hard_hit_pct (0-100)
T2_CONTACT_MIN    = 70.0       # contact_score (0-100)
T2_BVP_BA_MIN     = 0.280
T2_BVP_PA_MIN     = 8

# ─── Scoring constants ────────────────────────────────────────────────────────

SCORE_BASE_TRIPLE  = 1.10      # 3-of-3 Tier 1
SCORE_BASE_STANDARD = 1.00     # 2-of-3 Tier 1
SCORE_BASE_STOWERS = 0.85      # 1-of-3 Tier 1 + ≥3-of-4 Tier 2

TIER2_BONUS_PER_HIT = 0.05
TIER2_BONUS_CAP     = 0.15

STOWERS_TIER2_REQUIRED = 3     # need this many T2 hits to use Stowers rule

# ─── Tier 4 stake modifier ────────────────────────────────────────────────────

STAKE_MOD_FLOOR = 0.7
STAKE_MOD_CEIL  = 1.3


# ─── Tier 1 evaluators ────────────────────────────────────────────────────────

def evaluate_tier1_hr(
    batter_stats_l14: Optional[dict],
    pitcher_primary_pitch_type: Optional[str],
    season_by_pitch: Optional[dict] = None,
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
            if float(barrel) >= T1_HR_BARREL_MIN:
                detail["barrel"]["passed"] = True

        # T1B: xSLG (requires L14 row)
        xslg = batter_stats_l14.get("xslg")
        if xslg is not None:
            detail["xslg"]["value"] = float(xslg)
            if float(xslg) >= T1_HR_XSLG_MIN:
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
            if hr_vs >= T1_HR_HRVSPITCH_MIN:
                detail["hr_vs_pitch"]["passed"] = True

    hits = sum(1 for v in detail.values() if v["passed"])
    return (hits, detail)


def evaluate_tier1_hrr(
    batter_stats_l14: Optional[dict],
    bvp_row: Optional[dict],
) -> tuple[int, dict]:
    """
    Evaluate HRR-market Tier 1 thresholds.

    Args:
        batter_stats_l14: batter_stats L14 row with hit_per_pa, xba
        bvp_row: row from bvp_history for this batter × this pitcher
            (avg, pa). None if no BvP history exists.

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
            if float(hr_per_pa_field) >= T1_HRR_HITRATE_MIN:
                detail["hit_rate"]["passed"] = True

        # T1B: xBA
        xba = batter_stats_l14.get("xba")
        if xba is not None:
            detail["xba"]["value"] = float(xba)
            if float(xba) >= T1_HRR_XBA_MIN:
                detail["xba"]["passed"] = True

    # T1C: BvP positive
    if bvp_row:
        pa = bvp_row.get("pa") or 0
        avg = bvp_row.get("avg")
        detail["bvp"]["pa"] = int(pa)
        if avg is not None:
            detail["bvp"]["value"] = float(avg)
            if int(pa) >= T1_HRR_BVP_PA_MIN and float(avg) >= T1_HRR_BVP_BA_MIN:
                detail["bvp"]["passed"] = True

    hits = sum(1 for v in detail.values() if v["passed"])
    return (hits, detail)


# ─── Tier 2 evaluator ─────────────────────────────────────────────────────────

def evaluate_tier2(
    batter_stats_l14: Optional[dict],
    heat_tier: Optional[str],
    contact_score: Optional[float],
    bvp_row: Optional[dict],
) -> tuple[int, dict]:
    """
    Evaluate Tier 2 confirming signals. Applies to both HR and HRR markets.

    Args:
        batter_stats_l14: batter_stats L14 row (for hard_hit_pct)
        heat_tier: 'BLAZING'|'HOT'|'WARM'|'FLAT'|'COOL'|'COLD'|'FROZEN' or None
        contact_score: 0-100 composite from compute_contact_score, or None
        bvp_row: bvp_history row for this batter × this pitcher, or None

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
    if heat_tier in T2_HEAT_TIERS_QUALIFYING:
        detail["heat"]["passed"] = True

    # T2B: hard hit %
    if batter_stats_l14:
        hh = batter_stats_l14.get("hard_hit_pct")
        if hh is not None:
            detail["hh_pct"]["value"] = float(hh)
            if float(hh) >= T2_HH_PCT_MIN:
                detail["hh_pct"]["passed"] = True

    # T2C: contact score
    if contact_score is not None and float(contact_score) >= T2_CONTACT_MIN:
        detail["contact"]["passed"] = True

    # T2D: BvP positive
    if bvp_row:
        pa = bvp_row.get("pa") or 0
        avg = bvp_row.get("avg")
        detail["bvp"]["pa"] = int(pa)
        if avg is not None:
            detail["bvp"]["value"] = float(avg)
            if int(pa) >= T2_BVP_PA_MIN and float(avg) >= T2_BVP_BA_MIN:
                detail["bvp"]["passed"] = True

    hits = sum(1 for v in detail.values() if v["passed"])
    return (hits, detail)


# ─── Scoring ──────────────────────────────────────────────────────────────────

def score_candidate(tier1_hits: int, tier2_hits: int) -> Optional[float]:
    """
    Compute final tier_score for a candidate.

    Returns None if the candidate doesn't qualify (publish nothing rule).

    Qualification paths:
      - 3-of-3 Tier 1            → base 1.10
      - 2-of-3 Tier 1 (standard) → base 1.00
      - 1-of-3 Tier 1 + ≥3 T2    → base 0.85 (Stowers rule)
      - otherwise                → disqualified

    Tier 2 bonus: +0.05 per T2 hit, capped at +0.15. Applied on top of all
    base scores including Stowers (a 1+3T2 maxed Stowers = 0.85 + 0.15 = 1.00,
    tying a vanilla 2-of-3 — Tier 3 tiebreakers will sort it out).
    """
    # Determine qualification path
    if tier1_hits >= 3:
        base = SCORE_BASE_TRIPLE
    elif tier1_hits >= 2:
        base = SCORE_BASE_STANDARD
    elif tier1_hits == 1 and tier2_hits >= STOWERS_TIER2_REQUIRED:
        base = SCORE_BASE_STOWERS
    else:
        return None   # disqualified

    bonus = min(tier2_hits * TIER2_BONUS_PER_HIT, TIER2_BONUS_CAP)
    return round(base + bonus, 3)


def qualification_path(tier1_hits: int, tier2_hits: int) -> Optional[str]:
    """Returns 'triple' | 'standard' | 'stowers' | None (disqualified)."""
    if tier1_hits >= 3:
        return "triple"
    if tier1_hits >= 2:
        return "standard"
    if tier1_hits == 1 and tier2_hits >= STOWERS_TIER2_REQUIRED:
        return "stowers"
    return None


# ─── Stake modifier ───────────────────────────────────────────────────────────

def stake_modifier_for(park_factor: float, weather_factor: float = 1.0) -> float:
    """
    Combine park × weather into informational stake modifier.
    Clamped to [0.7, 1.3] so we never recommend wild stake swings.
    """
    raw = float(park_factor or 1.0) * float(weather_factor or 1.0)
    return round(max(STAKE_MOD_FLOOR, min(STAKE_MOD_CEIL, raw)), 3)


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

    Order (descending priority, all higher-is-better):
      1. edge          — model's projected vs market gap
      2. ev_per_dollar — payout-weighted EV
      3. tier1_hits    — prefer more T1 confirmations
      4. tier2_hits    — prefer more T2 confirmations

    Platoon advantage and CLV signal not yet wired (future).
    """
    return (
        candidate.get("edge") or -999,
        candidate.get("ev_per_dollar") or -999,
        candidate.get("tier1_hits") or 0,
        candidate.get("tier2_hits") or 0,
    )
