"""
pick_v2.py — v2 shadow HR picker (hr_score_v2). Day 5.

Reads CONFIRMED lineups for the slate, computes hr_model.compute_hr_probability
for every confirmed batter, runs slate-level sanity checks, and writes the full
slate to picks_v2 (or picks_v2_rejected on failure). TRACKING ONLY — no stake,
under stop-the-bleed. Idempotent on UNIQUE(pick_date, game_id, player_id,
market, model_version).

CRON: hourly 11:00-19:00 ET — catches each game as its lineup confirms
(confirmed lineups aren't posted at the 3:30 AM pick time). Re-runs upsert.

SANITY GATES:
  per-pick   — hard reject if model_prob_per_game > 0.30 → picks_v2_rejected
               (reason 'per_game_gt_0.30'); per_game_high (>0.20) is a warning.
  slate      — median per_game within SLATE_MEDIAN_BAND, and no batter on >3
               picks. On failure the whole slate is diverted to picks_v2_rejected
               (reason 'slate_*'), nothing is written to picks_v2, logged LOUD.

Usage:
  python pick_v2.py --dry-run [--date YYYY-MM-DD]   # compute + report, NO writes
  python pick_v2.py [--date YYYY-MM-DD]             # prod upsert
"""

import os
import sys
import logging
import statistics as st
from datetime import datetime, timezone, timedelta
from collections import Counter

from supabase import create_client
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v2 import hr_model as HM

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pick_v2")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

MARKET = "hr_anytime"
SLATE_MEDIAN_BAND = (0.085, 0.140)   # drift guard around the ~11% league median
MAX_PER_BATTER = 3


def _today_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _confirmed_lineups(date_iso):
    games = {g["id"]: g for g in sb.table("games").select(
        "id, home_team_id, away_team_id, home_pitcher_id, away_pitcher_id, status")
        .eq("game_date", date_iso).execute().data or []}
    lus = sb.table("lineups").select(
        "game_id, team_id, batting_order, player_id, source, is_confirmed") \
        .in_("game_id", list(games)).execute().data or []
    lus = [l for l in lus if (l.get("is_confirmed") or l.get("source") == "mlb_api")
           and l.get("player_id")]
    return games, lus


def _build_ctx_map(games, lus):
    """Batch-fetch everything compute_hr_probability needs, keyed for ctx."""
    bids = list({l["player_id"] for l in lus})
    pids = list({p for g in games.values()
                 for p in (g["home_pitcher_id"], g["away_pitcher_id"]) if p})
    players = {p["id"]: p for p in sb.table("players").select("id, bats, throws")
               .in_("id", bids + pids).execute().data or []}
    bstats = {r["batter_id"]: r for r in sb.table("batter_stats")
              .select("batter_id, pa, hr_per_pa, pulled_airball_rate")
              .eq("season", 2026).eq("window_type", "season").eq("vs_hand", "A")
              .in_("batter_id", bids).execute().data or []}
    prows = {r["pitcher_id"]: r for r in sb.table("pitcher_stats")
             .select("pitcher_id, innings_pitched, bbe")
             .eq("season", 2026).eq("window_type", "l30")
             .in_("pitcher_id", pids).execute().data or []}
    odds = {}
    for gid in games:
        for r in sb.table("odds_snapshots").select("player_id, american_odds") \
                .eq("game_id", gid).eq("market", "hr_anytime_yes") \
                .eq("is_current", True).execute().data or []:
            odds.setdefault((gid, r["player_id"]), r["american_odds"])
    return players, bstats, prows, odds


def _ctx_for(l, games, players, bstats, prows, odds):
    g = games[l["game_id"]]
    is_home = l["team_id"] == g["home_team_id"]
    opp = g["away_pitcher_id"] if is_home else g["home_pitcher_id"]
    pr = prows.get(opp, {})
    lsrc = "confirmed" if (l.get("is_confirmed") and l.get("source") == "mlb_api") else l.get("source")
    ctx = {
        "bstats": bstats.get(l["player_id"]),
        "batting_order": l.get("batting_order"),
        "lineup_source": lsrc,
        "bats": (players.get(l["player_id"]) or {}).get("bats") or "R",
        "throws": (players.get(opp) or {}).get("throws"),
        "home_team_id": g["home_team_id"],
        "american_odds": odds.get((l["game_id"], l["player_id"])),
        "pitcher_ip": float(pr.get("innings_pitched") or 0),
        "pitcher_bbe": int(pr.get("bbe") or 0),
    }
    return opp, ctx


def _row(p, date_iso, team_id, rejected=False, reason=None):
    base = {
        "pick_date": date_iso, "game_id": p["game_id"], "player_id": p["batter_id"],
        "market": MARKET, "model_version": p["model_version"],
        "model_prob_per_game": p["model_prob_per_game"],
        "components": p["components"], "warnings": p["warnings"],
    }
    if rejected:
        base["reject_reason"] = reason
        return base
    base.update({
        "model_prob_per_pa": p["model_prob_per_pa"],
        "best_american_odds": p["book_odds"], "no_vig_prob": p["no_vig_prob"],
        "edge_pct": p["edge_pct"], "edge_status": p["edge_status"],
        "lineup_source": p["warnings"].get("lineup_source"),
    })
    return base


def main():
    dry = "--dry-run" in sys.argv
    date_iso = _today_iso()
    if "--date" in sys.argv:
        date_iso = sys.argv[sys.argv.index("--date") + 1]
    log.info("🧅 pick_v2 %s — slate %s (model %s, c=%.2f)",
             "DRY-RUN" if dry else "sync", date_iso, HM.MODEL_VERSION, HM.MODEL_INTERCEPT_C)

    games, lus = _confirmed_lineups(date_iso)
    if not lus:
        log.warning("No confirmed lineups for %s — nothing to do (run later once they post).", date_iso)
        return
    players, bstats, prows, odds = _build_ctx_map(games, lus)

    picks, rejected, team_of = [], [], {}
    for l in lus:
        opp, ctx = _ctx_for(l, games, players, bstats, prows, odds)
        r = HM.compute_hr_probability(l["player_id"], l["game_id"], opp, sb=sb, ctx=ctx)
        if r is None:
            continue
        team_of[r["batter_id"]] = l["team_id"]
        if r["hard_reject"]:
            rejected.append((r, "per_game_gt_0.30"))
        else:
            picks.append(r)

    # ── slate-level sanity ──
    slate_fail = None
    if picks:
        med = st.median([p["model_prob_per_game"] for p in picks])
        if not (SLATE_MEDIAN_BAND[0] <= med <= SLATE_MEDIAN_BAND[1]):
            slate_fail = f"slate_median_per_game={med:.3f}_outside_{SLATE_MEDIAN_BAND}"
        worst_bat = Counter(p["batter_id"] for p in picks).most_common(1)
        if worst_bat and worst_bat[0][1] > MAX_PER_BATTER:
            slate_fail = (slate_fail or "") + f" max_per_batter={worst_bat[0][1]}"

    if slate_fail:
        log.error("SLATE SANITY FAILED (%s) — diverting %d picks to picks_v2_rejected, "
                  "writing NOTHING to picks_v2.", slate_fail, len(picks))
        # TODO: wire to alerting pipeline if/when one exists; loud log for now.
        rejected = [(p, slate_fail) for p in picks] + rejected
        picks = []

    # ── report ──
    rated = [p for p in picks if p["edge_pct"] is not None
             and p["edge_status"] != "longshot_unrated"]
    edges = sorted(p["edge_pct"] * 100 for p in rated)
    pgs = sorted(p["model_prob_per_game"] for p in picks) if picks else [0]
    med_pg = st.median(pgs) * 100
    med_e = edges[len(edges) // 2] if edges else 0.0
    sbk = sum(1 for x in edges if x >= 5)
    highs = sum(1 for p in picks if p["warnings"].get("per_game_high"))
    maxc = max((p["components"]["combined_factor"] for p in picks), default=0)
    log.info("Computed: %d picks, %d hard-rejected, %d slate-diverted",
             len(picks), sum(1 for _ in rejected) - (len(picks) if slate_fail else 0), 0)
    log.info("median per_game=%.1f%%  median edge=%+.2f%%  strong_back=%d (%d%%)  "
             "per_game_high=%d  max_combined=%.3f  hard_reject(>0.30)=%d",
             med_pg, med_e, sbk, (100 * sbk // len(picks)) if picks else 0,
             highs, maxc, sum(1 for r, why in rejected if why == "per_game_gt_0.30"))

    if dry:
        # FINAL PRE-WRITE HARNESS: acceptance criteria + named picks
        crit = [("median edge in [-1,+1]", -1 <= med_e <= 1, f"{med_e:+.2f}%"),
                ("strong_back <= 10% slate", sbk <= 0.10 * max(len(picks), 1), f"{sbk}"),
                ("median per_game in [10,12]", 10 <= med_pg <= 12, f"{med_pg:.1f}%"),
                ("hard_reject(>0.30) == 0", sum(1 for r, w in rejected if w == "per_game_gt_0.30") == 0,
                 str(sum(1 for r, w in rejected if w == "per_game_gt_0.30"))),
                ("max combined factor <= 2.10", maxc <= 2.10, f"{maxc:.3f}")]
        log.info("ACCEPTANCE CRITERIA:")
        for n, ok, v in crit:
            log.info("  [%s] %-28s = %s", "PASS" if ok else "FAIL", n, v)
        log.info("DRY-RUN — NO DB writes.")
        return

    # ── write (idempotent upsert) ──
    if picks:
        rows = [_row(p, date_iso, team_of.get(p["batter_id"])) for p in picks]
        for i in range(0, len(rows), 200):
            sb.table("picks_v2").upsert(
                rows[i:i + 200],
                on_conflict="pick_date,game_id,player_id,market,model_version").execute()
        log.info("✓ Wrote %d picks_v2 rows", len(rows))
    if rejected:
        rrows = [_row(p, date_iso, team_of.get(p["batter_id"]), rejected=True, reason=why)
                 for p, why in rejected]
        for i in range(0, len(rrows), 200):
            sb.table("picks_v2_rejected").insert(rrows[i:i + 200]).execute()
        log.info("✓ Wrote %d picks_v2_rejected rows", len(rrows))
    log.info("🧅 pick_v2 complete")


if __name__ == "__main__":
    main()
