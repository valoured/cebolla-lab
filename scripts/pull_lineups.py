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

def get_player_map() -> dict[int, int]:
    """{mlbam_id: players.id} for everyone we've seen."""
    res = sb.table("players").select("id, mlbam_id").execute()
    return {p["mlbam_id"]: p["id"] for p in res.data}


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
        "id, mlb_game_pk, away_team_id, home_team_id, game_date"
    ).in_("game_date", [today_et, tomorrow_et]).execute()
    return res.data


def fetch_last_known_lineup(team_id: int, current_game_id: int,
                            lookback_days: int = 14) -> list[dict]:
    """
    Fall back to this team's most recent posted lineup from the last N days,
    excluding the current game.

    Returns a list of dicts shaped like extract_lineup() output, or [] if no
    historical lineup is found in the lookback window.

    Strategy:
      1. Find this team's most recent past game with at least 9 lineup rows
      2. Pull those 9 batters with their batting_order, position, bats
      3. Return them so caller can re-stamp as "projected" for the current game

    Notes:
      - We only consider lineups from games on dates BEFORE the current game.
        Same-day doubleheaders are intentionally excluded to avoid the rare
        case where Game 1 lineup is mid-write when this script fires.
      - We accept any prior lineup with >=9 rows, regardless of whether it
        was originally confirmed or projected. Best-available wins.
    """
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    today_et = et_now.date().isoformat()
    cutoff_date = (et_now.date() - timedelta(days=lookback_days)).isoformat()

    # Find candidate past games for this team where lineups exist
    games_res = sb.table("games") \
        .select("id, game_date") \
        .or_(f"home_team_id.eq.{team_id},away_team_id.eq.{team_id}") \
        .gte("game_date", cutoff_date) \
        .lt("game_date", today_et) \
        .neq("id", current_game_id) \
        .order("game_date", desc=True) \
        .limit(10) \
        .execute()

    candidate_games = games_res.data or []
    if not candidate_games:
        return []

    # For each candidate (newest first), check if 9 lineup rows exist for this team
    for cg in candidate_games:
        lineup_res = sb.table("lineups") \
            .select("player_id, batting_order, position, bats") \
            .eq("game_id", cg["id"]) \
            .eq("team_id", team_id) \
            .order("batting_order") \
            .execute()

        rows = lineup_res.data or []
        # Need a real starting lineup — require 9 unique slots 1-9
        slots = sorted({r["batting_order"] for r in rows if r.get("batting_order")})
        if slots != list(range(1, 10)):
            continue

        # Dedupe by slot (in case multiple players share a slot in history)
        by_slot = {}
        for r in rows:
            slot = r["batting_order"]
            if slot not in by_slot:
                by_slot[slot] = r

        # Reshape to match extract_lineup() output format (uses mlbam_id)
        # We need the mlbam_id — look up each player_id
        player_ids = [by_slot[s]["player_id"] for s in range(1, 10)]
        players_res = sb.table("players") \
            .select("id, mlbam_id, name") \
            .in_("id", player_ids) \
            .execute()
        mlbam_by_internal = {p["id"]: p for p in players_res.data}

        out = []
        for slot in range(1, 10):
            r = by_slot[slot]
            p = mlbam_by_internal.get(r["player_id"])
            if not p:
                # Missing player record — skip this lineup, try older one
                break
            out.append({
                "mlbam_id": p["mlbam_id"],
                "name": p["name"],
                "position": r.get("position"),
                "bats": r.get("bats"),
                "batting_order": slot,
            })
        else:
            # All 9 slots resolved cleanly — return this lineup
            log.info("    fallback: using lineup from game_id=%d (%s)",
                     cg["id"], cg["game_date"])
            return out

    return []


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
            log.info("   %s team has no MLB lineup yet → trying last-known fallback",
                     team_label)
            lineup = fetch_last_known_lineup(our_team_id, game["id"])
            if lineup:
                source = "last_known"
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
                and new_sources == {"last_known"}
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
