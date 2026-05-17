"""
compute_projections.py — Cebolla Lab projection model v0.2.0

NEW IN v0.2.0:
  - 1+ HITS market (`hits_yes`) — full pipeline parallel to HR Anytime
  - Writes BOTH `hr_anytime` and `hits_yes` projections per batter per game
  - Hits-specific shrinkage (smaller K — hits stabilize faster than HRs)
  - Hits-specific vig curve (hits Yes is rarely a longshot)
  - No arsenal adjustment for hits (pitch-type effect <2%, not worth noise)

Carried over from v0.1.3:
  - Bayesian shrinkage on batter HR/PA and pitcher HR/9
  - Dynamic vig curve per market
  - Longshot filter
  - Arsenal-weighted adjustment for HR

HR market formula (unchanged):
  shrunk_batter_hr_per_pa = (PA × obs + 200 × LEAGUE) / (PA + 200)
  shrunk_pitcher_hr_per_9 = (BF × obs + 80 × LEAGUE) / (BF + 80)
  projected_hr_per_pa = shrunk_batter × pitcher_factor × park × arsenal_adj
  projected_anytime = 1 - (1 - per_pa)^expected_PAs

HITS market formula (NEW):
  shrunk_batter_hit_per_pa = (PA × obs + 100 × LEAGUE) / (PA + 100)
  shrunk_pitcher_hit_per_pa = (BF × obs + 60 × LEAGUE) / (BF + 60)
  pitcher_baa_factor = clamp(shrunk_p_hit_per_pa / LEAGUE_HIT_PER_PA, [0.80, 1.25])
  park_ba_factor = clamp(team.park_ba_factor, [0.90, 1.10])
  projected_hit_per_pa = shrunk_batter × pitcher_baa_factor × park_ba_factor
  projected_1plus_hits = 1 - (1 - per_pa)^expected_PAs
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

MODEL_VERSION = "v0.2.0"

# ──── HR market constants ────
LEAGUE_HR_PER_PA = 0.029
LEAGUE_HR_PER_9  = 1.15
BATTER_SHRINKAGE_K  = 200
PITCHER_SHRINKAGE_K = 80
ARSENAL_CAP_LO = 0.85
ARSENAL_CAP_HI = 1.15
ARSENAL_MIN_MULT = 0.5
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

# ──── Longshot filters ────
HR_LONGSHOT_THRESHOLD   = 2000           # HR market — filter at +2000+
HITS_LONGSHOT_THRESHOLD = 600            # Hits market — filter at +600+

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
                raw_mult = max(ARSENAL_MIN_MULT, min(2.0, raw_mult))
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


def one_minus_pow_per_pa(rate_per_pa: float, expected_pas: float) -> float:
    """1 - (1 - rate_per_pa)^PAs. Used for both HR Anytime and 1+ Hits."""
    if rate_per_pa <= 0:
        return 0.0
    if rate_per_pa >= 1:
        return 1.0
    return 1 - math.pow(1 - rate_per_pa, expected_pas)


# Alias for back-compat readability
hr_anytime_from_per_pa = one_minus_pow_per_pa


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

    # Two odds markets, fetched in parallel
    # NOTE: hits_yes contains BOTH 1+ Hits (line=0.5) and 2+ Hits (line=1.5)
    # rows because DK uses Yes/No labels for both ladders. Filter to line=0.5.
    hr_odds_map   = get_current_odds(game["id"], all_batter_ids, "hr_anytime_yes")
    hits_odds_map = get_current_odds(game["id"], all_batter_ids, "hits_yes", line=0.5)

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

        hr_row = _project_hr(
            batter, home_arsenal, home_pstats, bstats,
            hr_odds_map.get(batter["player_id"]),
            game.get("hr_factor_lhb"), game.get("hr_factor_rhb"),
            game.get("hr_factor_overall"), game["id"], snapshot_time,
        )
        if hr_row:
            rows.append(hr_row)

        hits_row = _project_hits(
            batter, home_pstats, bstats,
            hits_odds_map.get(batter["player_id"]),
            park_ba, game["id"], snapshot_time,
        )
        if hits_row:
            rows.append(hits_row)

    # Home batters face AWAY pitcher
    for batter in home_batters:
        bstats = batter_stats_map.get(batter["player_id"])

        hr_row = _project_hr(
            batter, away_arsenal, away_pstats, bstats,
            hr_odds_map.get(batter["player_id"]),
            game.get("hr_factor_lhb"), game.get("hr_factor_rhb"),
            game.get("hr_factor_overall"), game["id"], snapshot_time,
        )
        if hr_row:
            rows.append(hr_row)

        hits_row = _project_hits(
            batter, away_pstats, bstats,
            hits_odds_map.get(batter["player_id"]),
            park_ba, game["id"], snapshot_time,
        )
        if hits_row:
            rows.append(hits_row)

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

    # ─── Combine ───
    projected_per_pa = base * p_factor * park * arsenal_adj
    projected_per_pa = max(0.001, min(PROJ_PER_PA_CAP, projected_per_pa))

    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)
    projected_prob = one_minus_pow_per_pa(projected_per_pa, expected_pas)

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
    odds_row: dict | None,
    park_ba_factor: float,
    game_id: int,
    snapshot_time: str,
) -> dict | None:
    """1+ Hits projection — one row per batter."""
    if not batter_stats:
        return None

    raw_hit_per_pa = batter_stats.get("hit_per_pa")
    pa = batter_stats.get("pa") or 0
    if raw_hit_per_pa is None:
        return None
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

    # ─── Combine (no arsenal adjustment for hits — pitch type effect is noise) ───
    projected_per_pa = base * p_factor * park
    projected_per_pa = max(0.001, min(PROJ_HIT_PER_PA_CAP, projected_per_pa))

    spot = batter.get("batting_order") or 6
    expected_pas = PA_BY_LINEUP_SPOT.get(spot, 4.0)
    projected_prob = one_minus_pow_per_pa(projected_per_pa, expected_pas)

    # ─── Edge ───
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
        "market": "hits_yes",
        "model_version": MODEL_VERSION,
        "projected_prob": round(projected_prob, 4),
        "base_rate": round(base, 5),
        "pitcher_adj": round(p_factor, 3),
        "park_adj": round(park, 3),
        "weather_adj": 1.0,
        "arsenal_adj": 1.0,                          # n/a for hits
        "best_book": "draftkings" if best_american is not None else None,
        "best_american_odds": best_american,
        "no_vig_prob": round(no_vig, 4) if no_vig is not None else None,
        "edge": round(edge, 4) if edge is not None else None,
        "edge_bucket": edge_bucket(edge) if best_american is not None else None,
        "created_at": snapshot_time,
    }


# Back-compat shim (older code paths)
_project_single = _project_hr


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

    hr_rows   = [r for r in all_rows if r["market"] == "hr_anytime"]
    hits_rows = [r for r in all_rows if r["market"] == "hits_yes"]
    _diag("HR ANYTIME", hr_rows)
    _diag("1+ HITS",    hits_rows)

    log.info("🧅 Projection compute complete")


if __name__ == "__main__":
    main()
