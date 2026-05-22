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
import json
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
    res = sb.table("games") \
        .select("id, home_team_id, away_team_id, "
                "home_team:teams!games_home_team_id_fkey(abbrev), "
                "away_team:teams!games_away_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .in_("status", ["Pre-Game", "Scheduled", "Warmup", "Postponed"]) \
        .execute()
    return res.data or []


def fetch_batter_l14_stats(player_ids, season):
    """L14 batter_stats for all candidate players. Returns {player_id: row}."""
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, hit_per_pa, barrel_pct, hard_hit_pct, "
                "xslg, xba, by_pitch_type") \
        .eq("season", season) \
        .eq("window_type", "l14") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_pitcher_arsenals(pitcher_ids, season):
    """Pitcher arsenals. Returns {pitcher_id: [arsenal_rows]}."""
    if not pitcher_ids:
        return {}
    res = sb.table("pitcher_arsenals") \
        .select("pitcher_id, pitch_type, usage_pct, hr_pct") \
        .eq("season", season) \
        .eq("window_type", "l50g") \
        .eq("vs_stance", "A") \
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


def fetch_starting_pitcher_for_game(game_ids):
    """{(game_id, team_id): pitcher_id} — each team's starter."""
    if not game_ids:
        return {}
    res = sb.table("lineups") \
        .select("game_id, team_id, pitcher_id") \
        .in_("game_id", game_ids) \
        .not_.is_("pitcher_id", "null") \
        .execute()
    out = {}
    for r in res.data or []:
        out[(r["game_id"], r["team_id"])] = r["pitcher_id"]
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


# ─── Tier-aware candidate construction ────────────────────────────────────────

def _enrich_with_tiers(
    p, market_class,
    games_by_id, players_by_id,
    contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
):
    """Evaluate a projection through tier system. None if disqualified."""
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

    pitcher_primary = None
    if opposing_pitcher_id:
        arsenal_rows = pitcher_arsenals.get(opposing_pitcher_id) or []
        pitcher_primary = primary_pitch_type(arsenal_rows)

    bvp_row = bvp_pairs.get((p["player_id"], opposing_pitcher_id)) if opposing_pitcher_id else None

    # Tier 1
    if market_class == "hr":
        t1_hits, t1_detail = evaluate_tier1_hr(bstats, pitcher_primary)
    else:
        t1_hits, t1_detail = evaluate_tier1_hrr(bstats, bvp_row)

    # Tier 2
    heat_tier = heat.get("combined_tier") if heat else None
    contact = contact_by_player.get(p["player_id"])
    t2_hits, t2_detail = evaluate_tier2(bstats, heat_tier, contact, bvp_row)

    # Score
    score = score_candidate(t1_hits, t2_hits)
    if score is None:
        return None
    qpath = qualification_path(t1_hits, t2_hits)

    # EV per dollar
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

    # Stake modifier
    stake_mod = p.get("stake_modifier")
    if stake_mod is None:
        stake_mod = stake_modifier_for(p.get("park_adj") or 1.0, p.get("weather_adj") or 1.0)

    own_abbrev = (game["home_team"] if is_home else game["away_team"])["abbrev"]
    opp_abbrev = (game["away_team"] if is_home else game["home_team"])["abbrev"]

    metadata = {
        "tier1": t1_detail,
        "tier2": t2_detail,
        "qualification_path": qpath,
        "stake_modifier": stake_mod,
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
        "combined_tier": heat_tier,
        "combined_trend": heat.get("combined_trend") if heat else None,
    }


def fetch_hr_candidates(
    date_iso, games, players_by_id, contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
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
        .gte("projected_prob", HR_MIN_PROJECTED_PROB) \
        .gte("edge", HR_MIN_EDGE) \
        .not_.is_("best_american_odds", "null") \
        .execute()

    enriched = []
    for p in proj_res.data or []:
        cand = _enrich_with_tiers(
            p, "hr", games_by_id, players_by_id, contact_by_player, heat_by_player,
            batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
        )
        if cand:
            enriched.append(cand)

    enriched.sort(key=lambda c: (c["tier_score"], *tier3_key(c)), reverse=True)
    return enriched


def fetch_hrr_candidates(
    date_iso, games, players_by_id, contact_by_player, heat_by_player,
    batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
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
    for player_id, projs in by_batter.items():
        best = max(projs, key=lambda x: (float(x["edge"] or 0),
                                         float(x["projected_prob"] or 0)))
        cand = _enrich_with_tiers(
            best, "hrr", games_by_id, players_by_id, contact_by_player, heat_by_player,
            batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
        )
        if cand:
            enriched.append(cand)

    enriched.sort(key=lambda c: (c["tier_score"], *tier3_key(c)), reverse=True)
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
        # tier_score is 0.85-1.25; scale to 0-10000 range to look comparable.
        "combined_score": round(pick["tier_score"] * 9000, 1),
        # Tier system fields
        "tier1_hits": pick["tier1_hits"],
        "tier2_hits": pick["tier2_hits"],
        "tier_score": pick["tier_score"],
        "stake_modifier": pick["stake_modifier"],
        "tier_metadata": json.dumps(pick["tier_metadata"]),
        "stake": 10.00,
        "status": "pending",
    }).execute()


def log_top3(label, candidates):
    log.info("Top %s candidates by tier_score (then edge/EV):", label)
    for i, c in enumerate(candidates[:3], 1):
        cs = c["contact_score"]
        cs_str = f"{cs:.0f}" if cs is not None else "—"
        heat_str = f" heat {c['combined_tier']}" if c.get("combined_tier") else ""
        sm = c.get("stake_modifier") or 1.0
        sm_str = f" [stake_mod {sm:+.2f}]" if abs(sm - 1.0) > 0.02 else ""
        ev_str = f" ev/${c['ev_per_dollar']:+.3f}" if c.get("ev_per_dollar") is not None else ""
        log.info(
            "  #%d  %s (%s vs %s)  %s  proj %.1f%%  odds %+d  edge %.3f%s  "
            "T1=%d/3 T2=%d/4 path=%s  score=%.3f  contact %s%s%s",
            i, c["player_name"], c["team_abbrev"], c["opponent_abbrev"],
            c["market"],
            100 * float(c["projected_prob"]),
            c["american_odds"], float(c["edge"]), ev_str,
            c["tier1_hits"], c["tier2_hits"], c["qualification_path"],
            c["tier_score"], cs_str, heat_str, sm_str,
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
    log.info(
        "[%s] ✓ POD locked for %s: %s @ %+d (edge %.3f, T1=%d/3 T2=%d/4 score=%.3f path=%s)",
        market_class.upper(), date_iso, pick["player_name"], pick["american_odds"],
        float(pick["edge"]), pick["tier1_hits"], pick["tier2_hits"],
        pick["tier_score"], pick["qualification_path"],
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = get_today_iso()
    log.info("🧅 POD picker — slate %s (HR + HRR, tier system v0.4.0)", today)

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
        .not_.is_("edge", "null") \
        .execute()
    all_proj_player_ids = list({p["player_id"] for p in (proj_ids_res.data or [])})
    if not all_proj_player_ids:
        log.warning("No projections with edge data for %s.", today)
        return

    player_res = sb.table("players").select("id, mlbam_id, name, team_id") \
        .in_("id", all_proj_player_ids).execute()
    players_by_id = {p["id"]: p for p in (player_res.data or [])}

    log.info("Fetching tier-system data for %d candidate players...", len(all_proj_player_ids))
    batter_stats_l14 = fetch_batter_l14_stats(all_proj_player_ids, season)
    log.info("  L14 stats: %d rows", len(batter_stats_l14))

    starter_by_game_team = fetch_starting_pitcher_for_game(game_ids)
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

    # ── HR POD ──
    hr_candidates = fetch_hr_candidates(
        today, games, players_by_id, contact_by_player, heat_by_player,
        batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
    )
    log.info("HR candidates qualifying tier system: %d", len(hr_candidates))
    log_top3("HR", hr_candidates)
    pick_for_market(today, "hr", hr_candidates)

    # ── HRR POD ──
    hrr_candidates = fetch_hrr_candidates(
        today, games, players_by_id, contact_by_player, heat_by_player,
        batter_stats_l14, pitcher_arsenals, bvp_pairs, starter_by_game_team,
    )
    log.info("HRR candidates qualifying tier system: %d", len(hrr_candidates))
    log_top3("HRR", hrr_candidates)
    pick_for_market(today, "hrr", hrr_candidates)

    log.info("🧅 POD picker complete")


if __name__ == "__main__":
    main()
