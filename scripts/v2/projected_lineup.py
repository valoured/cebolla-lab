"""
projected_lineup.py — derive each team's *typical* lineup from recent history.

WHY THIS EXISTS
  pick_v2 originally waited for CONFIRMED lineups (lineups table, mlb_api
  source) before it could compute the slate. That meant the shadow picker
  couldn't run until late morning, game-by-game, as lineups posted.

  BallparkPal / Krashboard and other shadow tools don't wait — they project
  each team's most-likely lineup from recent games and pick at the start of
  the day. This module does the same: for each team, take the batters with the
  most plate appearances over the last N days, keep the top 9, and assign each
  their MOST-COMMON batting-order slot.

SOURCE
  batter_game_log (sql/18). One row per (batter, game) with team_id,
  batting_order (1-9, slot-derived), pa, game_date. Pitchers are already
  excluded by pull_batter_game_log.py. Refreshed daily at 3:13 AM ET, so by
  the 8:17 AM ET pick run yesterday's finals are already logged.

API
  get_projected_lineups(team_ids, sb, *, days=14, top_n=9)
    -> { team_id: {
           "batters": [{"player_id": int, "batting_order": int|None}, ...],  # PA-ranked, top_n
           "window":  {"days": int, "from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
         } }

NOTES
  - batting_order is the batter's MODAL slot over the window (not necessarily
    unique across the 9 — two batters can share a modal slot; that's fine, the
    slot only feeds PA_BY_LINEUP_SPOT's expected-PA, which barely moves
    4.55 -> 3.68 across the order).
  - team_id comes from batter_game_log, i.e. the team the batter actually
    played for. Recent games reflect current team, so trades self-correct.
  - Supabase caps select() at 1000 rows — we paginate (known v2 gotcha).
"""

from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta


def _today_iso():
    """Slate date in ET (UTC-4, EDT) — matches pick_v2._today_iso()."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _fetch_game_log(team_ids, cutoff_iso, sb):
    """All batter_game_log rows for these teams since cutoff, paginated."""
    rows, off = [], 0
    while True:
        page = sb.table("batter_game_log") \
            .select("team_id, batter_id, batting_order, pa, game_date") \
            .gte("game_date", cutoff_iso) \
            .in_("team_id", team_ids) \
            .order("game_date") \
            .range(off, off + 999).execute().data or []
        rows.extend(page)
        if len(page) < 1000:
            break
        off += 1000
    return rows


def get_projected_lineups(team_ids, sb, *, days=14, top_n=9):
    """Typical top-`top_n` lineup per team from the last `days` of game logs.

    Returns {team_id: {"batters": [...], "window": {...}}}. Teams with no
    logged games in the window are omitted (caller handles the gap).
    """
    team_ids = list({t for t in team_ids if t})
    if not team_ids:
        return {}

    today = _today_iso()
    cutoff = (datetime.fromisoformat(today).date() - timedelta(days=days)).isoformat()
    rows = _fetch_game_log(team_ids, cutoff, sb)

    # Per (team, batter): total PA + modal batting-order tally + actual date span.
    agg = defaultdict(lambda: {"pa": 0, "ord": Counter()})
    dmin, dmax = None, None
    for r in rows:
        k = (r["team_id"], r["batter_id"])
        agg[k]["pa"] += r.get("pa") or 0
        if r.get("batting_order"):
            agg[k]["ord"][r["batting_order"]] += 1
        gd = r.get("game_date")
        if gd:
            dmin = gd if dmin is None or gd < dmin else dmin
            dmax = gd if dmax is None or gd > dmax else dmax

    window = {"days": days, "from": dmin or cutoff, "to": dmax or today}

    out = {}
    for team_id in team_ids:
        cands = sorted(((b, v) for (t, b), v in agg.items() if t == team_id),
                       key=lambda kv: kv[1]["pa"], reverse=True)[:top_n]
        if not cands:
            continue
        out[team_id] = {
            "batters": _assign_unique_slots(cands, top_n),
            "window": window,
        }
    return out


def _assign_unique_slots(cands, top_n):
    """Turn the top-N batters into a clean 1..N lineup permutation.

    Each batter has a MODAL slot (most-common position). Two batters can share
    a modal slot — if we left that as-is, both would be credited the same
    expected-PA and a real slot would go empty (overcounting opportunity). So we
    resolve collisions: process batters by modal confidence (modal count, then
    PA), give each its modal slot when free, otherwise the NEAREST free slot
    (which is typically the gap the collision created — e.g. a second modal-#6
    drops to #7). Batters with no modal history fill whatever remains.
    """
    pool = set(range(1, top_n + 1))
    decided, undecided = [], []
    for b, v in cands:
        modal = v["ord"].most_common(1)[0] if v["ord"] else None
        rec = {"player_id": b, "modal": modal[0] if modal else None,
               "count": modal[1] if modal else 0, "pa": v["pa"]}
        (decided if rec["modal"] else undecided).append(rec)

    # Most-confident modal claims first so they keep their natural slot.
    decided.sort(key=lambda r: (r["count"], r["pa"]), reverse=True)
    assigned = []
    for rec in decided:
        if rec["modal"] in pool:
            slot = rec["modal"]
        else:
            slot = min(pool, key=lambda s: (abs(s - rec["modal"]), s))
        pool.discard(slot)
        assigned.append({"player_id": rec["player_id"], "batting_order": slot})

    # No-modal batters take the leftover slots, ascending.
    for rec, slot in zip(undecided, sorted(pool)):
        assigned.append({"player_id": rec["player_id"], "batting_order": slot})

    assigned.sort(key=lambda a: a["batting_order"])
    return assigned


if __name__ == "__main__":
    # Smoke test: print projected lineups for today's slate teams.
    import os
    from supabase import create_client
    from dotenv import load_dotenv
    load_dotenv()
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    date_iso = _today_iso()
    games = sb.table("games").select("home_team_id, away_team_id") \
        .eq("game_date", date_iso).execute().data or []
    tids = [t for g in games for t in (g["home_team_id"], g["away_team_id"])]
    lus = get_projected_lineups(tids, sb)
    print(f"slate {date_iso}: {len(games)} games, {len(tids)} team slots, "
          f"{len(lus)} teams with a projected lineup")
    for tid, info in list(lus.items())[:3]:
        w = info["window"]
        print(f"  team {tid}  window {w['from']}->{w['to']} ({len(info['batters'])} batters)")
        for b in info["batters"]:
            print(f"    {b['player_id']}  slot {b['batting_order']}")
