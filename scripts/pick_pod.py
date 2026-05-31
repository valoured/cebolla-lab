"""
pick_pod.py — Daily Play of the Day selection (HR + HRR markets).

PHASE 1 — MATCHUP-FIRST ANCHOR (replaces v0.4.0 tier/confidence)
════════════════════════════════════════════════════════════════
Selects ONE pick per market_class per slate. The selection algorithm is
matchup-first, not edge-first:

  1. Fetch projections for today's HR + HRR markets.
  2. For each batter (market-agnostic), evaluate:
       · Hard exclusion: season_pa < 50.
       · Gate A (opportunity): season_pa >= 120 AND
         family-summed pitch_type_pa >= 20.
       · Gate B (matchup exception): bvp.ab >= 8
         AND (bvp.hr >= 2 OR (bvp.avg >= .300 AND bvp.hr >= 1))
         AND (season barrel_pct >= 8 OR season xslg >= .430).
     Pass A OR B → eligible.
  3. Compute primary_signal = max of three components:
       observed_vs_pitcher    bvp.hr/bvp.ab           (gated bvp.ab >= 8)
       observed_vs_pitch_type by_pitch[primary].hr_pct (gated family pa >= 20)
       recent_power_form      L7 xSLG / 2.0 (or L14 fallback)
     Source label (which component won) persisted for audit.
  4. Rank ANCHORS by primary_signal DESC (max-edge DESC tiebreak).
  5. For each market, walk the ranked anchors and materialize the first
     pick whose market projection passes the EV screen:
       edge >= 0.03         "full"
       0.0 <= edge < 0.03   "drop"        (suggested_stake_tier bumped worse)
       -0.10 <= edge < 0.0  "warn_drop"   (drop + warning flag)
       edge < -0.10         "disqualify"  (pick rejected, fall to next anchor)
     The same batter typically anchors both HR and HRR.

Heat is REMOVED from the picker (combined_tier / combined_trend / FROZEN
filter). compute_batter_trends.py keeps running for the frontend.

The v1/v2 picker code paths (tier1/tier2 gates, confidence_score,
catcher boost, near-miss boost, user flags, vulnerable stacks) are
dropped from this file but their tier_system.py implementations remain
in place for a clean revert.

PERSISTED COLUMNS (Phase 1, added in sql/28):
  primary_signal, primary_signal_source, suggested_stake_tier, phase1_metadata
  Back-compat: primary_signal is ALSO written to pods.confidence_score
  until the frontend cuts over to reading primary_signal explicitly.

Run order:
  pull_schedule → pull_savant → pull_arsenals → pull_pitcher_stats →
  pull_lineups → pull_bvp → pull_weather → pull_dk_odds →
  compute_projections → compute_batter_trends (still runs for FE) → pick_pod
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client
from dotenv import load_dotenv

from tier_system import (
    # Phase 1 primitives
    evaluate_eligibility,
    compute_primary_signal_v3,
    compute_recency_dampener,
    apply_ev_screen,
    suggested_stake_tier_for,
    pitch_family_for,
    # Reused infra
    primary_pitch_type,
    load_thresholds,
    configure,
    _cfg_num,
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


# ─── Market definitions & retained sanity floor ───────────────────────────────

# CRITICAL: this string MUST match compute_projections.py's MODEL_VERSION.
# Mismatch causes picker to find ZERO candidates → publish nothing (safe but
# silent failure mode — check logs for "No projections" if picks vanish).
REQUIRED_MODEL_VERSION = "v0.4.0"

HR_MARKET = "hr_anytime"
HRR_MARKETS = ["h_r_rbi_1.5", "h_r_rbi_2.5", "h_r_rbi_3.5"]

# Per-line probability floor retained as a sanity filter on HRR projections.
# The Phase 1 EV screen handles negative-edge picks via demote/disqualify, but
# this guards against shipping an HRR pick whose projected_prob is way below
# the line baseline (e.g. "1+ hits @ 8%"), which is almost always a data issue
# rather than a real edge.
HRR_MIN_PROB_BY_LINE = {
    "h_r_rbi_1.5": 0.40,
    "h_r_rbi_2.5": 0.20,
    "h_r_rbi_3.5": 0.07,
}

# ── Stake sizing (tier_v1, sql/30) — mirrors pick_cards.TIER_STAKE ────────
# Conviction-tier dollars; 1U = $100 on a $10k bankroll. settle reads
# pods.stake as REAL DOLLARS, so these are dollars not units. A Lock POD →
# $200 (2U), Safe → $100 (1U), etc. None/unknown tier → risky fallback.
TIER_STAKE = {
    "lock":     200.00,
    "safe":     100.00,
    "risky":     25.00,
    "lottery":   10.00,
    "donation":   5.00,
}
TIER_STAKE_FALLBACK = 25.00          # None/unknown tier → risky (0.25U)
STAKE_FRAMEWORK = "tier_v1"


# ─── Date helpers ─────────────────────────────────────────────────────────────

def get_today_iso():
    """ET-relative slate date (matches the rest of the pipeline)."""
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


# ─── Fetchers ─────────────────────────────────────────────────────────────────

def existing_pod_for(date_iso, market_class):
    res = sb.table("pods").select("id") \
        .eq("pod_date", date_iso) \
        .eq("market_class", market_class) \
        .execute()
    return bool(res.data)


def fetch_today_games(date_iso):
    """Today's games not yet started/final, with team abbrevs and probable pitchers."""
    res = sb.table("games") \
        .select("id, home_team_id, away_team_id, home_pitcher_id, away_pitcher_id, "
                "home_team:teams!games_home_team_id_fkey(abbrev), "
                "away_team:teams!games_away_team_id_fkey(abbrev)") \
        .eq("game_date", date_iso) \
        .not_.in_("status", ["Final", "Game Over", "Completed Early", "In Progress"]) \
        .execute()
    return res.data or []


def fetch_starting_pitcher_for_game(games):
    """
    Build {(game_id, team_id): pitcher_id} from the games table, keyed by the
    team the pitcher pitches FOR. To find the opposing pitcher of a batter,
    look up the batter's OPPONENT team in this dict.
    """
    out = {}
    for g in games:
        home_pid = g.get("home_pitcher_id")
        away_pid = g.get("away_pitcher_id")
        if home_pid:
            out[(g["id"], g["home_team_id"])] = home_pid
        if away_pid:
            out[(g["id"], g["away_team_id"])] = away_pid
    return out


def fetch_batter_l14_stats(player_ids, season):
    """
    L14 batter_stats — used as the L7 fallback for the recent_power_form
    component of primary_signal_v3 (when L7 PA is below threshold or L7 row
    is missing).
    """
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, xslg, hit_per_pa, xba") \
        .eq("season", season) \
        .eq("window_type", "l14") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_batter_l7_stats(player_ids, season):
    """
    L7 batter_stats — primary input for recent_power_form (L7 xSLG / 2.0).
    pull_savant.py writes l7 alongside l14/l30/season on every run.
    """
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, xslg, hit_per_pa") \
        .eq("season", season) \
        .eq("window_type", "l7") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_recent_game_logs(player_ids, today_iso, lookback_days=21):
    """
    Path B (recency dampener): per-batter recent game lines from batter_game_log,
    grouped {player_id: [rows]} sorted MOST-RECENT FIRST. Each row carries
    total_bases + ab so compute_recency_dampener can build last-3 vs last-7 SLG.

    Only games the batter actually played (pa > 0) are returned — DNPs would
    otherwise occupy a "last N games" slot with no contact. A 21-day window
    comfortably covers 7 games for an everyday hitter (slack for off-days);
    extra rows are harmless (the dampener slices to the first 7). Paginated
    because the slate-wide pull can exceed PostgREST's 1000-row default cap.
    """
    if not player_ids:
        return {}
    start = (datetime.fromisoformat(today_iso).date()
             - timedelta(days=lookback_days)).isoformat()
    out = {}
    page = 0
    PAGE = 1000
    while True:
        res = sb.table("batter_game_log") \
            .select("batter_id, game_date, ab, total_bases, pa") \
            .in_("batter_id", player_ids) \
            .gte("game_date", start) \
            .gt("pa", 0) \
            .order("game_date", desc=True) \
            .range(page * PAGE, page * PAGE + PAGE - 1) \
            .execute()
        rows = res.data or []
        for r in rows:                      # global desc order → per-player desc preserved
            out.setdefault(r["batter_id"], []).append(r)
        if len(rows) < PAGE:
            break
        page += 1
    return out


def fetch_recent_pod_losses(date_iso):
    """
    Path A (consecutive-loss suppression): player_ids whose POD in a given
    market_class LOST on EITHER of the prior 2 slate days. Returns
    {"hr": set(player_ids), "hrr": set(player_ids)}.

    Yordan-style guard: after a POD loses, the same player is barred from being
    POD in that same market_class for the next 2 days — long enough to span the
    common case where a player is POD one day, misses, sits/loses, and the stale
    single-source signal would re-nominate him the very next slate.
    """
    d = datetime.fromisoformat(date_iso).date()
    prior_days = [(d - timedelta(days=1)).isoformat(),
                  (d - timedelta(days=2)).isoformat()]
    res = sb.table("pods") \
        .select("player_id, market_class, pod_date, status") \
        .in_("pod_date", prior_days) \
        .eq("status", "loss") \
        .execute()
    out = {"hr": set(), "hrr": set()}
    for r in (res.data or []):
        mc = r.get("market_class")
        pid = r.get("player_id")
        if mc in out and pid is not None:
            out[mc].add(pid)
    return out


def fetch_batter_season_stats(player_ids, season):
    """
    Season-window batter_stats — drives both Gate A (season_pa, family-summed
    pitch_type_pa via by_pitch_type) and Gate B (season barrel_pct, xslg power
    floors). by_pitch_type also feeds observed_vs_pitch_type in primary_signal.
    """
    if not player_ids:
        return {}
    res = sb.table("batter_stats") \
        .select("batter_id, pa, barrel_pct, xslg, xba, hit_per_pa, by_pitch_type") \
        .eq("season", season) \
        .eq("window_type", "season") \
        .eq("vs_hand", "A") \
        .in_("batter_id", player_ids) \
        .execute()
    return {r["batter_id"]: r for r in (res.data or [])}


def fetch_pitcher_arsenals(pitcher_ids, season):
    """
    Pitcher arsenals across both stances. primary_pitch_type() in tier_system
    sums usage_pct across stances to pick the overall most-thrown pitch.
    """
    if not pitcher_ids:
        return {}
    res = sb.table("pitcher_arsenals") \
        .select("pitcher_id, pitch_type, vs_stance, usage_pct, hr_pct") \
        .eq("season", season) \
        .eq("window_type", "season") \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    out = {}
    for r in res.data or []:
        out.setdefault(r["pitcher_id"], []).append(r)
    return out


def fetch_bvp(batter_ids, pitcher_ids):
    """
    Career BvP per (batter, pitcher). Expanded vs prior versions to include
    ab/hits/hr — required by Gate B (matchup exception) and by the
    observed_vs_pitcher component of primary_signal_v3.
    """
    if not batter_ids or not pitcher_ids:
        return {}
    res = sb.table("bvp_history") \
        .select("batter_id, pitcher_id, pa, ab, hits, hr, avg, ops") \
        .in_("batter_id", batter_ids) \
        .in_("pitcher_id", pitcher_ids) \
        .execute()
    return {(r["batter_id"], r["pitcher_id"]): r for r in (res.data or [])}


# ─── Phase 1 forensics helpers ────────────────────────────────────────────────

def _phase1_signal_components(bvp_row, by_pitch, pitcher_primary,
                              l7_stats, l14_stats, cfg):
    """
    Capture the three raw primary-signal component values + power-form source
    for the phase1_metadata.primary_signal_components forensics dict. None
    means the component was unavailable or below its reliability gate. Mirrors
    the gating in compute_primary_signal_v3 — kept here separately so we can
    report ALL three values regardless of which won the max.
    """
    bvp_v = None
    if bvp_row:
        try:
            ab = int(bvp_row.get("ab") or 0)
            hr = int(bvp_row.get("hr") or 0)
            if ab >= int(_cfg_num(cfg, "primary_bvp_ab_min", 8)) and ab > 0:
                bvp_v = round(hr / ab, 5)
        except (TypeError, ValueError):
            pass

    pt_v = None
    if pitcher_primary and by_pitch:
        entry = by_pitch.get(pitcher_primary)
        family = pitch_family_for(pitcher_primary)
        if entry and isinstance(entry, dict) and family:
            fam_pa = 0
            for label, sub in by_pitch.items():
                if pitch_family_for(label) != family or not isinstance(sub, dict):
                    continue
                try:
                    fam_pa += int(sub.get("pa") or 0)
                except (TypeError, ValueError):
                    continue
            if fam_pa >= int(_cfg_num(cfg, "primary_pitch_type_pa_min", 20)) \
               and entry.get("hr_pct") is not None:
                try:
                    pt_v = round(float(entry["hr_pct"]) / 100.0, 5)
                except (TypeError, ValueError):
                    pass

    divisor = _cfg_num(cfg, "primary_l7_xslg_divisor", 2.0)
    pf_v = None
    pf_source = None
    if l7_stats and l7_stats.get("xslg") is not None:
        try:
            if int(l7_stats.get("pa") or 0) >= int(_cfg_num(cfg, "primary_l7_pa_min", 10)) \
               and divisor:
                pf_v = round(float(l7_stats["xslg"]) / divisor, 5)
                pf_source = "l7"
        except (TypeError, ValueError):
            pass
    if pf_v is None and l14_stats and l14_stats.get("xslg") is not None and divisor:
        try:
            pf_v = round(float(l14_stats["xslg"]) / divisor, 5)
            pf_source = "l14"
        except (TypeError, ValueError):
            pass

    return {
        "bvp_observed": bvp_v,
        "pitch_type_observed": pt_v,
        "power_form": pf_v,
        "power_form_source": pf_source,
    }


def _log_signal_distribution(anchors, cfg=None):
    """
    Locked-decision audit log — print distribution stats across the enriched
    anchor pool so night-1 review doesn't require DB queries. Stake-tier
    counts use the BASE breakpoints (no EV demote — that's per-pick).
    """
    if not anchors:
        log.info("Signal distribution: empty pool")
        return
    sigs = sorted(float(a.get("primary_signal") or 0.0) for a in anchors)
    n = len(sigs)
    s_min, s_max = sigs[0], sigs[-1]
    if n % 2:
        s_med = sigs[n // 2]
    else:
        s_med = (sigs[n // 2 - 1] + sigs[n // 2]) / 2

    lock_min    = _cfg_num(cfg, "stake_tier_lock_min", 0.65)
    safe_min    = _cfg_num(cfg, "stake_tier_safe_min", 0.50)
    risky_min   = _cfg_num(cfg, "stake_tier_risky_min", 0.30)
    lottery_min = _cfg_num(cfg, "stake_tier_lottery_min", 0.15)

    counts = {"lock": 0, "safe": 0, "risky": 0, "lottery": 0, "donation": 0}
    for s in sigs:
        if s >= lock_min:      counts["lock"]    += 1
        elif s >= safe_min:    counts["safe"]    += 1
        elif s >= risky_min:   counts["risky"]   += 1
        elif s >= lottery_min: counts["lottery"] += 1
        else:                  counts["donation"] += 1

    src_counts = {}
    for a in anchors:
        src = a.get("primary_signal_source") or "none"
        src_counts[src] = src_counts.get(src, 0) + 1

    log.info("Signal distribution: n=%d  min=%.3f  med=%.3f  max=%.3f",
             n, s_min, s_med, s_max)
    log.info("  by stake tier: lock=%d safe=%d risky=%d lottery=%d donation=%d",
             counts["lock"], counts["safe"], counts["risky"],
             counts["lottery"], counts["donation"])
    log.info("  by source:     bvp_observed=%d  pitch_type_observed=%d  "
             "l7_power_form=%d  l14_power_form=%d  none=%d",
             src_counts.get("bvp_observed", 0),
             src_counts.get("pitch_type_observed", 0),
             src_counts.get("l7_power_form", 0),
             src_counts.get("l14_power_form", 0),
             src_counts.get("none", 0))


# ─── Anchor enrichment + market materialization ──────────────────────────────

def _enrich_anchor(player_id, game_id, games_by_id, players_by_id,
                   batter_stats_l14, batter_stats_l7, batter_stats_season,
                   pitcher_arsenals, bvp_pairs, starter_by_game_team,
                   game_log_by_player, cfg):
    """
    Market-agnostic anchor enrichment. Computes eligibility + primary_signal
    + per-component forensics from the batter's stats vs the opposing pitcher.
    Returns None on hard exclusion or ineligibility. The EV screen + per-market
    odds context are applied later in _materialize_pick.
    """
    game = games_by_id.get(game_id)
    player = players_by_id.get(player_id)
    if not game or not player:
        return None

    is_home = player.get("team_id") == game.get("home_team_id")
    opposing_team_id = game.get("away_team_id") if is_home else game.get("home_team_id")
    opposing_pitcher_id = starter_by_game_team.get((game_id, opposing_team_id))

    pitcher_primary = None
    if opposing_pitcher_id:
        arsenal = pitcher_arsenals.get(opposing_pitcher_id) or []
        pitcher_primary = primary_pitch_type(arsenal)

    bstats_season = batter_stats_season.get(player_id)
    by_pitch = (bstats_season or {}).get("by_pitch_type") if bstats_season else None
    bvp_row = bvp_pairs.get((player_id, opposing_pitcher_id)) if opposing_pitcher_id else None
    l14_stats = batter_stats_l14.get(player_id)
    l7_stats = batter_stats_l7.get(player_id)

    passed, gate, elig_detail = evaluate_eligibility(
        bstats_season, by_pitch, pitcher_primary, bvp_row, cfg=cfg
    )
    if not passed:
        return None

    dampener = compute_recency_dampener(player_id, game_log_by_player, cfg=cfg)
    signal, source = compute_primary_signal_v3(
        bvp_row, by_pitch, pitcher_primary, l7_stats, l14_stats, cfg=cfg,
        recency_dampener=dampener,
    )
    components = _phase1_signal_components(
        bvp_row, by_pitch, pitcher_primary, l7_stats, l14_stats, cfg
    )

    own_abbrev = (game["home_team"] if is_home else game["away_team"])["abbrev"]
    opp_abbrev = (game["away_team"] if is_home else game["home_team"])["abbrev"]

    return {
        "player_id": player_id,
        "player_mlbam_id": player.get("mlbam_id"),
        "player_name": player["name"],
        "game_id": game_id,
        "team_abbrev": own_abbrev,
        "opponent_abbrev": opp_abbrev,
        "primary_signal": signal,
        "primary_signal_source": source,
        "recency_dampener": dampener,
        "gate": gate,
        "eligibility_detail": elig_detail,
        "signal_components": components,
        "opposing_pitcher_id": opposing_pitcher_id,
        "pitcher_primary_pitch_type": pitcher_primary,
    }


def _materialize_pick(anchor, proj, market, cfg=None):
    """
    Apply the EV screen + per-market odds context to a (anchor, projection)
    pair. Returns None if the market's edge disqualifies; the caller falls
    through to the next anchor for that market.
    """
    edge_val = proj.get("edge")
    ev_action, ev_warning = apply_ev_screen(edge_val, cfg=cfg)
    if ev_action == "disqualify":
        return None

    stake_tier = suggested_stake_tier_for(
        anchor.get("primary_signal") or 0.0, ev_action, cfg=cfg
    )

    projected = proj.get("projected_prob")
    american = proj.get("best_american_odds")
    ev_per_dollar = None
    if projected is not None and american is not None:
        try:
            pp = float(projected)
            am = int(american)
            decimal = (am / 100.0 + 1.0) if am > 0 else (100.0 / abs(am) + 1.0)
            ev_per_dollar = pp * (decimal - 1.0) - (1.0 - pp)
        except (TypeError, ValueError):
            pass

    phase1_metadata = {
        "gate": anchor.get("gate"),
        "ev_action": ev_action,
        "ev_warning": ev_warning,
        "eligibility_detail": anchor.get("eligibility_detail"),
        "primary_signal_components": anchor.get("signal_components"),
        "primary_signal_source": anchor.get("primary_signal_source"),
        "recency_dampener": anchor.get("recency_dampener"),
    }

    return {
        "game_id": anchor["game_id"],
        "player_id": anchor["player_id"],
        "player_mlbam_id": anchor.get("player_mlbam_id"),
        "player_name": anchor["player_name"],
        "team_abbrev": anchor.get("team_abbrev"),
        "opponent_abbrev": anchor.get("opponent_abbrev"),
        "market": market,
        "projected_prob": projected,
        "no_vig_prob": proj.get("no_vig_prob"),
        "edge": edge_val,
        "ev_per_dollar": ev_per_dollar,
        "american_odds": proj.get("best_american_odds"),
        "book": proj.get("best_book"),
        "model_version": proj.get("model_version"),
        "primary_signal": anchor.get("primary_signal"),
        "primary_signal_source": anchor.get("primary_signal_source"),
        "gate": anchor.get("gate"),
        "ev_action": ev_action,
        "ev_warning": ev_warning,
        "suggested_stake_tier": stake_tier,
        "phase1_metadata": phase1_metadata,
    }


# ─── Unified anchor + market selection ───────────────────────────────────────

def select_anchors_and_market_picks(
    date_iso, games, players_by_id,
    batter_stats_l14, batter_stats_l7, batter_stats_season,
    pitcher_arsenals, bvp_pairs, starter_by_game_team,
    game_log_by_player, cfg
):
    """
    Phase 1 unified-anchor selection. Returns {"hr": pick|None, "hrr": pick|None}.

    One enrichment pass per batter computes market-agnostic eligibility +
    primary_signal. Anchors rank by primary_signal DESC (max-edge DESC tiebreak).
    For each market, walks the ranked anchor list and materializes the first
    pick whose market projection passes the EV screen. The same anchor usually
    serves both markets; if the top anchor lacks a market's projection — or
    fails its EV screen — the next anchor takes that market.
    """
    if not games:
        return {"hr": None, "hrr": None}
    game_ids = [g["id"] for g in games]
    games_by_id = {g["id"]: g for g in games}

    # ── Pull all projection rows for HR + HRR markets ──
    # No projected_prob or edge floors here (Phase 1 EV screen handles weak
    # picks via demote/disqualify in _materialize_pick). HRR per-line prob
    # floor is applied below (sanity).
    proj_res = sb.table("projections") \
        .select("game_id, player_id, market, projected_prob, no_vig_prob, edge, "
                "best_american_odds, best_book, model_version") \
        .in_("game_id", game_ids) \
        .in_("market", [HR_MARKET] + HRR_MARKETS) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .not_.is_("best_american_odds", "null") \
        .execute()
    projections = proj_res.data or []

    # ── Group projections per batter, per market_class ──
    hr_by_player = {}
    hrr_by_player = {}
    for p in projections:
        pid = p["player_id"]
        mkt = p["market"]
        if mkt == HR_MARKET:
            hr_by_player[pid] = p
        elif mkt in HRR_MARKETS:
            floor = HRR_MIN_PROB_BY_LINE.get(mkt)
            if floor is None or (p.get("projected_prob") or 0) < floor:
                continue
            existing = hrr_by_player.get(pid)
            # Collapse to best line per batter by (edge, prob), like v0.4.0.
            new_key = (float(p["edge"] or 0), float(p["projected_prob"] or 0))
            if existing is None:
                hrr_by_player[pid] = p
            else:
                old_key = (float(existing["edge"] or 0),
                           float(existing["projected_prob"] or 0))
                if new_key > old_key:
                    hrr_by_player[pid] = p

    candidate_pids = set(hr_by_player) | set(hrr_by_player)
    log.info("Projection pool: %d distinct batter(s) with HR or HRR projections",
             len(candidate_pids))

    # ── Enrich anchors (market-agnostic) ──
    anchors = []
    errors_by_type = {}
    for pid in candidate_pids:
        rep = hr_by_player.get(pid) or hrr_by_player.get(pid)
        try:
            anchor = _enrich_anchor(
                pid, rep["game_id"],
                games_by_id, players_by_id,
                batter_stats_l14, batter_stats_l7, batter_stats_season,
                pitcher_arsenals, bvp_pairs, starter_by_game_team,
                game_log_by_player, cfg=cfg,
            )
        except Exception as e:
            # Per-candidate skip (don't let one batter blow up the run); caught
            # at the call site so real bugs still surface per-candidate.
            etype = type(e).__name__
            errors_by_type[etype] = errors_by_type.get(etype, 0) + 1
            log.warning("Anchor enrich skipped player_id=%s: %s: %s",
                        pid, etype, e)
            continue
        if anchor is None:
            continue
        # Attach max edge across this batter's market projections for tiebreak.
        edges = []
        if pid in hr_by_player and hr_by_player[pid].get("edge") is not None:
            try: edges.append(float(hr_by_player[pid]["edge"]))
            except (TypeError, ValueError): pass
        if pid in hrr_by_player and hrr_by_player[pid].get("edge") is not None:
            try: edges.append(float(hrr_by_player[pid]["edge"]))
            except (TypeError, ValueError): pass
        anchor["max_edge"] = max(edges) if edges else 0.0
        anchors.append(anchor)

    if errors_by_type:
        log.warning("Phase 1: %d anchor(s) skipped due to enrichment errors: %s",
                    sum(errors_by_type.values()), errors_by_type)
    log.info("Eligible anchors after Phase 1 gates: %d", len(anchors))

    # ── Rank: primary_signal DESC, max_edge DESC tiebreak ──
    anchors.sort(
        key=lambda a: ((a.get("primary_signal") or 0.0),
                       (a.get("max_edge") or 0.0)),
        reverse=True,
    )

    # ── Distribution + top-3 logs ──
    _log_signal_distribution(anchors, cfg=cfg)
    log_top3("anchor", anchors)

    # ── Path A: consecutive-loss POD suppression (per market_class) ──
    # A player whose POD in this market lost on either of the prior 2 days is
    # barred from being POD here today — the stale single-source signal keeps
    # re-nominating a cold bat (Yordan: 3 straight HR-POD losses) otherwise.
    suppressed = fetch_recent_pod_losses(date_iso)
    for mc in ("hr", "hrr"):
        if suppressed[mc]:
            names = sorted(players_by_id.get(pid, {}).get("name", str(pid))
                           for pid in suppressed[mc])
            log.info("Suppressed %d player(s) from %s POD for consecutive losses "
                     "(prior 2 days): %s", len(suppressed[mc]), mc.upper(), names)

    # ── Materialize HR pick (walk anchors until one passes EV screen) ──
    hr_pick = None
    hr_skipped = 0
    hr_suppressed = 0
    for anchor in anchors:
        if anchor["player_id"] in suppressed["hr"]:
            hr_suppressed += 1
            continue
        proj = hr_by_player.get(anchor["player_id"])
        if not proj:
            continue
        pick = _materialize_pick(anchor, proj, HR_MARKET, cfg=cfg)
        if pick is None:
            hr_skipped += 1
            continue
        hr_pick = pick
        break

    # ── Materialize HRR pick (walk anchors until one passes EV screen) ──
    hrr_pick = None
    hrr_skipped = 0
    hrr_suppressed = 0
    for anchor in anchors:
        if anchor["player_id"] in suppressed["hrr"]:
            hrr_suppressed += 1
            continue
        proj = hrr_by_player.get(anchor["player_id"])
        if not proj:
            continue
        pick = _materialize_pick(anchor, proj, proj["market"], cfg=cfg)
        if pick is None:
            hrr_skipped += 1
            continue
        hrr_pick = pick
        break

    if hr_skipped:
        log.info("HR: %d anchor(s) skipped by EV screen before pick locked", hr_skipped)
    if hrr_skipped:
        log.info("HRR: %d anchor(s) skipped by EV screen before pick locked", hrr_skipped)
    if hr_suppressed:
        log.info("HR: %d anchor(s) skipped by consecutive-loss suppression", hr_suppressed)
    if hrr_suppressed:
        log.info("HRR: %d anchor(s) skipped by consecutive-loss suppression", hrr_suppressed)
    if hr_pick is None and hr_suppressed:
        log.warning("HR: no anchor survived suppression — no HR POD today.")
    if hrr_pick is None and hrr_suppressed:
        log.warning("HRR: no anchor survived suppression — no HRR POD today.")

    return {"hr": hr_pick, "hrr": hrr_pick}


# ─── Logging + insertion ─────────────────────────────────────────────────────

def log_top3(label, items):
    """Top 3 by primary_signal (then edge). Works for both anchors and materialized picks."""
    log.info("Top %s by primary_signal (edge tiebreak):", label)
    for i, c in enumerate(items[:3], 1):
        sig = c.get("primary_signal")
        sig_str = f"{sig:.3f}" if sig is not None else "—"
        src = c.get("primary_signal_source") or "—"
        gate = c.get("gate") or "—"
        edge = c.get("edge") if c.get("edge") is not None else c.get("max_edge")
        edge_str = f"{float(edge):.3f}" if edge is not None else "—"
        ev_action = c.get("ev_action") or "—"
        stake = c.get("suggested_stake_tier") or "—"
        log.info(
            "  #%d  %s (%s vs %s)  sig=%s [%s, gate %s]  edge=%s  ev=%s  stake=%s",
            i,
            c.get("player_name"),
            c.get("team_abbrev") or "?",
            c.get("opponent_abbrev") or "?",
            sig_str, src, gate, edge_str, ev_action, stake,
        )


def insert_pod(pick, date_iso, market_class):
    """
    Insert a Phase 1 POD row. Writes:
      - core pick fields + Phase 1 columns (primary_signal,
        primary_signal_source, suggested_stake_tier, phase1_metadata).
      - back-compat dual-write of primary_signal into confidence_score so the
        existing frontend keeps rendering until it migrates to read
        primary_signal explicitly. confidence_tier left NULL — the v2 letter
        mapping was derived from a different distribution, populating it now
        would skew historical filters.

    v1/v2 columns left unset (and thus NULL): combined_score, tier1_hits,
    tier2_hits, tier_score, stake_modifier, tier_metadata, confidence_tier,
    market_context, contact_score.
    """
    sig = pick.get("primary_signal")
    stake = TIER_STAKE.get(pick.get("suggested_stake_tier"), TIER_STAKE_FALLBACK)
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
        # Phase 1 columns
        "primary_signal": sig,
        "primary_signal_source": pick.get("primary_signal_source"),
        "suggested_stake_tier": pick.get("suggested_stake_tier"),
        "phase1_metadata": pick.get("phase1_metadata"),
        # Stake-sizing regime (migration 30). tier_v1 = conviction-tier dollars.
        "stake_framework": STAKE_FRAMEWORK,
        # Back-compat dual write
        "confidence_score": sig,
        "stake": stake,
        "status": "pending",
    }).execute()


def pick_for_market(date_iso, market_class, pick):
    if existing_pod_for(date_iso, market_class):
        log.info("[%s] POD already exists for %s. Skipping.",
                 market_class.upper(), date_iso)
        return
    if not pick:
        log.warning("[%s] No anchor qualifies for %s. Publishing nothing (conviction signal).",
                    market_class.upper(), date_iso)
        return
    insert_pod(pick, date_iso, market_class)
    odds = pick.get("american_odds")
    odds_str = f"{int(odds):+d}" if odds is not None else "—"
    edge = pick.get("edge")
    edge_str = f"{float(edge):.3f}" if edge is not None else "—"
    sig = pick.get("primary_signal")
    sig_str = f"{sig:.3f}" if sig is not None else "—"
    log.info(
        "[%s] ✓ POD locked: %s @ %s  sig=%s [%s, gate %s]  edge=%s  ev=%s  stake=%s",
        market_class.upper(),
        pick.get("player_name"),
        odds_str, sig_str,
        pick.get("primary_signal_source") or "—",
        pick.get("gate") or "—",
        edge_str,
        pick.get("ev_action") or "—",
        pick.get("suggested_stake_tier") or "—",
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = get_today_iso()
    log.info("🧅 POD picker — slate %s (Phase 1: matchup-first anchor)", today)

    # ── Idempotency gate ────────────────────────────────────────────────
    existing = sb.table("pods").select("id, market_class") \
        .eq("pod_date", today) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .execute()
    if existing.data:
        markets = {row.get("market_class", "hr") for row in existing.data}
        log.info("POD already locked for %s (model %s, markets=%s). Skipping.",
                 today, REQUIRED_MODEL_VERSION, sorted(markets))
        return

    # ── Threshold cache (defaults fall through if model_thresholds unreachable) ──
    try:
        cfg = load_thresholds(sb)
        configure(cfg)
        log.info("Loaded %d thresholds from model_thresholds.", len(cfg))
    except Exception as e:
        cfg = {}
        log.warning("model_thresholds load failed (%s) — using _cfg_num defaults.", e)

    games = fetch_today_games(today)
    if not games:
        log.warning("No games scheduled for %s.", today)
        return

    game_ids = [g["id"] for g in games]
    season = datetime.now(timezone.utc).year

    # ── Distinct projection batters ──
    proj_ids_res = sb.table("projections") \
        .select("player_id") \
        .in_("game_id", game_ids) \
        .in_("market", [HR_MARKET] + HRR_MARKETS) \
        .eq("model_version", REQUIRED_MODEL_VERSION) \
        .execute()
    all_proj_player_ids = list({p["player_id"] for p in (proj_ids_res.data or [])})
    if not all_proj_player_ids:
        log.warning("No projections for %s.", today)
        return

    # ── Player metadata ──
    player_res = sb.table("players").select("id, mlbam_id, name, team_id, position") \
        .in_("id", all_proj_player_ids).execute()
    players_by_id = {p["id"]: p for p in (player_res.data or [])}

    log.info("Fetching Phase 1 data for %d candidate players...",
             len(all_proj_player_ids))

    # ── Batter stat windows ──
    batter_stats_l14 = fetch_batter_l14_stats(all_proj_player_ids, season)
    log.info("  L14 stats:    %d rows", len(batter_stats_l14))
    batter_stats_l7 = fetch_batter_l7_stats(all_proj_player_ids, season)
    log.info("  L7 stats:     %d rows", len(batter_stats_l7))
    batter_stats_season = fetch_batter_season_stats(all_proj_player_ids, season)
    log.info("  Season stats: %d rows", len(batter_stats_season))
    game_log_by_player = fetch_recent_game_logs(all_proj_player_ids, today)
    log.info("  Game logs:    %d batters w/ recent games", len(game_log_by_player))

    # ── Pitcher matchups ──
    starter_by_game_team = fetch_starting_pitcher_for_game(games)
    pitcher_ids = list({pid for pid in starter_by_game_team.values() if pid})
    log.info("  Starting pitchers: %d", len(pitcher_ids))
    pitcher_arsenals = fetch_pitcher_arsenals(pitcher_ids, season)
    log.info("  Pitcher arsenals:  %d", len(pitcher_arsenals))

    # ── BvP + coverage pre-flight ──
    bvp_pairs = fetch_bvp(all_proj_player_ids, pitcher_ids)
    bvp_batter_count = len({k[0] for k in bvp_pairs})
    coverage_pct = (
        bvp_batter_count / len(all_proj_player_ids) * 100
        if all_proj_player_ids else 0.0
    )
    log.info("  BvP pairs: %d (%d distinct batters with bvp, %.1f%% slate coverage)",
             len(bvp_pairs), bvp_batter_count, coverage_pct)
    if all_proj_player_ids and coverage_pct < 20:
        log.warning(
            "Low bvp coverage for today's slate (%d/%d batters, %.1f%%) — "
            "Gate B may rarely fire.",
            bvp_batter_count, len(all_proj_player_ids), coverage_pct,
        )

    # ── Select anchors + market picks ──
    picks = select_anchors_and_market_picks(
        today, games, players_by_id,
        batter_stats_l14, batter_stats_l7, batter_stats_season,
        pitcher_arsenals, bvp_pairs, starter_by_game_team,
        game_log_by_player, cfg,
    )

    pick_for_market(today, "hr", picks.get("hr"))
    pick_for_market(today, "hrr", picks.get("hrr"))

    log.info("🧅 POD picker complete")


if __name__ == "__main__":
    main()
