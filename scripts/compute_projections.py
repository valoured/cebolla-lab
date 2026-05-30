"""
compute_projections.py — Cebolla Lab projection model v0.3.0

NEW IN v0.3.0:
  - H+R+RBI (HRR) market — writes THREE rows per batter per game:
    h_r_rbi_1.5, h_r_rbi_2.5, h_r_rbi_3.5
  - Poisson model: λ_per_pa = p(hit) + p(R) + p(RBI) - overlap, then
    λ_game = λ_per_pa × E[PA from lineup spot].
  - L14 blend on R/RBI rates (catches role + lineup-context shifts).
  - Requires migration 15 (adds r_per_pa, rbi_per_pa to batter_stats) and
    pull_batter_counting_stats.py to backfill those fields.

Carried over from v0.2.0:
  - 1+ HITS market (`hits_yes`) — full pipeline parallel to HR Anytime
  - Writes `hr_anytime`, `hits_yes`, and now h_r_rbi_* projections
  - Bayesian shrinkage on batter rates per market

HR market formula (v0.4.0 — park moved to stake_modifier):
  shrunk_batter_hr_per_pa = (PA × obs + 200 × LEAGUE) / (PA + 200)
  shrunk_pitcher_hr_per_9 = (BF × obs + 80 × LEAGUE) / (BF + 80)
  projected_hr_per_pa = shrunk_batter × pitcher_factor × arsenal_adj_v2
  projected_anytime = 1 - (1 - per_pa)^expected_PAs
  stake_modifier = clamp(park × weather, 0.7, 1.3)  ← INFORMATIONAL, not used in projection

  arsenal_adj_v2 (2026-05-21):
    per-pitch ratio = (batter_hr_pct + pitcher_hr_allowed_pct) /
                      (batter_overall_hr_pct + LEAGUE_HR_PCT)
    weighted by pitcher usage, sample-size-ramped from 10 → 150 PA per pitch.
    Dynamic clamp by data confidence:
      HIGH   (covered PA ≥100 AND usage concentration ≥30) → [0.70, 1.30]
      MEDIUM (covered PA ≥50)                              → [0.80, 1.20]
      LOW    (default)                                     → [0.85, 1.15]

HITS market formula (v0.4.0 — park moved to stake_modifier):
  shrunk_batter_hit_per_pa = (PA × obs + 100 × LEAGUE) / (PA + 100)
  shrunk_pitcher_hit_per_pa = (BF × obs + 60 × LEAGUE) / (BF + 60)
  pitcher_baa_factor = clamp(shrunk_p_hit_per_pa / LEAGUE_HIT_PER_PA, [0.80, 1.25])
  projected_hit_per_pa = shrunk_batter × pitcher_baa_factor
  projected_1plus_hits = 1 - (1 - per_pa)^expected_PAs   (market='hits_yes')
  projected_2plus_hits = 1 - (1-per_pa)^PA - PA·per_pa·(1-per_pa)^(PA-1)
                         (market='hits_yes_1.5'; same per_pa + PA as 1+, so
                          P(2+) ≤ P(1+) by construction)
  stake_modifier = clamp(park_ba × weather, 0.7, 1.3)  ← INFORMATIONAL only

HRR market formula (v0.4.0 — park moved to stake_modifier):
  p_hit  = shrunk_batter_hit_per_pa × pitcher_baa_factor
  p_R    = season/L14 blended shrunk_r_per_pa (50/50 if L14 PA ≥ 30)
  p_RBI  = season/L14 blended shrunk_rbi_per_pa
  λ_per_pa = p_hit + p_R + p_RBI − 0.06 (overlap correction), clamped [0.05, 0.75]
  stake_modifier = clamp(park_ba × weather, 0.7, 1.3)  ← INFORMATIONAL only
  λ_game = λ_per_pa × E[PA from lineup spot]
  P(HRR ≥ X+1) = 1 − Σ_{k=0..X} (e^-λ × λ^k / k!)   for each line X.5
"""

import os
import sys
import math
import logging
from datetime import datetime, timezone, date, timedelta
from collections import defaultdict

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MODEL_VERSION = "v0.4.0"   # tier system: removed park/weather from projected_prob, moved to stake_modifier

# ──── HR market constants ────
LEAGUE_HR_PER_PA = 0.029
LEAGUE_HR_PER_9  = 1.15
BATTER_SHRINKAGE_K  = 200
PITCHER_SHRINKAGE_K = 80
ARSENAL_CAP_LO = 0.85
ARSENAL_CAP_HI = 1.15
ARSENAL_MIN_MULT = 0.5
# ── Arsenal v2 dynamic-clamp tiers (added 2026-05-21) ──
# When matchup signal has high data confidence (large sample + concentrated
# pitcher usage), allow a wider swing than the static [0.85, 1.15] cap.
# Tiers:
#   HIGH   — batter PA ≥100 vs covered pitches AND usage concentration ≥30 → ±30%
#   MEDIUM — batter PA ≥ 50 vs covered pitches                              → ±20%
#   LOW    — anything else                                                  → ±15%  (default)
ARSENAL_V2_HIGH_PA_THRESHOLD   = 100
ARSENAL_V2_MEDIUM_PA_THRESHOLD = 50
ARSENAL_V2_CONCENTRATION_THRESHOLD = 30.0   # HHI-like (sum of usage_pct^2/100)
ARSENAL_V2_HIGH_CAP_LO   = 0.70
ARSENAL_V2_HIGH_CAP_HI   = 1.30
ARSENAL_V2_MEDIUM_CAP_LO = 0.80
ARSENAL_V2_MEDIUM_CAP_HI = 1.20
# League average HR-allowed % per pitch (for the two-sided factor denominator).
# Same as LEAGUE_HR_PER_PA but expressed as a percentage to match how pitcher
# arsenal hr_pct rows are stored.
ARSENAL_V2_LEAGUE_HR_PCT = LEAGUE_HR_PER_PA * 100   # ≈ 2.9
PITCHER_CAP_LO = 0.75
PITCHER_CAP_HI = 1.40
PARK_CAP_LO    = 0.80
PARK_CAP_HI    = 1.20
PROJ_PER_PA_CAP = 0.08

# ──── Hits market constants ────
LEAGUE_HIT_PER_PA = 0.230                # MLB avg ~0.23 hits/PA (BABIP-adjusted)
BATTER_HITS_SHRINKAGE_K  = 100
PITCHER_HITS_SHRINKAGE_K = 60
PITCHER_BAA_CAP_LO = 0.80
PITCHER_BAA_CAP_HI = 1.25
PARK_BA_CAP_LO     = 0.90
PARK_BA_CAP_HI     = 1.10
PROJ_HIT_PER_PA_CAP = 0.40               # league best ~0.32; cap at 0.40

PA_BY_LINEUP_SPOT = {
    1: 4.55,  2: 4.45,  3: 4.35,  4: 4.25,  5: 4.15,
    6: 4.05,  7: 3.92,  8: 3.80,  9: 3.68,
}

# ──── HRR (H+R+RBI) market constants ────
# League-average per-PA rates from MLB Stats API aggregates.
# Hits per PA is already a constant above (LEAGUE_HIT_PER_PA = 0.230).
LEAGUE_R_PER_PA   = 0.115                # ~runs/PA league avg
LEAGUE_RBI_PER_PA = 0.110                # ~RBI/PA league avg
BATTER_R_SHRINKAGE_K   = 150             # noisier counting stat → less weight on small samples
BATTER_RBI_SHRINKAGE_K = 150

# Overlap correction. When we sum p(H) + p(R) + p(RBI) per PA we overcount
# cases where the same PA contributes to more than one bucket:
#   - A hit that scores = both Hit AND Run counted
#   - A hit that drives in = both Hit AND RBI counted
#   - A solo HR = Hit + Run + RBI (counted 3x for 1 event)
# Empirical estimate from MLB-wide play-by-play: ~0.06 events/PA double-count.
HRR_OVERLAP_PER_PA = 0.06

# Lines we project for HRR (each becomes its own projection row).
HRR_LINES = [1.5, 2.5, 3.5]

# Cap on combined per-PA event rate. League-leading batters top out around
# 0.55 (Judge tier); we cap at 0.75 to accommodate hot streaks without going silly.
HRR_LAMBDA_PER_PA_CAP = 0.75

# ──── Longshot filters ────
HR_LONGSHOT_THRESHOLD   = 2000           # HR market — filter at +2000+
HITS_LONGSHOT_THRESHOLD = 600            # Hits market — filter at +600+
HRR_LONGSHOT_THRESHOLD  = 800            # HRR — filter at +800+

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Shrinkage helpers
# ────────────────────────────────────────────────────────────────

def shrink_batter_hr_per_pa(observed: float, pa: int) -> float:
    if pa is None or pa <= 0:
        return LEAGUE_HR_PER_PA
    return (pa * observed + BATTER_SHRINKAGE_K * LEAGUE_HR_PER_PA) / (pa + BATTER_SHRINKAGE_K)


def shrink_pitcher_hr_per_9(observed: float, bf: int) -> float:
    if bf is None or bf <= 0:
        return LEAGUE_HR_PER_9
    return (bf * observed + PITCHER_SHRINKAGE_K * LEAGUE_HR_PER_9) / (bf + PITCHER_SHRINKAGE_K)


def shrink_batter_hit_per_pa(observed: float, pa: int) -> float:
    if pa is None or pa <= 0:
        return LEAGUE_HIT_PER_PA
    return (pa * observed + BATTER_HITS_SHRINKAGE_K * LEAGUE_HIT_PER_PA) / (pa + BATTER_HITS_SHRINKAGE_K)


def shrink_pitcher_hit_per_pa(observed: float, bf: int) -> float:
    if bf is None or bf <= 0:
        return LEAGUE_HIT_PER_PA
    return (bf * observed + PITCHER_HITS_SHRINKAGE_K * LEAGUE_HIT_PER_PA) / (bf + PITCHER_HITS_SHRINKAGE_K)


def shrink_batter_r_per_pa(observed: float, pa: int) -> float:
    """Bayesian shrinkage for runs-scored rate (HRR market)."""
    if pa is None or pa <= 0:
        return LEAGUE_R_PER_PA
    return (pa * observed + BATTER_R_SHRINKAGE_K * LEAGUE_R_PER_PA) / (pa + BATTER_R_SHRINKAGE_K)


def shrink_batter_rbi_per_pa(observed: float, pa: int) -> float:
    """Bayesian shrinkage for RBI rate (HRR market)."""
    if pa is None or pa <= 0:
        return LEAGUE_RBI_PER_PA
    return (pa * observed + BATTER_RBI_SHRINKAGE_K * LEAGUE_RBI_PER_PA) / (pa + BATTER_RBI_SHRINKAGE_K)


def poisson_tail(threshold: int, lam: float) -> float:
    """
    Return P(X >= threshold) for X ~ Poisson(lam).
    Used for HRR projections: P(H+R+RBI >= threshold).

    Computed as 1 - sum_{k=0..threshold-1} (e^-lam * lam^k / k!).
    Threshold is small (≤ 4) so direct summation is fine — no need for scipy.
    """
    if lam <= 0:
        return 0.0
    if threshold <= 0:
        return 1.0

    import math
    # CDF up to threshold-1 inclusive
    cdf = 0.0
    factorial = 1.0
    exp_neg_lam = math.exp(-lam)
    power = 1.0  # lam^k starts at lam^0 = 1
    for k in range(threshold):
        if k > 0:
            factorial *= k
            power *= lam
        cdf += exp_neg_lam * power / factorial
    return max(0.0, min(1.0, 1.0 - cdf))


def american_to_implied(odds: int | None) -> float | None:
    if odds is None:
        return None
    if odds >= 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def devig_anytime(american_odds: int | None, implied: float | None,
                   market: str = "hr_anytime") -> float | None:
    """
    Dynamic vig curve for single-sided Yes props.
    Different curve per market (HR vs Hits) because price ranges differ.
    Returns no-vig probability or None if outside trust range (longshot filter).
    """
    if american_odds is None or implied is None or implied <= 0 or implied >= 1:
        return None

    if market == "hits_yes":
        # Hits Yes props rarely longshot — most batters have >40% chance of 1+ hit
        if american_odds >= HITS_LONGSHOT_THRESHOLD:
            return None
        # Tighter, flatter curve
        if american_odds <= -150:
            vig = 0.04
        elif american_odds <= 100:
            vig = 0.05
        elif american_odds <= 300:
            vig = 0.06
        else:  # +300 to +600
            vig = 0.08
    else:
        # HR Anytime — wider price range, steeper longshot vig
        if american_odds >= HR_LONGSHOT_THRESHOLD:
            return None
        if american_odds <= 200:
            vig = 0.05
        elif american_odds <= 500:
            vig = 0.07
        elif american_odds <= 1000:
            vig = 0.10
        else:  # +1000 to +2000
            vig = 0.13

    return implied / (1 + vig)


def pitcher_factor_from_shrunk(shrunk_hr_per_9: float) -> float:
    raw = shrunk_hr_per_9 / LEAGUE_HR_PER_9
    return max(PITCHER_CAP_LO, min(PITCHER_CAP_HI, raw))


def pitcher_baa_factor_from_shrunk(shrunk_hit_per_pa: float) -> float:
    raw = shrunk_hit_per_pa / LEAGUE_HIT_PER_PA
    return max(PITCHER_BAA_CAP_LO, min(PITCHER_BAA_CAP_HI, raw))


def park_capped(p: float) -> float:
    return max(PARK_CAP_LO, min(PARK_CAP_HI, p))


def park_ba_capped(p: float) -> float:
    return max(PARK_BA_CAP_LO, min(PARK_BA_CAP_HI, p))


def compute_arsenal_adjustment(
    batter_by_pitch: dict | None,
    pitcher_arsenal: list,
    batter_overall_hr_pct: float,
) -> tuple[float, dict]:
    """
    v2 (2026-05-21) — pitcher × batter matchup multiplier on HR rate.

    What's new vs v1:
      1. TWO-SIDED FACTOR. v1 only used batter's HR rate per pitch:
             raw_mult = batter_hr_pct / batter_overall_hr_pct
         v2 also factors in the PITCHER's HR-allowed rate on that pitch:
             raw_mult = (batter_hr_pct + pitcher_hr_allowed_pct) /
                        (batter_overall_hr_pct + league_avg_hr_pct)
         A batter who mashes sliders facing a pitcher who gets crushed on
         sliders is meaningfully more dangerous than either signal alone.

      2. DYNAMIC CLAMP. v1 hard-capped at [0.85, 1.15] regardless of data
         quality. v2 widens the cap when matchup confidence is high (large
         sample of PAs vs the pitcher's actual pitches AND concentrated
         usage):
             HIGH confidence   → [0.70, 1.30]
             MEDIUM confidence → [0.80, 1.20]
             LOW confidence    → [0.85, 1.15]  (default — matches v1)

    Returns (multiplier, contributions_dict). The dict is for logging/audit
    — same shape as v1 with two added fields: 'two_sided_mult' (uncombined)
    and 'pitcher_hr_pct' (the pitcher side of the signal).
    """
    if not batter_by_pitch or not pitcher_arsenal or batter_overall_hr_pct <= 0:
        return (1.0, {})

    # Aggregate pitcher usage by pitch_type (rows can split across stances).
    usage_map = defaultdict(float)
    pitcher_hr_map = defaultdict(list)   # collect HR% across stance rows
    for a in pitcher_arsenal:
        pt = a.get("pitch_type")
        u = a.get("usage_pct") or 0
        if pt and u:
            usage_map[pt] += float(u)
            php = a.get("hr_pct")
            if php is not None:
                pitcher_hr_map[pt].append(float(php))
    total_usage = sum(usage_map.values())
    if total_usage <= 0:
        return (1.0, {})

    contributions = {}
    weighted_sum = 0.0
    total_weight = 0.0

    # Confidence accumulators — only count pitches the pitcher actually throws
    # (≥5% usage) AND where the batter has any data on that pitch.
    covered_pa_total = 0
    covered_usage_total = 0.0
    concentration = 0.0   # sum of (usage_pct^2 / 100) — HHI-like

    for pitch_label, usage_raw in usage_map.items():
        usage = usage_raw / total_usage    # normalized to sum to 1.0
        usage_pct_display = usage_raw       # original % (for concentration)

        # Pitcher's HR-allowed rate on this pitch (averaged if multiple
        # stance rows exist). Defaults to league avg if missing.
        php_samples = pitcher_hr_map.get(pitch_label, [])
        if php_samples:
            pitcher_hr_pct = sum(php_samples) / len(php_samples)
        else:
            pitcher_hr_pct = ARSENAL_V2_LEAGUE_HR_PCT

        batter_stat = batter_by_pitch.get(pitch_label)
        if not batter_stat:
            multiplier = 1.0
            two_sided_mult = 1.0
            sample_pa = 0
        else:
            b_hr_pct = batter_stat.get("hr_pct", 0) or 0
            sample_pa = batter_stat.get("pa", 0) or 0
            if sample_pa < 10:
                multiplier = 1.0
                two_sided_mult = 1.0
            else:
                # ─── Two-sided ratio (v2 core change) ───
                # Numerator: combined HR signal (batter side + pitcher side)
                # Denominator: league baseline + batter's overall (same combination)
                numerator   = b_hr_pct + pitcher_hr_pct
                denominator = batter_overall_hr_pct + ARSENAL_V2_LEAGUE_HR_PCT
                if denominator <= 0:
                    two_sided_mult = 1.0
                else:
                    two_sided_mult = numerator / denominator

                # Same global bounds on raw multiplier as v1 (prevents one
                # weird pitch from dominating before the per-pitch sample
                # weight kicks in).
                two_sided_mult = max(ARSENAL_MIN_MULT, min(2.0, two_sided_mult))

                # Per-pitch sample-size weight (linear ramp from 10 → 150 PAs).
                # Below 10 PA already returned 1.0 above.
                weight = min(1.0, max(0, (sample_pa - 10) / 140))
                multiplier = 1.0 + (two_sided_mult - 1.0) * weight

                # Only count toward confidence if we have meaningful batter data
                covered_pa_total += sample_pa
                covered_usage_total += usage_pct_display
                concentration += (usage_pct_display ** 2) / 100.0

        weighted_sum += multiplier * usage
        total_weight += usage
        contributions[pitch_label] = {
            "usage": round(usage, 3),
            "mult": round(multiplier, 3),
            "two_sided_mult": round(two_sided_mult, 3),
            "pa": sample_pa,
            "pitcher_hr_pct": round(pitcher_hr_pct, 2),
        }

    if total_weight <= 0:
        return (1.0, contributions)

    adj = weighted_sum / total_weight

    # ─── v2 dynamic clamp ───
    # Determine confidence tier from covered PA + usage concentration.
    # Only "covered" pitches (batter has ≥10 PA on them) contribute to PA total.
    if (covered_pa_total >= ARSENAL_V2_HIGH_PA_THRESHOLD
            and concentration >= ARSENAL_V2_CONCENTRATION_THRESHOLD):
        cap_lo, cap_hi = ARSENAL_V2_HIGH_CAP_LO, ARSENAL_V2_HIGH_CAP_HI
        confidence_tier = "HIGH"
    elif covered_pa_total >= ARSENAL_V2_MEDIUM_PA_THRESHOLD:
        cap_lo, cap_hi = ARSENAL_V2_MEDIUM_CAP_LO, ARSENAL_V2_MEDIUM_CAP_HI
        confidence_tier = "MEDIUM"
    else:
        cap_lo, cap_hi = ARSENAL_CAP_LO, ARSENAL_CAP_HI
        confidence_tier = "LOW"

    adj_capped = max(cap_lo, min(cap_hi, adj))

    # Stash confidence info on the contributions dict for audit/logging.
    contributions["_meta"] = {
        "covered_pa_total": covered_pa_total,
        "concentration": round(concentration, 2),
        "confidence_tier": confidence_tier,
        "cap_lo": cap_lo,
        "cap_hi": cap_hi,
        "adj_raw": round(adj, 3),
        "adj_capped": round(adj_capped, 3),
    }

    return (adj_capped, contributions)


def one_minus_pow_per_pa(rate_per_pa: float, expected_pas: float) -> float:
    """1 - (1 - rate_per_pa)^PAs. Used for both HR Anytime and 1+ Hits."""
    if rate_per_pa <= 0:
        return 0.0
    if rate_per_pa >= 1:
        return 1.0
    return 1 - math.pow(1 - rate_per_pa, expected_pas)


# Alias for back-compat readability
hr_anytime_from_per_pa = one_minus_pow_per_pa


def two_plus_from_per_pa(rate_per_pa: float, expected_pas: float) -> float:
    """
    P(2+ hits) under the SAME per-PA binomial-survival model used for 1+:
        P(2+) = 1 - (1-p)^n - n*p*(1-p)^(n-1)
    where p = rate_per_pa, n = expected_pas (fractional, from lineup spot).

    Uses identical p and n as one_minus_pow_per_pa (the 1+ form), so
    P(2+) <= P(1+) holds by construction (their difference is exactly the
    P(exactly 1) term, which is >= 0). Clamped to [0, 1] defensively — for
    the model's valid (p, n) range P(0)+P(1) < 1 always, but the clamp guards
    against any future cap change pushing it out of bounds.
    """
    if rate_per_pa <= 0 or expected_pas <= 0:
        return 0.0
    if rate_per_pa >= 1:
        return 1.0
    p0 = math.pow(1 - rate_per_pa, expected_pas)
    p1 = expected_pas * rate_per_pa * math.pow(1 - rate_per_pa, expected_pas - 1)
    return max(0.0, min(1.0, 1 - p0 - p1))


def edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "longshot_unrated"
    if edge >= 0.05:  return "strong_back"
    if edge >= 0.02:  return "lean_back"
    if edge >= -0.02: return "flat"
    if edge >= -0.05: return "lean_fade"
    return "strong_fade"


# ────────────────────────────────────────────────────────────────
# DB loaders
# ────────────────────────────────────────────────────────────────

def get_todays_games() -> list[dict]:
    today = date.today().isoformat()
    res = sb.table("games").select(
        "id, mlb_game_pk, game_time_utc, "
        "away_team_id, home_team_id, "
        "away_pitcher_id, home_pitcher_id, "
        "hr_factor_overall, hr_factor_lhb, hr_factor_rhb, status"
    ).eq("game_date", today).execute()
    return res.data


def _present_row_source(r: dict) -> str:
    """
    lineup_source for a row that EXISTS in today's lineups table (Finding C).
    'confirmed' only when it's real, complete MLB data; pull-written estimates
    pass their specific value through; legacy 'last_known' and partial 'mlb_api'
    postings degrade conservatively to 'estimated_last_known'.
    """
    src = r.get("source")
    if r.get("is_confirmed") and src == "mlb_api":
        return "confirmed"
    if src in ("estimated_rolling_7", "estimated_last_known"):
        return src
    return "estimated_last_known"


def get_lineups_for_game(game_id: int) -> list[dict]:
    """
    Return lineups for a game, predicting any team whose lineup isn't posted yet.

    pick_pod/pick_cards lock at ~3 AM ET, before MLB posts lineups. For any team
    missing today's lineup we call lineup_predict.predicted_lineup_for_projections
    (Option B: rolling-7 most-common + best-effort handedness layer, degrading to
    the single most-recent lineup on thin history).

    Every returned row carries `lineup_source` — the single provenance signal
    ('confirmed' | 'estimated_rolling_7' | 'estimated_last_known'); the old
    in-memory `is_estimated` flag is removed. Returns a list of lineup-row dicts.
    """
    from lineup_predict import predicted_lineup_for_projections, _pitcher_throws_map

    # First try: today's posted lineup. `source` drives provenance (Finding C).
    res = sb.table("lineups").select(
        "id, team_id, batting_order, position, bats, player_id, is_confirmed, source"
    ).eq("game_id", game_id).execute()
    rows = res.data or []
    for r in rows:
        r["lineup_source"] = _present_row_source(r)

    # Which teams need predicting, plus the opposing starter for handedness.
    game_res = sb.table("games").select(
        "away_team_id, home_team_id, away_pitcher_id, home_pitcher_id, game_date"
    ).eq("id", game_id).single().execute()
    game = game_res.data or {}
    needed_teams = {game.get("away_team_id"), game.get("home_team_id")} - {None}
    have_teams = {r["team_id"] for r in rows}
    missing_teams = needed_teams - have_teams

    if not missing_teams:
        return rows

    log.info("Lineup predict for game %d: %d/%d teams missing — Option B rolling-7",
             game_id, len(missing_teams), len(needed_teams))

    throws_map = _pitcher_throws_map(sb)   # one players query per cron run (memoized)
    slate = game.get("game_date")
    for team_id in missing_teams:
        opp_pid = (game.get("away_pitcher_id") if game.get("home_team_id") == team_id
                   else game.get("home_pitcher_id"))
        opp_throws = throws_map.get(opp_pid) if opp_pid else None

        predicted = predicted_lineup_for_projections(sb, team_id, opp_throws, slate)
        if not predicted:
            log.warning("  Team %d has no predictable lineup (insufficient history) — skipping",
                        team_id)
            continue
        rows.extend(predicted)
        log.info("  Team %d: %d predicted batters [%s]",
                 team_id, len(predicted), predicted[0]["lineup_source"])

    return rows


def get_batter_stats_map(batter_ids: list[int]) -> dict[int, dict]:
    if not batter_ids:
        return {}
    res = sb.table("batter_stats").select("*") \
        .in_("batter_id", batter_ids) \
        .eq("window_type", "season") \
        .eq("vs_hand", "A") \
        .execute()
    return {r["batter_id"]: r for r in res.data}


def get_batter_stats_l14_map(batter_ids: list[int]) -> dict[int, dict]:
    """L14 rolling-window stats (mirrors get_batter_stats_map, different window)."""
    if not batter_ids:
        return {}
    res = sb.table("batter_stats").select("*") \
        .in_("batter_id", batter_ids) \
        .eq("window_type", "l14") \
        .eq("vs_hand", "A") \
        .execute()
    return {r["batter_id"]: r for r in res.data}


def get_player_names(player_ids: list[int]) -> dict[int, str]:
    if not player_ids:
        return {}
    res = sb.table("players").select("id, name").in_("id", player_ids).execute()
    return {r["id"]: r["name"] for r in res.data}


def get_pitcher_arsenal(pitcher_id: int) -> list[dict]:
    if not pitcher_id:
        return []
    res = sb.table("pitcher_arsenals").select("*") \
        .eq("pitcher_id", pitcher_id) \
        .eq("window_type", "season") \
        .execute()
    return res.data


def get_pitcher_stats(pitcher_id: int) -> dict | None:
    """Read clean season pitching stats from pitcher_stats table."""
    if not pitcher_id:
        return None
    res = sb.table("pitcher_stats").select(
        "hr_per_9, hr_per_pa, hit_per_pa, hits_per_9, baa, "
        "batters_faced, innings_pitched, hr_allowed, hits_allowed"
    ).eq("pitcher_id", pitcher_id).eq("window_type", "season").execute()
    if not res.data:
        return None
    return res.data[0]


def get_current_odds(
    game_id: int, batter_ids: list[int], market: str,
    line: float | None = None,
) -> dict[int, dict]:
    """
    Fetch latest odds_snapshots rows for a (game, market) tuple.
    For markets with multiple lines (e.g. hits_yes has both 1+ and 2+),
    pass an explicit `line` to filter.
    """
    if not batter_ids:
        return {}
    q = sb.table("odds_snapshots").select("*") \
        .eq("game_id", game_id) \
        .eq("market", market) \
        .eq("book", "draftkings") \
        .eq("is_current", True) \
        .in_("player_id", batter_ids) \
        .order("snapshot_time", desc=True)
    if line is not None:
        q = q.eq("line", line)
    res = q.execute()
    out = {}
    for row in res.data:
        if row["player_id"] not in out:
            out[row["player_id"]] = row
    return out


def get_hrr_odds_map(
    game_id: int, batter_ids: list[int]
) -> dict[int, dict[float, dict]]:
    """
    Fetch HRR odds bucketed as {player_id: {line: odds_row}}.
    HRR ships multiple lines per batter (0.5/1.5/2.5/3.5/4.5) under a single
    market='h_r_rbi_yes'; we collect all of them in one query to avoid
    hammering the DB with 3 separate calls.
    """
    if not batter_ids:
        return {}
    res = sb.table("odds_snapshots").select("*") \
        .eq("game_id", game_id) \
        .eq("market", "h_r_rbi_yes") \
        .eq("book", "draftkings") \
        .eq("is_current", True) \
        .in_("player_id", batter_ids) \
        .order("snapshot_time", desc=True) \
        .execute()
    out: dict[int, dict[float, dict]] = {}
    for row in res.data:
        pid = row["player_id"]
        line = row.get("line")
        if line is None:
            continue
        line_f = float(line)
        bucket = out.setdefault(pid, {})
        # First occurrence wins (already ordered by snapshot_time desc)
        bucket.setdefault(line_f, row)
    return out


def get_park_ba_factor(home_team_id: int) -> float:
    """Fetch park_ba_factor for the home team (1.0 if unknown)."""
    if not home_team_id:
        return 1.0
    res = sb.table("teams").select("park_ba_factor") \
        .eq("id", home_team_id).execute()
    if not res.data or res.data[0].get("park_ba_factor") is None:
        return 1.0
    return float(res.data[0]["park_ba_factor"])


# ────────────────────────────────────────────────────────────────
# Projection pipeline
# ────────────────────────────────────────────────────────────────

def project_game(game: dict) -> list[dict]:
    lineups = get_lineups_for_game(game["id"])
    if not lineups:
        return []

    away_team_id = game["away_team_id"]
    home_team_id = game["home_team_id"]
    away_batters = [l for l in lineups if l["team_id"] == away_team_id]
    home_batters = [l for l in lineups if l["team_id"] == home_team_id]

    all_batter_ids = [l["player_id"] for l in lineups if l.get("player_id")]
    if not all_batter_ids:
        return []

    batter_stats_map = get_batter_stats_map(all_batter_ids)
    batter_stats_l14_map = get_batter_stats_l14_map(all_batter_ids)

    # Odds markets, fetched in parallel.
    # NOTE: odds_snapshots stores ALL hits ladders under market='hits_yes',
    # differentiated by line: 0.5 = 1+ Hits, 1.5 = 2+ Hits (also 2.5/3.5 exist).
    # We pull the 0.5 and 1.5 lines separately to price the 1+ and 2+ rows.
    hr_odds_map    = get_current_odds(game["id"], all_batter_ids, "hr_anytime_yes")
    hits_odds_map  = get_current_odds(game["id"], all_batter_ids, "hits_yes", line=0.5)
    hits2_odds_map = get_current_odds(game["id"], all_batter_ids, "hits_yes", line=1.5)
    hrr_odds_map   = get_hrr_odds_map(game["id"], all_batter_ids)

    # Pitcher data
    away_arsenal = get_pitcher_arsenal(game.get("away_pitcher_id"))
    home_arsenal = get_pitcher_arsenal(game.get("home_pitcher_id"))
    away_pstats  = get_pitcher_stats(game.get("away_pitcher_id"))
    home_pstats  = get_pitcher_stats(game.get("home_pitcher_id"))

    # Park BA factor — based on the HOME venue (where the game is played)
    park_ba = get_park_ba_factor(home_team_id)

    rows = []
    snapshot_time = datetime.now(timezone.utc).isoformat()

    # Away batters face HOME pitcher
    for batter in away_batters:
        bstats = batter_stats_map.get(batter["player_id"])
        bstats_l14 = batter_stats_l14_map.get(batter["player_id"])

        hr_row = _project_hr(
            batter, home_arsenal, home_pstats, bstats,
            hr_odds_map.get(batter["player_id"]),
            game.get("hr_factor_lhb"), game.get("hr_factor_rhb"),
            game.get("hr_factor_overall"), game["id"], snapshot_time,
        )
        if hr_row:
            rows.append(hr_row)

        hits_rows = _project_hits(
            batter, home_pstats, bstats,
            hits_odds_map.get(batter["player_id"]),
            hits2_odds_map.get(batter["player_id"]),
            park_ba, game["id"], snapshot_time,
        )
        rows.extend(hits_rows)

        hrr_rows = _project_hrr(
            batter, home_pstats, bstats, bstats_l14,
            hrr_odds_map.get(batter["player_id"], {}),
            park_ba, game["id"], snapshot_time,
        )
        rows.extend(hrr_rows)

    # Home batters face AWAY pitcher
    for batter in home_batters:
        bstats = batter_stats_map.get(batter["player_id"])
        bstats_l14 = batter_stats_l14_map.get(batter["player_id"])

        hr_row = _project_hr(
            batter, away_arsenal, away_pstats, bstats,
            hr_odds_map.get(batter["player_id"]),
            game.get("hr_factor_lhb"), game.get("hr_factor_rhb"),
            game.get("hr_factor_overall"), game["id"], snapshot_time,
        )
        if hr_row:
            rows.append(hr_row)

        hits_rows = _project_hits(
            batter, away_pstats, bstats,
            hits_odds_map.get(batter["player_id"]),
            hits2_odds_map.get(batter["player_id"]),
            park_ba, game["id"], snapshot_time,
        )
        rows.extend(hits_rows)

        hrr_rows = _project_hrr(
            batter, away_pstats, bstats, bstats_l14,
            hrr_odds_map.get(batter["player_id"], {}),
            park_ba, game["id"], snapshot_time,
        )
        rows.extend(hrr_rows)

    return rows


def _project_hr(
    batter: dict,
    opposing_arsenal: list[dict],
    opposing_pstats: dict | None,
    batter_stats: dict | None,
    odds_row: dict | None,
    park_factor_lhb: float | None,
    park_factor_rhb: float | None,
    park_factor_overall: float | None,
    game_id: int,
    snapshot_time: str,
) -> dict | None:
    """HR Anytime projection — one row per batter."""
    if not batter_stats:
        return None

    raw_hr_per_pa = batter_stats.get("hr_per_pa")
    pa = batter_stats.get("pa") or 0
    if raw_hr_per_pa is None:
        return None
    raw_hr_per_pa = float(raw_hr_per_pa)

    # ─── Bayesian shrinkage on batter ───
    base = shrink_batter_hr_per_pa(raw_hr_per_pa, pa)

    # ─── Park factor (HR, handedness-specific) ───
    bats = batter.get("bats") or "R"
    if bats == "L":
        park_raw = park_factor_lhb or park_factor_overall or 1.0
    elif bats == "S":
        park_raw = park_factor_overall or 1.0
    else:
        park_raw = park_factor_rhb or park_factor_overall or 1.0
    park = park_capped(float(park_raw))

    # ─── Pitcher factor from pitcher_stats ───
    if opposing_pstats and opposing_pstats.get("hr_per_9") is not None:
        bf = opposing_pstats.get("batters_faced") or 0
        shrunk_pitcher_hr9 = shrink_pitcher_hr_per_9(
            float(opposing_pstats["hr_per_9"]), bf
        )
    else:
        shrunk_pitcher_hr9 = LEAGUE_HR_PER_9
    p_factor = pitcher_factor_from_shrunk(shrunk_pitcher_hr9)

    # ─── Arsenal adjustment ───
    base_pct_for_arsenal = base * 100
    by_pitch = batter_stats.get("by_pitch_type") or {}
    arsenal_adj, _breakdown = compute_arsenal_adjustment(
        by_pitch, opposing_arsenal, base_pct_for_arsenal
    )

    # ─── Combine (NO park or weather — those are stake modifiers, NOT projection inputs) ───
    # Pure projection = Statcast base × pitcher factor × arsenal factor.
    # Park & weather are tracked separately and surfaced as a stake recommendation.
    projected_per_pa = base * p_factor * arsenal_adj
    projected_per_pa = max(0.001, min(PROJ_PER_PA_CAP, projected_per_pa))

    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)
    projected_prob = one_minus_pow_per_pa(projected_per_pa, expected_pas)

    # Stake modifier (informational only — used to show "favorable conditions
    # +X%" on the ticket. NOT used to bias which batter we pick).
    # Weather currently a no-op (= 1.0) until weather adjustment ships.
    weather_factor = 1.0
    stake_modifier = max(0.7, min(1.3, park * weather_factor))

    # ─── Edge ───
    edge = None
    no_vig = None
    best_american = None
    if odds_row:
        american = odds_row.get("american_odds")
        if american is not None:
            implied = american_to_implied(american)
            no_vig = devig_anytime(american, implied, market="hr_anytime")
            if no_vig is not None:
                edge = projected_prob - no_vig
            best_american = american

    return {
        "game_id": game_id,
        "player_id": batter["player_id"],
        "market": "hr_anytime",
        "model_version": MODEL_VERSION,
        "projected_prob": round(projected_prob, 4),
        "base_rate": round(base, 5),
        "pitcher_adj": round(p_factor, 3),
        "park_adj": round(park, 3),
        "weather_adj": 1.0,
        "arsenal_adj": round(arsenal_adj, 3),
        "stake_modifier": round(stake_modifier, 3),
        "best_book": "draftkings" if best_american is not None else None,
        "best_american_odds": best_american,
        "no_vig_prob": round(no_vig, 4) if no_vig is not None else None,
        "edge": round(edge, 4) if edge is not None else None,
        "edge_bucket": edge_bucket(edge) if best_american is not None else None,
        "created_at": snapshot_time,
    }


def _project_hits(
    batter: dict,
    opposing_pstats: dict | None,
    batter_stats: dict | None,
    odds_row_1plus: dict | None,
    odds_row_2plus: dict | None,
    park_ba_factor: float,
    game_id: int,
    snapshot_time: str,
) -> list[dict]:
    """
    Hits projections — returns BOTH the 1+ row (market='hits_yes') and the
    2+ row (market='hits_yes_1.5'), computed from the same per-PA rate and
    expected-PA count. Returns [] if the required batter hit rate is missing.

    The 1+ and 2+ rows are intentionally derived from identical (p, n) inputs
    so P(2+) <= P(1+) always holds (see two_plus_from_per_pa). Each row is
    priced against its own DK line: 1+ uses line=0.5 odds, 2+ uses line=1.5.
    """
    if not batter_stats:
        return []

    raw_hit_per_pa = batter_stats.get("hit_per_pa")
    pa = batter_stats.get("pa") or 0
    if raw_hit_per_pa is None:
        return []
    raw_hit_per_pa = float(raw_hit_per_pa)

    # ─── Bayesian shrinkage on batter (hits) ───
    base = shrink_batter_hit_per_pa(raw_hit_per_pa, pa)

    # ─── Park BA factor ───
    park = park_ba_capped(float(park_ba_factor))

    # ─── Pitcher BAA factor ───
    if opposing_pstats and opposing_pstats.get("hit_per_pa") is not None:
        bf = opposing_pstats.get("batters_faced") or 0
        shrunk_pitcher_hit = shrink_pitcher_hit_per_pa(
            float(opposing_pstats["hit_per_pa"]), bf
        )
    else:
        shrunk_pitcher_hit = LEAGUE_HIT_PER_PA
    p_factor = pitcher_baa_factor_from_shrunk(shrunk_pitcher_hit)

    # ─── Combine (NO park or weather — those are stake modifiers, NOT projection inputs) ───
    # Pure projection = Statcast base × pitcher BAA factor.
    # Park & weather are tracked separately and surfaced as a stake recommendation.
    projected_per_pa = base * p_factor
    projected_per_pa = max(0.001, min(PROJ_HIT_PER_PA_CAP, projected_per_pa))

    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)
    prob_1plus = one_minus_pow_per_pa(projected_per_pa, expected_pas)
    prob_2plus = two_plus_from_per_pa(projected_per_pa, expected_pas)

    # Stake modifier (informational only) — shared by both lines.
    weather_factor = 1.0
    stake_modifier = max(0.7, min(1.3, park * weather_factor))

    def _hits_row(market: str, projected_prob: float, odds_row: dict | None) -> dict:
        # Both hits lines use the "hits_yes" devig curve (tighter/flatter than HR).
        edge = None
        no_vig = None
        best_american = None
        if odds_row:
            american = odds_row.get("american_odds")
            if american is not None:
                implied = american_to_implied(american)
                no_vig = devig_anytime(american, implied, market="hits_yes")
                if no_vig is not None:
                    edge = projected_prob - no_vig
                best_american = american
        return {
            "game_id": game_id,
            "player_id": batter["player_id"],
            "market": market,
            "model_version": MODEL_VERSION,
            "projected_prob": round(projected_prob, 4),
            "base_rate": round(base, 5),
            "pitcher_adj": round(p_factor, 3),
            "park_adj": round(park, 3),
            "weather_adj": 1.0,
            "arsenal_adj": 1.0,                          # n/a for hits
            "stake_modifier": round(stake_modifier, 3),
            "best_book": "draftkings" if best_american is not None else None,
            "best_american_odds": best_american,
            "no_vig_prob": round(no_vig, 4) if no_vig is not None else None,
            "edge": round(edge, 4) if edge is not None else None,
            "edge_bucket": edge_bucket(edge) if best_american is not None else None,
            "created_at": snapshot_time,
        }

    return [
        _hits_row("hits_yes",     prob_1plus, odds_row_1plus),
        _hits_row("hits_yes_1.5", prob_2plus, odds_row_2plus),
    ]


# Back-compat shim (older code paths)
_project_single = _project_hr


def _project_hrr(
    batter: dict,
    opposing_pstats: dict | None,
    batter_stats: dict | None,
    batter_stats_l14: dict | None,
    odds_by_line: dict[float, dict],
    park_ba: float,
    game_id: int,
    snapshot_time: str,
) -> list[dict]:
    """
    H+R+RBI (HRR) projection — produces ONE row per HRR_LINES entry.

    Model:
      1. Per-PA event rates for hits, runs, RBIs (Bayesian-shrunk).
      2. Optional L14 blend: if L14 PA ≥ 30, blend 50/50 with season for
         R/RBI rates (capturing recent role + lineup-context shifts).
         Hits per PA is unchanged — pull_savant already provides a good rate.
      3. Pitcher BAA adjustment applies to hits component only.
      4. Park BA factor applies to hits component only.
      5. λ_per_pa = p_hit_adj + p_r + p_rbi - HRR_OVERLAP_PER_PA  (capped).
      6. λ_game = λ_per_pa × E[PA from lineup spot].
      7. P(HRR ≥ X+1) = poisson_tail(X+1, λ_game) for each line X.5.

    Returns [] if any required stat is missing (no fake projections).
    """
    if not batter_stats:
        return []

    # ─── Pull rates (season) ───
    raw_hit = batter_stats.get("hit_per_pa")
    raw_r = batter_stats.get("r_per_pa")
    raw_rbi = batter_stats.get("rbi_per_pa")
    pa_season = batter_stats.get("pa") or 0

    if raw_hit is None or raw_r is None or raw_rbi is None:
        # Missing the new counting-stat fields — wait for pull_batter_counting_stats
        # to backfill. Don't fabricate.
        return []

    raw_hit = float(raw_hit)
    raw_r = float(raw_r)
    raw_rbi = float(raw_rbi)

    # ─── Bayesian shrinkage on each rate (season) ───
    p_hit_season = shrink_batter_hit_per_pa(raw_hit, pa_season)
    p_r_season = shrink_batter_r_per_pa(raw_r, pa_season)
    p_rbi_season = shrink_batter_rbi_per_pa(raw_rbi, pa_season)

    # ─── Optional L14 blend for R/RBI (catches role + lineup shifts) ───
    if batter_stats_l14 and batter_stats_l14.get("r_per_pa") is not None \
       and batter_stats_l14.get("rbi_per_pa") is not None:
        pa_l14 = batter_stats_l14.get("pa") or 0
        if pa_l14 >= 30:
            r_l14 = shrink_batter_r_per_pa(float(batter_stats_l14["r_per_pa"]), pa_l14)
            rbi_l14 = shrink_batter_rbi_per_pa(float(batter_stats_l14["rbi_per_pa"]), pa_l14)
            p_r = 0.5 * p_r_season + 0.5 * r_l14
            p_rbi = 0.5 * p_rbi_season + 0.5 * rbi_l14
        else:
            p_r, p_rbi = p_r_season, p_rbi_season
    else:
        p_r, p_rbi = p_r_season, p_rbi_season

    # ─── Pitcher BAA factor (affects hits component only) ───
    if opposing_pstats and opposing_pstats.get("hit_per_pa") is not None:
        bf = opposing_pstats.get("batters_faced") or 0
        shrunk_pitcher_hit = shrink_pitcher_hit_per_pa(
            float(opposing_pstats["hit_per_pa"]), bf
        )
    else:
        shrunk_pitcher_hit = LEAGUE_HIT_PER_PA
    p_factor = pitcher_baa_factor_from_shrunk(shrunk_pitcher_hit)

    park = park_ba_capped(float(park_ba)) if park_ba else 1.0

    # ─── Combined per-PA rate (NO park or weather here — those are stake modifiers) ───
    # Hits absorbs pitcher adjustment. Park goes to stake_modifier instead.
    # Runs/RBI are downstream events already implicitly correlated with team's
    # offensive context (captured through L14 blend) — no separate factor needed v1.
    p_hit_adj = p_hit_season * p_factor
    p_hit_adj = max(0.001, min(PROJ_HIT_PER_PA_CAP, p_hit_adj))

    lambda_per_pa = p_hit_adj + p_r + p_rbi - HRR_OVERLAP_PER_PA
    lambda_per_pa = max(0.05, min(HRR_LAMBDA_PER_PA_CAP, lambda_per_pa))

    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)
    lambda_game = lambda_per_pa * expected_pas

    # Stake modifier (informational only)
    weather_factor = 1.0
    stake_modifier = max(0.7, min(1.3, park * weather_factor))

    # ─── One row per line ───
    rows = []
    for line in HRR_LINES:
        # Line X.5 = "at least X+1". e.g. 1.5 → P(HRR >= 2).
        threshold = int(line + 0.5)  # 1.5 → 2, 2.5 → 3, 3.5 → 4
        projected_prob = poisson_tail(threshold, lambda_game)

        odds_row = odds_by_line.get(line)
        edge = None
        no_vig = None
        best_american = None
        if odds_row:
            american = odds_row.get("american_odds")
            if american is not None:
                implied = american_to_implied(american)
                # Use the existing devig curve. HRR isn't explicitly modeled
                # but the "anytime" curve (used for hits/hr) is a reasonable
                # approximation for typical HRR pricing (-200 to +400 range).
                no_vig = devig_anytime(american, implied, market="hits_yes")
                if no_vig is not None:
                    edge = projected_prob - no_vig
                best_american = american

        # Encode line in market string (consistent with existing convention
        # where hr_anytime_yes/hits_yes are line-implicit single rows).
        # HRR needs 3 lines so we differentiate via the market string.
        market_str = f"h_r_rbi_{line}"

        rows.append({
            "game_id": game_id,
            "player_id": batter["player_id"],
            "market": market_str,
            "model_version": MODEL_VERSION,
            "projected_prob": round(projected_prob, 4),
            "base_rate": round(lambda_per_pa, 5),    # store the per-PA rate for transparency
            "pitcher_adj": round(p_factor, 3),
            "park_adj": round(park, 3),
            "weather_adj": 1.0,
            "arsenal_adj": 1.0,                       # n/a for HRR
            "stake_modifier": round(stake_modifier, 3),
            "best_book": "draftkings" if best_american is not None else None,
            "best_american_odds": best_american,
            "no_vig_prob": round(no_vig, 4) if no_vig is not None else None,
            "edge": round(edge, 4) if edge is not None else None,
            "edge_bucket": edge_bucket(edge) if best_american is not None else None,
            "lineup_source": batter.get("lineup_source"),
            "created_at": snapshot_time,
        })

    return rows


# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────

def main():
    log.info("🧅 Cebolla Lab — projection compute starting (model %s)", MODEL_VERSION)
    log.info("   HR  K=(%d, %d)  pitcher_cap=[%.2f,%.2f]  longshot=+%d",
             BATTER_SHRINKAGE_K, PITCHER_SHRINKAGE_K,
             PITCHER_CAP_LO, PITCHER_CAP_HI, HR_LONGSHOT_THRESHOLD)
    log.info("   HIT K=(%d, %d)  baa_cap=[%.2f,%.2f]  longshot=+%d",
             BATTER_HITS_SHRINKAGE_K, PITCHER_HITS_SHRINKAGE_K,
             PITCHER_BAA_CAP_LO, PITCHER_BAA_CAP_HI, HITS_LONGSHOT_THRESHOLD)
    log.info("   HRR K=(R:%d, RBI:%d)  lines=%s  λ_cap=%.2f  longshot=+%d",
             BATTER_R_SHRINKAGE_K, BATTER_RBI_SHRINKAGE_K,
             HRR_LINES, HRR_LAMBDA_PER_PA_CAP, HRR_LONGSHOT_THRESHOLD)

    games = get_todays_games()
    if not games:
        log.info("No games today.")
        return
    log.info("Found %d games", len(games))

    all_rows = []
    for i, g in enumerate(games, 1):
        if (g.get("status") or "").lower() in {"final", "game over"}:
            continue
        log.info("[%d/%d] game %d", i, len(games), g["id"])
        rows = project_game(g)
        log.info("   %d projections", len(rows))
        all_rows.extend(rows)

    if not all_rows:
        log.warning("Zero projection rows generated.")
        return

    log.info("Total: %d projection rows. Writing to DB…", len(all_rows))

    written = 0
    for i in range(0, len(all_rows), 200):
        chunk = all_rows[i:i+200]
        sb.table("projections").upsert(
            chunk,
            on_conflict="game_id,player_id,market,model_version",
        ).execute()
        written += len(chunk)
    log.info("✓ Wrote %d", written)

    # ─── Diagnostics (per market) ───
    def _diag(market_label: str, market_rows: list[dict]):
        rated = [r for r in market_rows if r.get("edge") is not None]
        longshots = [r for r in market_rows if r.get("edge_bucket") == "longshot_unrated"]
        if not rated:
            log.info("─── %s: 0 rated rows ───", market_label)
            return

        sorted_by_edge = sorted(rated, key=lambda r: r["edge"], reverse=True)
        edge_player_ids = list({r["player_id"] for r in sorted_by_edge[:5] + sorted_by_edge[-5:]})
        name_map = get_player_names(edge_player_ids)

        log.info("─── %s — TOP 5 BACK ───", market_label)
        for r in sorted_by_edge[:5]:
            nm = name_map.get(r["player_id"], f"#{r['player_id']}")
            log.info("  %-25s odds=%+d  proj=%.1f%%  no_vig=%.1f%%  edge=%+.2f%%  [base=%.4f p=%.2f park=%.2f]",
                     nm[:25], r["best_american_odds"] or 0,
                     r["projected_prob"]*100,
                     (r["no_vig_prob"] or 0)*100,
                     r["edge"]*100,
                     r["base_rate"], r["pitcher_adj"], r["park_adj"])

        log.info("─── %s — BOTTOM 5 FADE ───", market_label)
        for r in sorted_by_edge[-5:]:
            nm = name_map.get(r["player_id"], f"#{r['player_id']}")
            log.info("  %-25s odds=%+d  proj=%.1f%%  no_vig=%.1f%%  edge=%+.2f%%  [base=%.4f p=%.2f park=%.2f]",
                     nm[:25], r["best_american_odds"] or 0,
                     r["projected_prob"]*100,
                     (r["no_vig_prob"] or 0)*100,
                     r["edge"]*100,
                     r["base_rate"], r["pitcher_adj"], r["park_adj"])

        edges_pct = [r["edge"] * 100 for r in sorted_by_edge]
        log.info("─── %s — distribution ───", market_label)
        log.info("  Min: %+.2f%%   Median: %+.2f%%   Max: %+.2f%%   Rated: %d   Longshots filtered: %d",
                 min(edges_pct),
                 sorted(edges_pct)[len(edges_pct)//2],
                 max(edges_pct),
                 len(edges_pct),
                 len(longshots))
        strong_back = sum(1 for e in edges_pct if e >= 5)
        lean_back   = sum(1 for e in edges_pct if 2 <= e < 5)
        flat        = sum(1 for e in edges_pct if -2 <= e < 2)
        lean_fade   = sum(1 for e in edges_pct if -5 <= e < -2)
        strong_fade = sum(1 for e in edges_pct if e < -5)
        log.info("  strong_back(≥+5%%)=%d  lean_back=%d  flat(±2%%)=%d  lean_fade=%d  strong_fade(≤-5%%)=%d",
                 strong_back, lean_back, flat, lean_fade, strong_fade)

    hr_rows    = [r for r in all_rows if r["market"] == "hr_anytime"]
    hits_rows  = [r for r in all_rows if r["market"] == "hits_yes"]
    hits2_rows = [r for r in all_rows if r["market"] == "hits_yes_1.5"]
    _diag("HR ANYTIME", hr_rows)
    _diag("1+ HITS",    hits_rows)
    _diag("2+ HITS",    hits2_rows)

    log.info("🧅 Projection compute complete")


if __name__ == "__main__":
    main()
