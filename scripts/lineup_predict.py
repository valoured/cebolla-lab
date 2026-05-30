"""
lineup_predict.py — Option B rolling-7 most-common predicted lineup.

Shared by pull_lineups.py (re-stamps fallback rows into the lineups table) and
compute_projections.py (feeds the projection model when today's lineup is not
posted yet at the ~3 AM ET lock).

Structure:
  _pitcher_throws_map(sb)        — memoized {player_id: throws}; ONE query per
                                   process (== one cron run).
  _fetch_team_lineup_history(..) — DB: recent complete lineup snapshots for a team.
  _resolve_modal_lineup(..)      — PURE (no DB): the whole algorithm; unit-tested.
  predicted_lineup_for_pull(..)        — adapter → mlbam-keyed rows (pull_lineups)
  predicted_lineup_for_projections(..) — adapter → player_id-keyed rows (compute)

lineup_source values produced here: 'estimated_rolling_7' | 'estimated_last_known'.
'confirmed' is assigned by compute_projections for real posted lineups, never here.

Handedness layer is BEST-EFFORT: games whose opposing-starter throws is unknown
are excluded from the same-handedness pool; if <HANDEDNESS_MIN_GAMES remain, we
silently fall back to the unsplit rolling window (WARN-logged for later audit).
Never crashes on missing handedness data.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta

log = logging.getLogger("lineup_predict")

ROLLING_WINDOW = 7            # games in the mode pool
HISTORY_FETCH = 10           # buffer; some teams have <7 games early season
HANDEDNESS_MIN_GAMES = 4     # >=4 same-hand games -> use the split pool
LOOKBACK_DAYS = 21           # calendar window to find ~HISTORY_FETCH games

# Module-level memo — populated once per process. Each cron run is a fresh
# process, so the cache lifetime is exactly one cron run: ONE players query
# serves every team's handedness lookups for the whole run.
_THROWS_CACHE = None


def _pitcher_throws_map(sb):
    """{player_id: throws} for all pitchers. Memoized — one query per process."""
    global _THROWS_CACHE
    if _THROWS_CACHE is None:
        out = {}
        offset = 0
        while True:
            r = sb.table("players").select("id, throws") \
                .eq("is_pitcher", True).range(offset, offset + 999).execute()
            chunk = r.data or []
            for p in chunk:
                out[p["id"]] = p.get("throws")
            if len(chunk) < 1000:
                break
            offset += 1000
        _THROWS_CACHE = out
    return _THROWS_CACHE


def _reset_throws_cache():
    """Test hook — clear the memo so a test can inject a fresh map."""
    global _THROWS_CACHE
    _THROWS_CACHE = None


def _fetch_team_lineup_history(sb, team_id, slate_date, throws_map):
    """
    Up to HISTORY_FETCH most-recent COMPLETE (slots 1-9) lineup snapshots for
    `team_id` strictly before `slate_date`, newest first. Each snapshot:
        {game_id, date, opp_throws, slots:{1..9: player_id},
         meta:{player_id: {position, bats}}}
    opp_throws is None when the opposing starter (or their throws) is unknown.
    """
    cutoff = (datetime.fromisoformat(slate_date).date()
              - timedelta(days=LOOKBACK_DAYS)).isoformat()
    res = sb.table("lineups").select(
        "game_id, team_id, batting_order, position, bats, player_id, "
        "games!inner(game_date, away_team_id, home_team_id, "
        "away_pitcher_id, home_pitcher_id)"
    ).eq("team_id", team_id) \
     .gte("games.game_date", cutoff) \
     .lt("games.game_date", slate_date) \
     .order("games(game_date)", desc=True) \
     .limit(HISTORY_FETCH * 11) \
     .execute()
    rows = res.data or []

    by_game = defaultdict(list)
    gmeta = {}
    for r in rows:
        by_game[r["game_id"]].append(r)
        gmeta[r["game_id"]] = r.get("games") or {}

    snaps = []
    for g in sorted(by_game, key=lambda x: gmeta[x].get("game_date", ""), reverse=True):
        slots, meta = {}, {}
        for r in by_game[g]:
            s = r.get("batting_order")
            if s and 1 <= s <= 9 and s not in slots and r.get("player_id"):
                slots[s] = r["player_id"]
                meta[r["player_id"]] = {"position": r.get("position"), "bats": r.get("bats")}
        if sorted(slots) != list(range(1, 10)):
            continue  # incomplete snapshot — skip
        gm = gmeta[g]
        opp_pid = (gm.get("away_pitcher_id") if gm.get("home_team_id") == team_id
                   else gm.get("home_pitcher_id"))
        snaps.append({
            "game_id": g,
            "date": gm.get("game_date"),
            "opp_throws": throws_map.get(opp_pid) if opp_pid else None,
            "slots": slots,
            "meta": meta,
        })
        if len(snaps) >= HISTORY_FETCH:
            break
    return snaps


def _resolve_modal_lineup(snapshots, opponent_throws):
    """
    PURE — no DB. `snapshots` newest-first (as from _fetch_team_lineup_history).

    Returns (rows, source):
      rows   = [{batting_order, player_id, position, bats}] for assignable slots
               (usually 9; fewer only if history is too thin to fill a slot)
      source = 'estimated_rolling_7' | 'estimated_last_known' | None (empty input)

    Algorithm:
      - <ROLLING_WINDOW snapshots  -> degrade to single most-recent ('..last_known')
      - handedness layer (best-effort): if opponent_throws given and >=
        HANDEDNESS_MIN_GAMES snapshots share it, restrict the pool to those
        (most-recent ROLLING_WINDOW); else WARN + unsplit most-recent ROLLING_WINDOW
      - per slot 1-9: modal player (ties -> most recent appearance)
      - cross-slot dedup: a player modal in >1 slot goes to the slot with the
        higher in-slot count (tie -> lower slot #); losing slots take next candidate
    """
    if not snapshots:
        return [], None

    # ── Degrade: insufficient history ──
    if len(snapshots) < ROLLING_WINDOW:
        recent = snapshots[0]
        rows = [{
            "batting_order": s,
            "player_id": recent["slots"][s],
            "position": recent["meta"].get(recent["slots"][s], {}).get("position"),
            "bats": recent["meta"].get(recent["slots"][s], {}).get("bats"),
        } for s in range(1, 10) if s in recent["slots"]]
        return rows, "estimated_last_known"

    # ── Handedness layer (best-effort) ──
    if opponent_throws:
        same = [s for s in snapshots if s["opp_throws"] == opponent_throws]
        if len(same) >= HANDEDNESS_MIN_GAMES:
            pool = same[:ROLLING_WINDOW]
        else:
            log.warning(
                "handedness: only %d same-hand (%s) game(s) of %d available "
                "after exclusions; using unsplit last-%d",
                len(same), opponent_throws, len(snapshots), ROLLING_WINDOW,
            )
            pool = snapshots[:ROLLING_WINDOW]
    else:
        pool = snapshots[:ROLLING_WINDOW]

    # Per-slot frequency + best (lowest = newest) recency index for tie-breaks.
    slot_counts = {s: Counter() for s in range(1, 10)}
    slot_recency = {s: {} for s in range(1, 10)}
    pmeta = {}
    for idx, snap in enumerate(pool):
        for slot, pid in snap["slots"].items():
            slot_counts[slot][pid] += 1
            if pid not in slot_recency[slot] or idx < slot_recency[slot][pid]:
                slot_recency[slot][pid] = idx
            if pid not in pmeta:
                pmeta[pid] = snap["meta"].get(pid, {})

    def ranked(slot):
        # count desc, then recency asc (lower idx = more recent)
        return sorted(slot_counts[slot].keys(),
                      key=lambda pid: (-slot_counts[slot][pid], slot_recency[slot][pid]))

    # Greedy assignment with cross-slot dedup. Each unfilled slot proposes its
    # top not-yet-taken candidate; conflicts resolved by higher in-slot count
    # (tie -> lower slot #); losers re-propose next round. Terminates because
    # every round assigns >=1 slot (or drops a slot with no candidates left).
    assigned, taken, remaining = {}, set(), set(range(1, 10))
    while remaining:
        proposals = {}
        for slot in list(remaining):
            cand = next((p for p in ranked(slot) if p not in taken), None)
            if cand is None:
                remaining.discard(slot)  # nothing left for this slot
            else:
                proposals[slot] = cand
        if not proposals:
            break
        by_cand = defaultdict(list)
        for slot, pid in proposals.items():
            by_cand[pid].append(slot)
        for pid, slots_wanting in by_cand.items():
            winner = max(slots_wanting, key=lambda sl: (slot_counts[sl][pid], -sl))
            assigned[winner] = pid
            taken.add(pid)
            remaining.discard(winner)
            # losing slots stay in `remaining`; they re-propose next round

    rows = [{
        "batting_order": s,
        "player_id": assigned[s],
        "position": pmeta.get(assigned[s], {}).get("position"),
        "bats": pmeta.get(assigned[s], {}).get("bats"),
    } for s in range(1, 10) if s in assigned]
    return rows, "estimated_rolling_7"


# ── Adapters ──────────────────────────────────────────────────────────────

def predicted_lineup_for_projections(sb, team_id, opponent_throws, slate_date):
    """player_id-keyed rows matching get_lineups_for_game()'s row shape."""
    snaps = _fetch_team_lineup_history(sb, team_id, slate_date, _pitcher_throws_map(sb))
    rows, source = _resolve_modal_lineup(snaps, opponent_throws)
    return [{
        "id": None,
        "team_id": team_id,
        "player_id": r["player_id"],
        "batting_order": r["batting_order"],
        "position": r["position"],
        "bats": r["bats"],
        "is_confirmed": False,
        "lineup_source": source,
    } for r in rows]


def predicted_lineup_for_pull(sb, team_id, opponent_throws, slate_date):
    """
    mlbam-keyed rows matching extract_lineup()'s shape (+ lineup_source) so
    pull_lineups can re-stamp them into the lineups table.
    """
    snaps = _fetch_team_lineup_history(sb, team_id, slate_date, _pitcher_throws_map(sb))
    rows, source = _resolve_modal_lineup(snaps, opponent_throws)
    if not rows:
        return []
    pids = [r["player_id"] for r in rows]
    pres = sb.table("players").select("id, mlbam_id, name").in_("id", pids).execute()
    pmap = {p["id"]: p for p in (pres.data or [])}
    out = []
    for r in rows:
        p = pmap.get(r["player_id"])
        if not p:
            continue  # missing player record — drop this slot
        out.append({
            "mlbam_id": p["mlbam_id"],
            "name": p["name"],
            "position": r["position"],
            "bats": r["bats"],
            "batting_order": r["batting_order"],
            "lineup_source": source,
        })
    return out
