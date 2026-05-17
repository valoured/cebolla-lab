"""
compute_projections.py — Cebolla Lab projection model v0.1.1

Changes from v0.1.0:
  - Bayesian shrinkage on batter HR/PA using K=200 phantom PAs (regress to league mean)
  - Tightened arsenal_adj cap: [0.85, 1.15] (was [0.70, 1.30])
  - Tightened pitcher_factor cap: [0.75, 1.40] (was [0.50, 1.80])
  - Hard cap on projected_hr_per_pa: 0.08 (was 0.20)
  - Floor on arsenal multiplier: 0.5 (was 0, which let zero-HR small-sample pitch types nuke the projection)
  - Diagnostic logging: per-component breakdown for top 3 edges

Math:
  shrunk_hr_per_pa = (PA × observed + K × league) / (PA + K)
  projected_per_pa = shrunk × pitcher_factor × park_factor × arsenal_adj
  projected_anytime = 1 - (1 - per_pa)^expected_PAs
  edge = projected_anytime - no_vig_prob_from_DK
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

MODEL_VERSION = "v0.1.1"

# ──── League constants ────
LEAGUE_HR_PER_PA = 0.029
LEAGUE_HR_PER_9  = 1.15

# ──── Shrinkage constant for batter HR/PA ────
# Higher K = more regression to mean for small samples.
# K=200 means a batter needs ~200 PA to be ~50% trusted vs league avg.
SHRINKAGE_K = 200

# ──── Caps (tightened in v0.1.1) ────
ARSENAL_CAP_LO = 0.85
ARSENAL_CAP_HI = 1.15
ARSENAL_MIN_MULT = 0.5    # individual pitch-type multiplier floor
PITCHER_CAP_LO = 0.75
PITCHER_CAP_HI = 1.40
PARK_CAP_LO    = 0.80
PARK_CAP_HI    = 1.20
PROJ_PER_PA_CAP = 0.08    # absolute ceiling

# ──── Expected PAs per lineup spot ────
PA_BY_LINEUP_SPOT = {
    1: 4.55,  2: 4.45,  3: 4.35,  4: 4.25,  5: 4.15,
    6: 4.05,  7: 3.92,  8: 3.80,  9: 3.68,
}

# ──── Devig assumption ────
DK_HR_VIG = 0.06    # DK builds ~6% vig into HR Anytime props

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Math helpers
# ────────────────────────────────────────────────────────────────

def shrink_hr_per_pa(observed: float, pa: int) -> float:
    """Bayesian shrinkage toward league mean."""
    if pa is None or pa <= 0:
        return LEAGUE_HR_PER_PA
    return (pa * observed + SHRINKAGE_K * LEAGUE_HR_PER_PA) / (pa + SHRINKAGE_K)


def american_to_implied(odds: int | None) -> float | None:
    if odds is None:
        return None
    if odds >= 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def devig_anytime(implied: float | None) -> float | None:
    """DK 'Anytime HR' is single-sided. Simple vig removal."""
    if implied is None or implied <= 0 or implied >= 1:
        return implied
    return implied / (1 + DK_HR_VIG)


def pitcher_factor(pitcher_hr_per_9: float | None) -> float:
    if pitcher_hr_per_9 is None or pitcher_hr_per_9 <= 0:
        return 1.0
    raw = pitcher_hr_per_9 / LEAGUE_HR_PER_9
    return max(PITCHER_CAP_LO, min(PITCHER_CAP_HI, raw))


def park_capped(p: float) -> float:
    return max(PARK_CAP_LO, min(PARK_CAP_HI, p))


def compute_arsenal_adjustment(
    batter_by_pitch: dict | None,
    pitcher_arsenal: list,
    batter_overall_hr_pct: float,
) -> tuple[float, dict]:
    """
    Weighted average of per-pitch multipliers, shrunk by sample size,
    capped at the individual-pitch and overall levels.
    """
    if not batter_by_pitch or not pitcher_arsenal or batter_overall_hr_pct <= 0:
        return (1.0, {})

    usage_map = defaultdict(float)
    for a in pitcher_arsenal:
        pt = a.get("pitch_type")
        u = a.get("usage_pct") or 0
        if pt and u:
            usage_map[pt] += float(u)
    total_usage = sum(usage_map.values())
    if total_usage <= 0:
        return (1.0, {})

    contributions = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for pitch_label, usage_raw in usage_map.items():
        usage = usage_raw / total_usage
        batter_stat = batter_by_pitch.get(pitch_label)
        if not batter_stat:
            multiplier = 1.0
            sample_pa = 0
        else:
            b_hr_pct = batter_stat.get("hr_pct", 0) or 0
            sample_pa = batter_stat.get("pa", 0) or 0
            if sample_pa < 10:
                multiplier = 1.0
            else:
                raw_mult = b_hr_pct / batter_overall_hr_pct
                # Floor on individual multiplier (prevents 0-HR/15-PA from nuking)
                raw_mult = max(ARSENAL_MIN_MULT, min(2.0, raw_mult))
                # Bayesian-ish shrinkage based on sample
                # Weight: 0 at PA=10, 1 at PA=150
                weight = min(1.0, max(0, (sample_pa - 10) / 140))
                multiplier = 1.0 + (raw_mult - 1.0) * weight
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
    if hr_per_pa <= 0:
        return 0.0
    if hr_per_pa >= 1:
        return 1.0
    return 1 - math.pow(1 - hr_per_pa, expected_pas)


def edge_bucket(edge: float) -> str:
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
    if not batter_ids:
        return {}
    res = sb.table("batter_stats").select("*") \
        .in_("batter_id", batter_ids) \
        .eq("window_type", "season") \
        .eq("vs_hand", "A") \
        .execute()
    return {r["batter_id"]: r for r in res.data}


def get_player_names(player_ids: list[int]) -> dict[int, str]:
    """For diagnostic logging."""
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


def compute_pitcher_hr_per_9(arsenal: list[dict]) -> float | None:
    total_pa = sum(a.get("pa", 0) or 0 for a in arsenal)
    total_hr = sum(a.get("hr", 0) or 0 for a in arsenal)
    if total_pa < 50:
        return None
    # HR/9 ≈ HR/PA × 4.3 (avg PA per inning)
    return (total_hr / total_pa) * 4.3


def get_current_odds(game_id: int, batter_ids: list[int], market: str) -> dict[int, dict]:
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
        if row["player_id"] not in out:
            out[row["player_id"]] = row
    return out


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
    odds_map = get_current_odds(game["id"], all_batter_ids, "hr_anytime_yes")

    away_arsenal = get_pitcher_arsenal(game.get("away_pitcher_id"))
    home_arsenal = get_pitcher_arsenal(game.get("home_pitcher_id"))
    away_hr9 = compute_pitcher_hr_per_9(away_arsenal)
    home_hr9 = compute_pitcher_hr_per_9(home_arsenal)

    rows = []
    snapshot_time = datetime.now(timezone.utc).isoformat()

    for batter in away_batters:
        row = _project_single(
            batter, home_arsenal, home_hr9, batter_stats_map.get(batter["player_id"]),
            odds_map.get(batter["player_id"]),
            game.get("hr_factor_lhb"), game.get("hr_factor_rhb"),
            game.get("hr_factor_overall"), game["id"], snapshot_time,
        )
        if row:
            rows.append(row)

    for batter in home_batters:
        row = _project_single(
            batter, away_arsenal, away_hr9, batter_stats_map.get(batter["player_id"]),
            odds_map.get(batter["player_id"]),
            game.get("hr_factor_lhb"), game.get("hr_factor_rhb"),
            game.get("hr_factor_overall"), game["id"], snapshot_time,
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
    if not batter_stats:
        return None

    raw_hr_per_pa = batter_stats.get("hr_per_pa")
    pa = batter_stats.get("pa") or 0
    if raw_hr_per_pa is None:
        return None
    raw_hr_per_pa = float(raw_hr_per_pa)

    # Bayesian shrinkage
    base = shrink_hr_per_pa(raw_hr_per_pa, pa)

    # Park factor by handedness
    bats = batter.get("bats") or "R"
    if bats == "L":
        park_raw = park_factor_lhb or park_factor_overall or 1.0
    elif bats == "S":
        park_raw = park_factor_overall or 1.0
    else:
        park_raw = park_factor_rhb or park_factor_overall or 1.0
    park = park_capped(float(park_raw))

    # Pitcher factor
    p_factor = pitcher_factor(opposing_hr_per_9)

    # Arsenal adjustment — uses shrunk base for the "overall" comparison too
    # Note: by_pitch stores HR% in percent units, not per-PA. Convert.
    base_pct_for_arsenal = base * 100
    by_pitch = batter_stats.get("by_pitch_type") or {}
    arsenal_adj, _breakdown = compute_arsenal_adjustment(
        by_pitch, opposing_arsenal, base_pct_for_arsenal
    )

    projected_per_pa = base * p_factor * park * arsenal_adj
    projected_per_pa = max(0.001, min(PROJ_PER_PA_CAP, projected_per_pa))

    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)

    projected_prob = hr_anytime_from_per_pa(projected_per_pa, expected_pas)

    edge = None
    no_vig = None
    best_american = None
    if odds_row:
        american = odds_row.get("american_odds")
        if american is not None:
            implied = american_to_implied(american)
            no_vig = devig_anytime(implied)
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
    log.info("   Shrinkage K=%d, arsenal cap=[%.2f, %.2f], pitcher cap=[%.2f, %.2f]",
             SHRINKAGE_K, ARSENAL_CAP_LO, ARSENAL_CAP_HI, PITCHER_CAP_LO, PITCHER_CAP_HI)

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

    # ──── Diagnostics ────
    # Get player names for top edges so we can verify
    sorted_by_edge = sorted(
        [r for r in all_rows if r.get("edge") is not None],
        key=lambda r: r["edge"], reverse=True,
    )
    edge_player_ids = list({r["player_id"] for r in sorted_by_edge[:5] + sorted_by_edge[-5:]})
    name_map = get_player_names(edge_player_ids)

    log.info("─── TOP 5 edges (model says BACK) ───")
    for r in sorted_by_edge[:5]:
        nm = name_map.get(r["player_id"], f"#{r['player_id']}")
        log.info("  %-25s odds=%+d  proj=%.1f%%  no_vig=%.1f%%  edge=%+.2f%%  [base=%.4f p=%.2f park=%.2f arsenal=%.2f]",
                 nm[:25], r["best_american_odds"] or 0,
                 r["projected_prob"]*100,
                 (r["no_vig_prob"] or 0)*100,
                 r["edge"]*100,
                 r["base_rate"], r["pitcher_adj"], r["park_adj"], r["arsenal_adj"])

    log.info("─── BOTTOM 5 edges (model says FADE) ───")
    for r in sorted_by_edge[-5:]:
        nm = name_map.get(r["player_id"], f"#{r['player_id']}")
        log.info("  %-25s odds=%+d  proj=%.1f%%  no_vig=%.1f%%  edge=%+.2f%%  [base=%.4f p=%.2f park=%.2f arsenal=%.2f]",
                 nm[:25], r["best_american_odds"] or 0,
                 r["projected_prob"]*100,
                 (r["no_vig_prob"] or 0)*100,
                 r["edge"]*100,
                 r["base_rate"], r["pitcher_adj"], r["park_adj"], r["arsenal_adj"])

    # Distribution stats
    edges_pct = [r["edge"] * 100 for r in sorted_by_edge]
    if edges_pct:
        log.info("─── Edge distribution ───")
        log.info("  Min: %+.2f%%   Median: %+.2f%%   Max: %+.2f%%   Count: %d",
                 min(edges_pct),
                 sorted(edges_pct)[len(edges_pct)//2],
                 max(edges_pct),
                 len(edges_pct))
        strong_back = sum(1 for e in edges_pct if e >= 5)
        strong_fade = sum(1 for e in edges_pct if e <= -5)
        log.info("  strong_back (≥+5%%): %d   strong_fade (≤-5%%): %d", strong_back, strong_fade)

    log.info("🧅 Projection compute complete")


if __name__ == "__main__":
    main()
