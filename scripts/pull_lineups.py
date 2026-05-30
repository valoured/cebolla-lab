"""
pull_lineups.py — Pull confirmed (or projected) batting lineups for today's games.

MLB Stats API exposes lineups via the boxscore endpoint:
    GET /api/v1/game/{gamePk}/boxscore

If the game hasn't started, this returns the *projected* lineup once teams post
it (~2-4 hours before first pitch). If still empty, we fall back to each team's
most-frequent starters from the past 14 days using statsapi.

Runs hourly during slate window via GitHub Actions.
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, date, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

from lineup_predict import predicted_lineup_for_pull, _pitcher_throws_map

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"
REQUEST_DELAY = 1.0  # be polite to MLB's free API

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Player upsert helpers
# ────────────────────────────────────────────────────────────────

# PostgREST default response cap is 1,000 rows per .execute(). Players topped
# 1,000 in mid-May 2026 — the unbounded select silently truncated to the
# first 1,000 rows, so any batter outside that window failed the
# {mlbam_id: id} lookup here. The downstream upsert was bailed out by the
# players_mlbam_id_key UNIQUE constraint (no duplicate rows ever created),
# but it meant wasted UPDATEs on every cron tick: rows the lookup thought
# were "missing" got re-upserted, overwriting the same fields with the
# same values. Paginate via .range() in a loop.
# DO NOT collapse this back to a single .select(...).execute() unless the
# PostgREST default has been raised or the row count has shrunk below 1,000.
_PLAYERS_PAGE = 1000


def get_player_map() -> dict[int, int]:
    """{mlbam_id: players.id} for everyone we've seen."""
    out: dict[int, int] = {}
    offset = 0
    while True:
        res = sb.table("players").select("id, mlbam_id") \
            .range(offset, offset + _PLAYERS_PAGE - 1).execute()
        rows = res.data or []
        for p in rows:
            out[p["mlbam_id"]] = p["id"]
        if len(rows) < _PLAYERS_PAGE:
            break
        offset += _PLAYERS_PAGE
    return out


def upsert_batter(mlbam_id: int, name: str, position: str | None, bats: str | None,
                  team_id: int | None) -> int:
    payload = {
        "mlbam_id": mlbam_id,
        "name": name,
        "team_id": team_id,
        "position": position,
        "is_pitcher": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Only write bats when we actually have it. Lineup sources (boxscore,
    # last-known lineup) frequently lack batSide, so bats arrives null here.
    # An upsert with bats=null would clobber the correct value pull_rosters.py
    # set (PostgREST updates every supplied column on conflict). Omitting the
    # key leaves the existing value untouched. This is the regression guard for
    # the 72%-null-bats data bug; see backfill_bats.py for the one-time repair.
    if bats:
        payload["bats"] = bats
    res = sb.table("players").upsert(payload, on_conflict="mlbam_id").execute()
    return res.data[0]["id"]


# ────────────────────────────────────────────────────────────────
# Team & game helpers
# ────────────────────────────────────────────────────────────────

def get_team_map() -> dict[int, int]:
    """{mlb_id: teams.id}."""
    res = sb.table("teams").select("id, mlb_id").execute()
    return {t["mlb_id"]: t["id"] for t in res.data}


def get_todays_games() -> list[dict]:
    """
    Fetch games whose official baseball date is today OR tomorrow in ET.

    Rationale: same as pull_schedule.py — we want to cover the full slate
    regardless of when the cron fires. A 9:40 PM PT game has officialDate
    of today even though its game_time_utc is tomorrow.

    Using ET-relative "today" instead of UTC "today" so the window matches
    MLB's officialDate logic.
    """
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    today_et = et_now.date().isoformat()
    tomorrow_et = (et_now + timedelta(days=1)).date().isoformat()

    res = sb.table("games").select(
        "id, mlb_game_pk, away_team_id, home_team_id, game_date, "
        "away_pitcher_id, home_pitcher_id"
    ).in_("game_date", [today_et, tomorrow_et]).execute()
    return res.data


def fetch_last_known_lineup(team_id: int, opp_throws: str | None,
                            slate_date: str) -> list[dict]:
    """
    Option B predicted lineup for `team_id` ahead of `slate_date`.

    Delegates to lineup_predict.predicted_lineup_for_pull: a rolling-7
    most-common lineup with a best-effort same-handedness layer (vs the
    opposing starter's `opp_throws`), degrading to the single most-recent
    lineup when <7 games of history exist.

    Returns extract_lineup()-shaped dicts, each carrying a `lineup_source` key
    ('estimated_rolling_7' | 'estimated_last_known') the caller stamps into
    lineups.source. [] when no usable history exists.

    (Games strictly before slate_date are used, so today's row — and any
    same-day doubleheader — is naturally excluded by the helper's date filter.)
    """
    return predicted_lineup_for_pull(sb, team_id, opp_throws, slate_date)


# ────────────────────────────────────────────────────────────────
# MLB Stats API boxscore parsing
# ────────────────────────────────────────────────────────────────

def fetch_boxscore(game_pk: int) -> dict | None:
    url = f"{MLB_API}/game/{game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("  Failed boxscore for game %d: %s", game_pk, e)
        return None


def extract_lineup(team_blob: dict) -> list[dict]:
    """
    Boxscore team blob → list of {mlbam_id, name, position, bats, batting_order}.
    Batting order in MLB API is a stringified 3-digit integer:
      '101' = #1 hitter, '201' = #2 hitter, ..., '901' = #9. Subs use '102', '103', etc.
    We take only starters (suffix '01').
    """
    out = []
    players = team_blob.get("players", {}) or {}
    for p_key, p in players.items():
        order_str = p.get("battingOrder")
        if not order_str:
            continue
        if not order_str.endswith("00") and not order_str.endswith("01"):
            # subs/pinch hitters
            continue
        # Only main starters: '100', '200', ..., '900' OR sometimes '101' etc.
        try:
            order_int = int(order_str)
        except ValueError:
            continue
        if order_int % 100 not in (0, 1):
            continue
        slot = order_int // 100  # 1-9
        if slot < 1 or slot > 9:
            continue

        person = p.get("person", {}) or {}
        mlbam = person.get("id")
        name = person.get("fullName")
        if not mlbam or not name:
            continue

        pos = (p.get("position") or {}).get("abbreviation")
        bats = (p.get("batSide") or {}).get("code")

        out.append({
            "mlbam_id": int(mlbam),
            "name": name,
            "position": pos,
            "bats": bats,
            "batting_order": slot,
        })

    # Dedupe by slot (in case both '100' and '101' exist for same player)
    seen_slots = {}
    for entry in out:
        seen_slots[entry["batting_order"]] = entry
    return sorted(seen_slots.values(), key=lambda x: x["batting_order"])


# ────────────────────────────────────────────────────────────────
# Main per-game processing
# ────────────────────────────────────────────────────────────────

def process_game(game: dict, team_map: dict[int, int], player_map: dict[int, int]) -> tuple[int, str]:
    """
    Returns (rows_written, status_label).

    status_label is one of:
      "confirmed"  → all real lineups confirmed (slots 1-9 from MLB API)
      "projected"  → MLB API returned partial/incomplete lineups
      "last_known" → at least one team had to fall back to historical lineup
      "empty"      → nothing posted, no historical fallback either
    """
    box = fetch_boxscore(game["mlb_game_pk"])
    if not box:
        return (0, "empty")

    teams = box.get("teams", {}) or {}
    away_blob = teams.get("away", {}) or {}
    home_blob = teams.get("home", {}) or {}

    away_lineup = extract_lineup(away_blob)
    home_lineup = extract_lineup(home_blob)

    rows = []
    any_confirmed = False
    any_fallback = False

    for team_blob, lineup, our_team_id, team_label in [
        (away_blob, away_lineup, game["away_team_id"], "away"),
        (home_blob, home_lineup, game["home_team_id"], "home"),
    ]:
        source = "mlb_api"

        # ── Last-known fallback ─────────────────────────────────
        # If MLB hasn't posted anything for this team yet, look back to
        # this team's most recent posted lineup and use it as projection.
        if not lineup:
            log.info("   %s team has no MLB lineup yet → predicted-lineup fallback",
                     team_label)
            # Opposing starter = the OTHER side's pitcher. Throws comes from the
            # memoized pitcher map (one players query per cron run) — no per-game
            # handedness query. Game pitcher ids ride the already-fetched game row.
            opp_pid = (game.get("home_pitcher_id") if team_label == "away"
                       else game.get("away_pitcher_id"))
            opp_throws = _pitcher_throws_map(sb).get(opp_pid) if opp_pid else None
            lineup = fetch_last_known_lineup(our_team_id, opp_throws, game["game_date"])
            if lineup:
                # The helper tags every row with the specific estimator used;
                # stamp it into lineups.source so compute_projections reads
                # provenance back (Finding C).
                source = lineup[0].get("lineup_source") or "estimated_last_known"
                any_fallback = True
            else:
                log.info("   %s team has no historical lineup either", team_label)
                continue

        # If batting orders are populated AND game hasn't started, MLB calls
        # them confirmed. Once 9 hitters with consecutive slots 1-9 = confirmed.
        slots = sorted(b["batting_order"] for b in lineup)
        is_confirmed = (source == "mlb_api" and slots == list(range(1, 10)))
        if is_confirmed:
            any_confirmed = True

        for b in lineup:
            mlbam = b["mlbam_id"]
            if mlbam not in player_map:
                # Auto-register batter (name + handedness)
                new_id = upsert_batter(
                    mlbam, b["name"], b.get("position"),
                    b.get("bats"), our_team_id,
                )
                player_map[mlbam] = new_id

            rows.append({
                "game_id": game["id"],
                "team_id": our_team_id,
                "player_id": player_map[mlbam],
                "batting_order": b["batting_order"],
                "position": b.get("position"),
                "bats": b.get("bats"),
                "is_confirmed": is_confirmed,
                "source": source,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    if not rows:
        return (0, "empty")

    # ── Smart delete: don't wipe a confirmed real lineup with a last_known ──
    # If ANY existing rows for this game are is_confirmed=true, AND our new
    # rows include last_known fallback data, only DELETE the team-side that
    # we're rewriting. This prevents the fallback path from accidentally
    # wiping a real lineup that arrived between cron runs.
    if any_fallback:
        # Per-team delete-and-insert: only nuke the teams we're writing for
        team_ids_in_rows = {r["team_id"] for r in rows}
        for tid in team_ids_in_rows:
            # Check if existing rows for this game+team are confirmed
            existing = sb.table("lineups") \
                .select("is_confirmed, source") \
                .eq("game_id", game["id"]) \
                .eq("team_id", tid) \
                .execute()
            existing_rows = existing.data or []
            # If existing rows are confirmed from MLB API, and our new rows
            # are from last_known fallback for the same team, SKIP the rewrite
            new_for_team = [r for r in rows if r["team_id"] == tid]
            new_sources = {r["source"] for r in new_for_team}
            if (
                existing_rows
                and any(r.get("is_confirmed") for r in existing_rows)
                and any(r.get("source") == "mlb_api" for r in existing_rows)
                and new_sources.issubset(
                    {"last_known", "estimated_rolling_7", "estimated_last_known"})
            ):
                log.info("   ⌀ skipping team_id=%d rewrite: existing data is confirmed", tid)
                # Strip out the rows for this team from our payload
                rows = [r for r in rows if r["team_id"] != tid]
                continue
            # Otherwise, delete this team's rows and we'll re-insert below
            sb.table("lineups").delete() \
                .eq("game_id", game["id"]) \
                .eq("team_id", tid) \
                .execute()
    else:
        # Standard path: real MLB data, full game wipe-and-insert
        sb.table("lineups").delete().eq("game_id", game["id"]).execute()

    # Insert in chunks
    for i in range(0, len(rows), 50):
        sb.table("lineups").insert(rows[i:i+50]).execute()

    # Status label for logging
    if any_confirmed and not any_fallback:
        status = "confirmed"
    elif any_fallback:
        status = "last_known"
    else:
        status = "projected"

    return (len(rows), status)


def main():
    log.info("🧅 Cebolla Lab — Lineups sync starting")

    team_map = get_team_map()
    player_map = get_player_map()
    games = get_todays_games()

    if not games:
        log.info("No games today.")
        return

    log.info("Found %d games today", len(games))

    total_rows = 0
    by_status = {"confirmed": 0, "projected": 0, "last_known": 0, "empty": 0, "error": 0}

    for i, g in enumerate(games, 1):
        log.info("[%d/%d] gamePk %d", i, len(games), g["mlb_game_pk"])
        # Per-game try/except — one game with a duplicate-key conflict or
        # malformed boxscore must not kill the entire run. Log, count, move on.
        try:
            rows, status = process_game(g, team_map, player_map)
        except Exception as e:
            by_status["error"] = by_status.get("error", 0) + 1
            log.warning("   ✗ game %d failed: %s", g.get("mlb_game_pk"), e)
            time.sleep(REQUEST_DELAY)
            continue

        by_status[status] = by_status.get(status, 0) + 1
        if rows:
            total_rows += rows
            badge = {
                "confirmed":   "✓ CONFIRMED",
                "projected":   "◦ projected",
                "last_known":  "↺ last-known fallback",
            }.get(status, status)
            log.info("   %s — %d batters", badge, rows)
        else:
            log.info("   ⌀ no lineup (no fallback available)")
        time.sleep(REQUEST_DELAY)

    log.info(
        "🧅 Lineups sync complete: %d batters total | "
        "confirmed=%d projected=%d last_known=%d empty=%d error=%d",
        total_rows,
        by_status["confirmed"],
        by_status["projected"],
        by_status["last_known"],
        by_status["empty"],
        by_status.get("error", 0),
    )


if __name__ == "__main__":
    main()
