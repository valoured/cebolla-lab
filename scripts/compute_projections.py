"""
compute_projections.py — Cebolla Lab Phase 4 projection model.

For every (batter, market, game) combo where we have:
  - a confirmed or projected lineup
  - batter Statcast stats (season + by-pitch-type)
  - pitcher arsenal (split by stance)
  - current DK odds
  - game weather + park factors

Compute a projected probability for HR (Anytime Home Run), then devig the
DK line via the Power method to get a no-vig fair probability, then
edge = projected - no_vig.

Math:
  projected_HR_per_PA =
    base_rate                              # batter season HR/PA
    × pitcher_factor                       # pitcher's HR/9 vs league avg
    × park_factor_by_handedness            # park HR factor for batter's side
    × weather_factor                       # encoded in game.hr_factor / team.park_factor
    × arsenal_adjustment                   # capped [0.7, 1.3]

  projected_HR_anytime = 1 - (1 - HR_per_PA) ^ expected_PAs

  edge = projected_HR_anytime - no_vig_prob_from_DK

Writes to the `projections` table with model_version stamp.
Runs hourly during slate window via GitHub Actions.

MODEL_VERSION = 'v0.1.0'
"""

import os
import sys
import math
import logging
from datetime import datetime, timezone, date
from collections import defaultdict

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MODEL_VERSION = "v0.1.0"

# League-wide averages — could move to a db table later
LEAGUE_HR_PER_PA = 0.029       # ~2.9% league HR/PA (recent MLB average)
LEAGUE_HR_PER_9  = 1.15        # league average HR/9

# Expected PAs per lineup spot (based on multi-year averages)
PA_BY_LINEUP_SPOT = {
    1: 4.55,  2: 4.45,  3: 4.35,  4: 4.25,  5: 4.15,
    6: 4.05,  7: 3.92,  8: 3.80,  9: 3.68,
}

# Arsenal adjustment cap — prevents wild predictions on small samples
ARSENAL_CAP_LO = 0.70
ARSENAL_CAP_HI = 1.30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Math helpers
# ────────────────────────────────────────────────────────────────

def american_to_implied(odds: int | None) -> float | None:
    if odds is None:
        return None
    if odds >= 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def devig_power(implied: float) -> float:
    """
    Power devig for single-sided binary markets.
    For Anytime HR (Yes only), DK's implied probability is overstated by vig.
    Standard estimate: DK builds ~6-8% vig into HR props.
    
    We approximate fair probability as implied / (1 + vig) where vig is
    estimated from the implied (more juice when more skewed to the underdog).
    
    A simpler approach: subtract a flat 4% from the implied (the vig spread
    over both Yes/No, splitting roughly evenly since longshot HRs have higher
    relative vig).
    """
    if implied is None or implied <= 0 or implied >= 1:
        return implied
    # Empirical: DK HR vig ~6% on the Yes side for typical longshot props
    # We approximate: no_vig = implied * (1 / (1 + 0.06))
    # = implied / 1.06
    return implied / 1.06


def pitcher_factor(pitcher_hr_per_9: float | None) -> float:
    """Ratio of pitcher's HR/9 to league average. Capped to prevent extreme."""
    if pitcher_hr_per_9 is None or pitcher_hr_per_9 <= 0:
        return 1.0
    raw = pitcher_hr_per_9 / LEAGUE_HR_PER_9
    return max(0.5, min(1.8, raw))


def compute_arsenal_adjustment(
    batter_by_pitch: dict,   # {pitch_label: {hr_pct, pa, ...}}
    pitcher_arsenal: list,   # list of {pitch_type, usage_pct, ...}
    batter_overall_hr_pct: float,
) -> tuple[float, dict]:
    """
    Arsenal-weighted adjustment. For each pitch the pitcher throws,
    weight by usage. The batter's HR% on that pitch vs their overall HR%
    gives a per-pitch multiplier. Average those weighted.
    
    Returns (adjustment, breakdown_dict_for_transparency).
    """
    if not batter_by_pitch or not pitcher_arsenal or batter_overall_hr_pct <= 0:
        return (1.0, {})

    # Build pitcher usage map (sum L+R for now since we're not stance-splitting yet here)
    usage_map = defaultdict(float)
    for a in pitcher_arsenal:
        pt = a.get("pitch_type")
        u = a.get("usage_pct") or 0
        if pt and u:
            usage_map[pt] += float(u)
    # Normalize usage to 1.0 across pitch types we have data on
    total_usage = sum(usage_map.values())
    if total_usage <= 0:
        return (1.0, {})

    contributions = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for pitch_label, usage_raw in usage_map.items():
        usage = usage_raw / total_usage  # normalize
        batter_stat = batter_by_pitch.get(pitch_label)
        if not batter_stat:
            # Batter has no data on this pitch type → assume neutral
            multiplier = 1.0
            sample_pa = 0
        else:
            b_hr_pct = batter_stat.get("hr_pct", 0)
            sample_pa = batter_stat.get("pa", 0)
            if sample_pa < 10 or batter_overall_hr_pct <= 0:
                multiplier = 1.0  # too few PAs to trust
            else:
                multiplier = b_hr_pct / batter_overall_hr_pct
                # Shrink toward 1.0 based on sample size (Bayesian flavor)
                # Confidence: 0 at PA=10, ~1 at PA=80
                weight = min(1.0, max(0, (sample_pa - 10) / 70))
                multiplier = 1.0 + (multiplier - 1.0) * weight
        weighted_sum += multiplier * usage
        total_weight += usage
        contributions[pitch_label] = {
            "usage": round(usage, 3),
            "mult": round(multiplier, 3),
            "pa": sample_pa,
        }

    if total_weight <= 0:
        return (1.0, contributions)

    adj = weighted_sum / total_weight
    adj_capped = max(ARSENAL_CAP_LO, min(ARSENAL_CAP_HI, adj))
    return (adj_capped, contributions)


def hr_anytime_from_per_pa(hr_per_pa: float, expected_pas: float) -> float:
    """P(at least one HR) given per-PA rate and expected PAs."""
    if hr_per_pa <= 0:
        return 0.0
    if hr_per_pa >= 1:
        return 1.0
    return 1 - math.pow(1 - hr_per_pa, expected_pas)


def edge_bucket(edge: float) -> str:
    """Bucket the edge into a category for the bet log ROI tracker."""
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


def get_lineups_for_game(game_id: int) -> list[dict]:
    res = sb.table("lineups").select(
        "id, team_id, batting_order, position, bats, player_id, is_confirmed"
    ).eq("game_id", game_id).execute()
    return res.data


def get_batter_stats_map(batter_ids: list[int]) -> dict[int, dict]:
    """Return {batter_id: stats_row} for window_type=season and vs_hand=A."""
    if not batter_ids:
        return {}
    res = sb.table("batter_stats").select("*") \
        .in_("batter_id", batter_ids) \
        .eq("window_type", "season") \
        .eq("vs_hand", "A") \
        .execute()
    return {r["batter_id"]: r for r in res.data}


def get_pitcher_arsenal(pitcher_id: int) -> list[dict]:
    if not pitcher_id:
        return []
    res = sb.table("pitcher_arsenals").select("*") \
        .eq("pitcher_id", pitcher_id) \
        .eq("window_type", "season") \
        .execute()
    return res.data


def compute_pitcher_hr_per_9(arsenal: list[dict]) -> float | None:
    """Total HR allowed per 9 innings, rough estimate from arsenal totals."""
    total_pa = sum(a.get("pa", 0) or 0 for a in arsenal)
    total_hr = sum(a.get("hr", 0) or 0 for a in arsenal)
    if total_pa < 50:
        return None
    # ~3.8 PA per inning league average → HR/9 = HR / PA × 3.8 × 9 / 9
    # Wait, HR/9 = HR / IP × 9. IP ≈ PA / 4.3. So HR/9 ≈ HR / PA × 4.3 * 9 / 9 = HR/PA × 4.3
    return (total_hr / total_pa) * 4.3


def get_current_odds(game_id: int, batter_ids: list[int], market: str) -> dict[int, dict]:
    """Return {batter_id: most_recent_odds_row} for this market."""
    if not batter_ids:
        return {}
    res = sb.table("odds_snapshots").select("*") \
        .eq("game_id", game_id) \
        .eq("market", market) \
        .eq("book", "draftkings") \
        .eq("is_current", True) \
        .in_("player_id", batter_ids) \
        .order("snapshot_time", desc=True) \
        .execute()
    out = {}
    for row in res.data:
        # Most recent wins (we ordered DESC)
        if row["player_id"] not in out:
            out[row["player_id"]] = row
    return out


# ────────────────────────────────────────────────────────────────
# Per-game projection pipeline
# ────────────────────────────────────────────────────────────────

def project_game(game: dict) -> list[dict]:
    """Returns list of projection rows ready for the `projections` table."""
    lineups = get_lineups_for_game(game["id"])
    if not lineups:
        return []

    # Separate by team
    away_team_id = game["away_team_id"]
    home_team_id = game["home_team_id"]
    away_batters = [l for l in lineups if l["team_id"] == away_team_id]
    home_batters = [l for l in lineups if l["team_id"] == home_team_id]

    all_batter_ids = [l["player_id"] for l in lineups if l.get("player_id")]
    if not all_batter_ids:
        return []

    batter_stats_map = get_batter_stats_map(all_batter_ids)
    away_odds_map = get_current_odds(game["id"], all_batter_ids, "hr_anytime_yes")

    # Pitcher arsenals
    away_arsenal = get_pitcher_arsenal(game.get("away_pitcher_id"))
    home_arsenal = get_pitcher_arsenal(game.get("home_pitcher_id"))
    away_hr9 = compute_pitcher_hr_per_9(away_arsenal)
    home_hr9 = compute_pitcher_hr_per_9(home_arsenal)

    rows = []
    snapshot_time = datetime.now(timezone.utc).isoformat()

    # Away batters face home pitcher
    for batter in away_batters:
        row = _project_single(
            batter=batter,
            opposing_arsenal=home_arsenal,
            opposing_hr_per_9=home_hr9,
            batter_stats=batter_stats_map.get(batter["player_id"]),
            odds_row=away_odds_map.get(batter["player_id"]),
            park_factor_lhb=game.get("hr_factor_lhb"),
            park_factor_rhb=game.get("hr_factor_rhb"),
            park_factor_overall=game.get("hr_factor_overall"),
            game_id=game["id"],
            snapshot_time=snapshot_time,
        )
        if row:
            rows.append(row)

    # Home batters face away pitcher
    for batter in home_batters:
        row = _project_single(
            batter=batter,
            opposing_arsenal=away_arsenal,
            opposing_hr_per_9=away_hr9,
            batter_stats=batter_stats_map.get(batter["player_id"]),
            odds_row=away_odds_map.get(batter["player_id"]),
            park_factor_lhb=game.get("hr_factor_lhb"),
            park_factor_rhb=game.get("hr_factor_rhb"),
            park_factor_overall=game.get("hr_factor_overall"),
            game_id=game["id"],
            snapshot_time=snapshot_time,
        )
        if row:
            rows.append(row)

    return rows


def _project_single(
    batter: dict,
    opposing_arsenal: list[dict],
    opposing_hr_per_9: float | None,
    batter_stats: dict | None,
    odds_row: dict | None,
    park_factor_lhb: float | None,
    park_factor_rhb: float | None,
    park_factor_overall: float | None,
    game_id: int,
    snapshot_time: str,
) -> dict | None:
    """Project a single batter's HR Anytime probability."""

    if not batter_stats:
        return None
    hr_per_pa = batter_stats.get("hr_per_pa")
    if hr_per_pa is None or hr_per_pa <= 0:
        return None
    hr_per_pa = float(hr_per_pa)
    batter_overall_hr_pct = hr_per_pa * 100  # convert to % units for arsenal calc

    # Park factor for batter's handedness
    bats = batter.get("bats") or "R"
    if bats == "L":
        park = park_factor_lhb or park_factor_overall or 1.0
    elif bats == "S":
        # switch hitters face opposite-handed pitcher's side, default to overall
        park = park_factor_overall or 1.0
    else:
        park = park_factor_rhb or park_factor_overall or 1.0
    park = float(park)

    # Pitcher factor
    p_factor = pitcher_factor(opposing_hr_per_9)

    # Arsenal adjustment
    by_pitch = batter_stats.get("by_pitch_type") or {}
    arsenal_adj, _breakdown = compute_arsenal_adjustment(
        by_pitch, opposing_arsenal, batter_overall_hr_pct
    )

    # Weather is already baked into park_factor (via games.hr_factor_*)
    # So we don't double-apply. But park_factor itself = static_park × weather_mult.
    # Since hr_factor_lhb/rhb in DB already includes weather, that's correct.

    base = hr_per_pa
    projected_hr_per_pa = base * p_factor * park * arsenal_adj
    projected_hr_per_pa = max(0.001, min(0.20, projected_hr_per_pa))  # sanity bounds

    # Expected PAs based on lineup spot
    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)

    projected_prob = hr_anytime_from_per_pa(projected_hr_per_pa, expected_pas)

    # If we have DK odds, compute no_vig and edge
    edge = None
    no_vig = None
    best_american = None
    if odds_row:
        american = odds_row.get("american_odds")
        if american is not None:
            implied = american_to_implied(american)
            no_vig = devig_power(implied)
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
        "weather_adj": 1.0,   # already in park_adj
        "arsenal_adj": round(arsenal_adj, 3),
        "best_book": "draftkings" if best_american is not None else None,
        "best_american_odds": best_american,
        "no_vig_prob": round(no_vig, 4) if no_vig is not None else None,
        "edge": round(edge, 4) if edge is not None else None,
        "edge_bucket": edge_bucket(edge) if edge is not None else None,
        "created_at": snapshot_time,
    }


# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────

def main():
    log.info("🧅 Cebolla Lab — projection compute starting (model %s)", MODEL_VERSION)

    games = get_todays_games()
    if not games:
        log.info("No games today; nothing to project.")
        return
    log.info("Found %d games", len(games))

    all_rows = []
    for i, g in enumerate(games, 1):
        # Skip already-final games
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

    # Upsert in chunks (conflict on game+player+market+model_version)
    written = 0
    for i in range(0, len(all_rows), 200):
        chunk = all_rows[i:i+200]
        sb.table("projections").upsert(
            chunk,
            on_conflict="game_id,player_id,market,model_version",
        ).execute()
        written += len(chunk)

    log.info("✓ Wrote %d", written)

    # Print top 10 edges for diagnostics
    sorted_rows = sorted(
        [r for r in all_rows if r.get("edge") is not None],
        key=lambda r: r["edge"],
        reverse=True,
    )
    log.info("─── TOP 5 edges (model says BACK) ───")
    for r in sorted_rows[:5]:
        log.info("  game %d player %d  proj=%.1f%%  no_vig=%.1f%%  edge=%+.2f%%  bucket=%s",
                 r["game_id"], r["player_id"],
                 r["projected_prob"]*100, (r["no_vig_prob"] or 0)*100,
                 r["edge"]*100, r["edge_bucket"])
    log.info("─── BOTTOM 5 edges (model says FADE) ───")
    for r in sorted_rows[-5:]:
        log.info("  game %d player %d  proj=%.1f%%  no_vig=%.1f%%  edge=%+.2f%%  bucket=%s",
                 r["game_id"], r["player_id"],
                 r["projected_prob"]*100, (r["no_vig_prob"] or 0)*100,
                 r["edge"]*100, r["edge_bucket"])

    log.info("🧅 Projection compute complete")


if __name__ == "__main__":
    main()
