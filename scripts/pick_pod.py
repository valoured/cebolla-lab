"""
pick_pod.py — Daily Play of the Day selection.

Picks ONE HR prop per slate using a multiplicative composite of market edge
AND L14 contact quality. The winner is the batter where BOTH signals agree:
strong market edge AND elite recent contact.

Selection criteria (all must hold):
  - market = HR_MARKET
  - projected_prob >= MIN_PROJECTED_PROB   (sanity floor)
  - edge IS NOT NULL                       (need a market price)
  - american_odds IS NOT NULL              (need a number to log)

Among qualifying candidates, ranks by:
  combined_score = normalize_edge(edge) * normalize_contact(score)

  where:
    - normalize_edge clamps edge to ±10% and maps to 0–100
    - normalize_contact uses the L14 contact score (0–100) directly,
      computed exactly like the frontend useContactScore.js helper
    - missing contact_score falls back to neutral 50 (so a batter with
      strong edge but no L14 data still ranks reasonably)

Run order:
  pull_schedule → pull_savant → compute_projections → pick_pod

The morning cron at 14:13 UTC (10:13 AM ET) is the right window — after
projections have been computed but before any games start.

Math kept in lockstep with cebolla-frontend/src/composables/useContactScore.js
and BatterTable.vue's combinedScore(). Any change to one MUST be mirrored
in the other or the POD pick won't match what the UI surfaces.
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client
from dotenv import load_dotenv

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

HR_MARKET = "hr_anytime"
MIN_PROJECTED_PROB = 0.30

# Contact-score constants — keep in sync with useContactScore.js
CONTACT_MIN_PA = 20
CONTACT_WEIGHTS = {"barrel_pct": 0.40, "hard_hit_pct": 0.30, "xslg": 0.30}
NEUTRAL_PERCENTILE = 50

# Combined-sort constants — keep in sync with BatterTable.vue combinedScore()
EDGE_CLAMP_PCT = 10  # clamp edge to ±10% before normalizing


def get_today_iso():
    """ET-relative date for POD purposes (same as elsewhere)."""
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


def existing_pod_for(date_iso):
    res = sb.table("pods").select("id").eq("pod_date", date_iso).limit(1).execute()
    return bool(res.data)


# ─── Contact score math (mirrors useContactScore.js exactly) ──────────────────

def _percentile_rank(value, pool):
    """Average-rank percentile of `value` within `pool`. Returns 0-100 or None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    finite = [float(p) for p in pool if p is not None and isinstance(p, (int, float))]
    if len(finite) < 3:
        return None
    below = sum(1 for p in finite if p < v)
    equal = sum(1 for p in finite if p == v)
    rank = (below + equal / 2) / len(finite)
    return max(0.0, min(100.0, rank * 100.0))


def _build_contact_pools(stat_rows):
    """Build per-component pools (filtered to PA >= MIN_PA)."""
    pools = {"barrel_pct": [], "hard_hit_pct": [], "xslg": []}
    for row in stat_rows or []:
        if not row:
            continue
        pa = row.get("pa")
        try:
            pa_v = float(pa) if pa is not None else 0
        except (TypeError, ValueError):
            continue
        if pa_v < CONTACT_MIN_PA:
            continue
        for key in pools.keys():
            v = row.get(key)
            if v is not None:
                try:
                    pools[key].append(float(v))
                except (TypeError, ValueError):
                    pass
    return pools


def _contact_score(stats, pools):
    """0-100 weighted percentile composite, or None when not scorable."""
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


def fetch_contact_scores(player_ids, season):
    """Returns {player_id: contact_score_or_none}.

    Pulls league-wide qualified L14 pool once, scores each requested player.
    Players not in the L14 dataset (low PA / no recent activity) get None.
    """
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


# ─── Combined-sort math (mirrors BatterTable.vue combinedScore() exactly) ─────

def _normalize_edge(edge):
    """Map edge (decimal, e.g. 0.05 = 5%) to 0-100 via ±EDGE_CLAMP_PCT clamp."""
    if edge is None:
        return 50.0
    try:
        pct = float(edge) * 100.0
    except (TypeError, ValueError):
        return 50.0
    clamped = max(-EDGE_CLAMP_PCT, min(EDGE_CLAMP_PCT, pct))
    return ((clamped + EDGE_CLAMP_PCT) / (2.0 * EDGE_CLAMP_PCT)) * 100.0


def _normalize_contact(score):
    """Pass-through clamp to 0-100. None → neutral 50."""
    if score is None:
        return 50.0
    try:
        return max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return 50.0


def _combined_score(edge, contact):
    """Multiplicative composite that rewards being good at both signals."""
    return _normalize_edge(edge) * _normalize_contact(contact)


# ─── Candidate fetch ──────────────────────────────────────────────────────────

def fetch_candidates(date_iso):
    """Fetch HR projections for today's games above the prob floor."""
    games_res = sb.table("games") \
        .select("id, away_team_id, home_team_id, "
                "away_team:teams!games_away_team_id_fkey(abbrev), "
                "home_team:teams!games_home_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .not_.in_("status", ["Final", "Game Over", "Completed Early", "In Progress"]) \
        .execute()
    games = games_res.data or []
    if not games:
        return []
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}

    proj_res = sb.table("projections") \
        .select("id, game_id, player_id, market, projected_prob, no_vig_prob, edge, "
                "best_american_odds, best_book, model_version") \
        .in_("game_id", game_ids) \
        .eq("market", HR_MARKET) \
        .gte("projected_prob", MIN_PROJECTED_PROB) \
        .not_.is_("edge", "null") \
        .not_.is_("best_american_odds", "null") \
        .execute()
    projections = proj_res.data or []
    if not projections:
        return []

    player_ids = list({p["player_id"] for p in projections})
    player_res = sb.table("players") \
        .select("id, mlbam_id, name, team_id") \
        .in_("id", player_ids) \
        .execute()
    players_by_id = {p["id"]: p for p in (player_res.data or [])}

    season = datetime.now(timezone.utc).year
    contact_by_player = fetch_contact_scores(player_ids, season)

    enriched = []
    for p in projections:
        game = games_by_id.get(p["game_id"])
        player = players_by_id.get(p["player_id"])
        if not game or not player:
            continue
        is_home = player.get("team_id") == game.get("home_team_id")
        own_abbrev = (game["home_team"] if is_home else game["away_team"])["abbrev"]
        opp_abbrev = (game["away_team"] if is_home else game["home_team"])["abbrev"]
        contact = contact_by_player.get(p["player_id"])
        combined = _combined_score(p["edge"], contact)
        enriched.append({
            "game_id": p["game_id"],
            "player_id": p["player_id"],
            "player_mlbam_id": player.get("mlbam_id"),
            "player_name": player["name"],
            "team_abbrev": own_abbrev,
            "opponent_abbrev": opp_abbrev,
            "market": p["market"],
            "projected_prob": p["projected_prob"],
            "no_vig_prob": p["no_vig_prob"],
            "edge": p["edge"],
            "american_odds": p["best_american_odds"],
            "book": p["best_book"],
            "model_version": p["model_version"],
            "contact_score": contact,
            "combined_score": round(combined, 1),
        })
    enriched.sort(key=lambda x: x["combined_score"], reverse=True)
    return enriched


def main():
    today = get_today_iso()
    log.info("🧅 POD picker — slate %s", today)

    if existing_pod_for(today):
        log.info("POD already exists for %s. Nothing to do.", today)
        return

    candidates = fetch_candidates(today)
    if not candidates:
        log.warning("No qualifying HR projections for %s (need >= %.2f projected_prob with odds).",
                    today, MIN_PROJECTED_PROB)
        return

    # Log top 3 candidates so we have audit visibility into close calls
    log.info("Top candidates by combined edge × contact:")
    for i, c in enumerate(candidates[:3], 1):
        cs = c["contact_score"]
        cs_str = f"{cs:.0f}" if cs is not None else "—"
        log.info("  #%d  %s (%s vs %s)  proj %.1f%%  odds %+d  edge %.3f  contact %s  combined %.1f",
                 i, c["player_name"], c["team_abbrev"], c["opponent_abbrev"],
                 100 * float(c["projected_prob"]), c["american_odds"], float(c["edge"]),
                 cs_str, c["combined_score"])

    pick = candidates[0]
    sb.table("pods").insert({
        "pod_date": today,
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
        "combined_score": pick["combined_score"],
        "stake": 10.00,
        "status": "pending",
    }).execute()
    log.info("✓ POD locked for %s", today)


if __name__ == "__main__":
    main()
