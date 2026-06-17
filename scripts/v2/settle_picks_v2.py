"""
settle_picks_v2.py — settlement writeback for picks_v2 (v2 rebuild, Day 5+).

Writes picks_v2.book_settled_outcome once the underlying game completes, using
the MLB Stats API boxscore as source of truth. Tri-state grading mirrors
settle_pods.py EXACTLY (project norm — keep the HR rule in sync):

    1+ HR                        → book_settled_outcome = TRUE
    played (PA > 0), no HR       → book_settled_outcome = FALSE
    0 PA or absent from boxscore → leave NULL, warnings.did_not_play = true
    postponed / cancelled        → leave NULL, warnings.game_void   = true

Architecture mirrors settle_pods.py with one efficiency: picks are GROUPED by
game_id and each boxscore is fetched ONCE (~15 API calls for a ~200-pick slate,
not one per pick). Reads games + boxscores only — no other job depends on it.

Idempotent: only processes rows where book_settled_outcome IS NULL and neither
the did_not_play nor game_void flag is already set, so TRUE/FALSE/DNP/void picks
are never reprocessed.

Usage:
  python settle_picks_v2.py --dry-run [--date YYYY-MM-DD]   # report, NO writes
  python settle_picks_v2.py [--date YYYY-MM-DD]             # prod writeback
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("settle_picks_v2")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

MLB_API = "https://statsapi.mlb.com/api/v1"

GRADED_STATUSES = ("Final", "Game Over", "Completed Early")
VOID_KEYWORDS = ("postponed", "cancelled", "canceled", "forfeit")
# "suspended" deliberately excluded — those resume and produce stats later.


def _today_iso():
    """ET-relative slate date (same convention as the rest of the pipeline)."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


# ── Boxscore helpers (copied from settle_pods.py — keep grading in sync) ──
def fetch_boxscore(mlb_game_pk: int) -> dict | None:
    """Pull boxscore from MLB Stats API."""
    url = f"{MLB_API}/game/{mlb_game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        if not r.ok:
            return None
        return r.json()
    except Exception as e:
        log.warning("  Failed boxscore for game_pk=%s: %s", mlb_game_pk, e)
        return None


def find_player_batting(boxscore: dict, mlbam_id: int) -> dict | None:
    """Find a batter's stat line in the boxscore. Returns None if absent."""
    if not boxscore:
        return None
    for side in ("away", "home"):
        team = boxscore.get("teams", {}).get(side, {})
        players = team.get("players", {})
        player_key = f"ID{mlbam_id}"
        if player_key in players:
            stats = players[player_key].get("stats", {}).get("batting", {})
            if stats:
                return stats
    return None


def grade_hr(batting: dict | None) -> str:
    """
    Tri-state HR grade (mirrors settle_pods.grade_hr_pod's WIN/LOSS/VOID rule):
      - no batting line (absent)        → 'dnp'
      - PA == 0 (def sub / pinch-run)   → 'dnp'
      - HR >= 1                         → 'hr'
      - PA > 0, HR == 0                 → 'no_hr'
    """
    if batting is None:
        return "dnp"
    hr  = int(batting.get("homeRuns", 0) or 0)
    ab  = int(batting.get("atBats", 0) or 0)
    bb  = int(batting.get("baseOnBalls", 0) or 0)
    sf  = int(batting.get("sacFlies", 0) or 0)
    hbp = int(batting.get("hitByPitch", 0) or 0)
    pa  = ab + bb + sf + hbp
    if pa == 0:
        return "dnp"
    return "hr" if hr >= 1 else "no_hr"


def _merge_warning(existing: dict | None, key: str) -> dict:
    w = dict(existing or {})
    w[key] = True
    return w


def main():
    dry = "--dry-run" in sys.argv
    date_iso = _today_iso()
    if "--date" in sys.argv:
        date_iso = sys.argv[sys.argv.index("--date") + 1]

    log.info("🧅 settle_picks_v2 %s — slate %s", "DRY-RUN" if dry else "sync", date_iso)

    # ── unsettled picks for the slate (flag-guarded → idempotent) ──
    rows = sb.table("picks_v2").select(
        "id, game_id, player_id, warnings, book_settled_outcome") \
        .eq("pick_date", date_iso).is_("book_settled_outcome", "null").execute().data or []
    # JSONB flag guards in Python (PostgREST neq drops NULLs; we must keep them).
    picks = [p for p in rows
             if (p.get("warnings") or {}).get("did_not_play") is not True
             and (p.get("warnings") or {}).get("game_void") is not True]
    if not picks:
        log.info("No unsettled picks_v2 rows for %s — nothing to do.", date_iso)
        return

    game_ids = sorted({p["game_id"] for p in picks})
    games = {g["id"]: g for g in sb.table("games").select(
        "id, mlb_game_pk, status").in_("id", game_ids).execute().data or []}
    player_ids = sorted({p["player_id"] for p in picks})
    players = {p["id"]: p for p in sb.table("players").select(
        "id, mlbam_id, name").in_("id", player_ids).execute().data or []}

    # ── classify each game ──
    graded_games, void_games, pending_games = [], [], []
    for gid in game_ids:
        g = games.get(gid)
        status = (g or {}).get("status") or ""
        if any(k in status.lower() for k in VOID_KEYWORDS):
            void_games.append(gid)
        elif status in GRADED_STATUSES:
            graded_games.append(gid)
        else:
            pending_games.append(gid)

    by_game = {}
    for p in picks:
        by_game.setdefault(p["game_id"], []).append(p)

    boxscores = {}  # game_id → boxscore (fetched once per graded game)
    n_true = n_false = n_dnp = n_void = n_pending = 0
    n_pending += sum(len(by_game.get(gid, [])) for gid in pending_games)

    def _apply(pick, *, outcome=None, warn_key=None):
        """Write (or, in dry-run, just count) one pick's settlement."""
        if dry:
            return
        if warn_key is not None:
            sb.table("picks_v2").update(
                {"warnings": _merge_warning(pick.get("warnings"), warn_key)}
            ).eq("id", pick["id"]).execute()
        else:
            sb.table("picks_v2").update(
                {"book_settled_outcome": outcome}).eq("id", pick["id"]).execute()

    # ── abandoned games → game_void ──
    for gid in void_games:
        status = games[gid].get("status")
        for p in by_game.get(gid, []):
            nm = (players.get(p["player_id"]) or {}).get("name") or p["player_id"]
            _apply(p, warn_key="game_void")
            n_void += 1
            log.info("  %s @ game %s: GAME_VOID (%s)", nm, gid, status)

    # ── graded games → fetch boxscore once, grade each pick ──
    for gid in graded_games:
        g = games[gid]
        box = boxscores.get(gid)
        if box is None:
            box = fetch_boxscore(g.get("mlb_game_pk"))
            boxscores[gid] = box
        if not box:
            log.warning("  game %s: boxscore fetch failed (pk=%s) — %d picks stay pending",
                        gid, g.get("mlb_game_pk"), len(by_game.get(gid, [])))
            n_pending += len(by_game.get(gid, []))
            continue
        for p in by_game.get(gid, []):
            pl = players.get(p["player_id"]) or {}
            nm = pl.get("name") or p["player_id"]
            mlbam = pl.get("mlbam_id")
            if not mlbam:
                log.warning("  %s (id=%s): no mlbam_id — stays pending", nm, p["player_id"])
                n_pending += 1
                continue
            verdict = grade_hr(find_player_batting(box, int(mlbam)))
            if verdict == "hr":
                _apply(p, outcome=True);  n_true += 1
                log.info("  %s @ game %s: HR  → TRUE", nm, gid)
            elif verdict == "no_hr":
                _apply(p, outcome=False); n_false += 1
                log.info("  %s @ game %s: NO-HR → FALSE", nm, gid)
            else:  # dnp
                _apply(p, warn_key="did_not_play"); n_dnp += 1
                log.info("  %s @ game %s: DNP/0-PA → NULL + did_not_play", nm, gid)

    log.info("%s summary [%s]: settled_true=%d settled_false=%d dnp=%d game_void=%d still_pending=%d",
             "DRY-RUN" if dry else "DONE", date_iso,
             n_true, n_false, n_dnp, n_void, n_pending)
    if dry:
        log.info("DRY-RUN — NO DB writes.")


if __name__ == "__main__":
    main()
