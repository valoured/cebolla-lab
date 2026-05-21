"""
capture_closing_lines.py — populate `closing_odds` columns on pods and card_legs.

We're not re-pulling DraftKings here. We already pull dk_odds hourly via
`pull_dk_odds.py`, which writes to `odds_snapshots`. For each pending POD
and card_leg, this script finds the most recent snapshot from BEFORE that
game's first pitch and copies it into the `closing_odds` columns on the
target row.

Why this approach:
  - Zero additional DK API calls (we already have the data)
  - Resilient — runs idempotently. If a game's already locked-in closing
    odds, we don't overwrite. If not yet captured, we try.
  - Simple — pure SQL/query work

When this runs:
  - Hourly at :17 past the hour, 1 PM ET through 11 PM ET (covers the
    slate window).
  - Each run captures any pending picks now within the closing window
    that haven't been captured yet. Idempotent — already-captured rows
    are filtered out at query time via `closing_odds IS NULL`.

What gets captured:
  - For each pending pod/card_leg
  - Find the latest odds_snapshot for that (game_id, player_id, market) tuple
  - Where snapshot_time < first_pitch_utc (game hasn't started yet)
  - Where snapshot_time >= first_pitch_utc - CLOSING_LOOKBACK_MINUTES (recent enough)
  - Copy american_odds → closing_odds
  - Compute closing_implied + closing_no_vig
  - Compute clv_raw + clv_no_vig (vs the lock-time american_odds/no_vig_prob)

Output columns written:
  - closing_odds (INT)
  - closing_implied (NUMERIC) — raw implied prob from closing_odds
  - closing_no_vig (NUMERIC) — de-vigged using the SAME dynamic vig curve
    that compute_projections.devig_anytime() uses at lock time. Critical
    for CLV to be meaningful (both sides of comparison must use the same
    method).
  - closing_captured_at (TIMESTAMPTZ)
  - clv_raw (NUMERIC) — closing_implied - lock_implied (positive = we beat the close)
  - clv_no_vig (NUMERIC) — closing_no_vig - no_vig_prob (preferred CLV measure)

CLV interpretation:
  Positive CLV consistently across many picks = our model is finding mispricing
  the rest of the market subsequently agrees with. This is the strongest short-
  term signal of model quality (way before W/L settlement variance smooths out).

PERSONAL USE ONLY.
"""

import os
import sys
import logging
from datetime import datetime, timedelta, timezone

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY env vars")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ────────────────────────────────────────────────────────────────────────
# CONSTANTS — kept in sync with compute_projections.py's devig_anytime()
# ────────────────────────────────────────────────────────────────────────

# How far before first pitch counts as "closing line" window.
#
# Original v1 was 90 min — works great for games where DK posts continuous
# odds through first pitch. But for longshot prices (+800 to +2000 HRs),
# DK often pulls the market hours before game time, leaving no snapshot in
# a tight window. To capture CLV on those picks we look back further.
#
# 240 minutes (4 hours) tradeoff:
#   - For normal markets: still picks the most recent pre-FP snapshot, same
#     result as 90 min would have given.
#   - For longshots with pulled markets: now captures the last-available
#     price as the "closing" line. Less true to "closing" semantically but
#     better than no CLV data at all.
#
# If a snapshot from 4 hours before first pitch is the BEST we have, that's
# effectively the closing price for that pick — the market closed early.
CLOSING_LOOKBACK_MINUTES = 240

# Longshot thresholds — MUST stay in sync with compute_projections.py.
# If the values in compute_projections.py ever change, update them here too,
# otherwise CLV computation will use a different "trust range" than lock-time
# projections — leading to silently inconsistent results.
#
# Verify on every change:
#   grep -n "HR_LONGSHOT_THRESHOLD\|HITS_LONGSHOT_THRESHOLD" scripts/compute_projections.py
#
# Picks past these thresholds have lock_no_vig = None at lock time
# (compute_projections.devig_anytime returns None), so they wouldn't produce
# clv_no_vig anyway. We match the threshold here so closing-side de-vig
# behaves identically.
HR_LONGSHOT_THRESHOLD = 2000      # match compute_projections.py:139
HITS_LONGSHOT_THRESHOLD = 600     # match compute_projections.py:140

# Market name mapping: pods.market and card_legs.market differ from
# odds_snapshots.market by a "_yes" suffix. Build a lookup so we match
# cleanly across the two tables.
#
# pods.market / card_legs.market values (as written by compute_projections
# and persisted by pick_pod / pick_cards):
#   'hr_anytime'         — single-sided HR Anytime
#   'h_r_rbi_1.5'        — HRR line 1.5  (HRR markets have NO _yes suffix
#   'h_r_rbi_2.5'        —                here despite being yes-side picks)
#   'h_r_rbi_3.5'
#   'hits_yes'           — 1+ hits (yes-side, suffix INCLUDED)
#   'rbi_yes'            — 1+ RBI  (never actually written by projections,
#                                   listed for completeness)
#
# odds_snapshots.market values written by pull_dk_odds.py:
#   'hr_anytime_yes'     — line=0.5  (HR is always 1+ ladder, line=0.5)
#   'h_r_rbi_yes'        — line=1.5 | 2.5 | 3.5  (line column differentiates)
#   'hits_yes'           — line=0.5  (also 1.5/2.5/3.5 exist for higher ladders)
#   'rbi_yes'            — line=0.5
#
# So the join from pick-table to snapshot table is:
#   - HR Anytime:  market='hr_anytime_yes'   (line implicitly 0.5)
#   - HRR:         market='h_r_rbi_yes' AND line=<1.5|2.5|3.5>
#   - Hits 1+:     market='hits_yes'         (line=0.5)
#   - RBI 1+:      market='rbi_yes'          (line=0.5)


def american_to_implied(american: int | None) -> float | None:
    """American odds → raw implied probability (still has vig)."""
    if american is None:
        return None
    if american > 0:
        return 100.0 / (american + 100.0)
    if american < 0:
        return abs(american) / (abs(american) + 100.0)
    return None


def devig_anytime(american_odds: int | None, implied: float | None,
                   market: str = "hr_anytime") -> float | None:
    """
    De-vig single-sided Yes props using the SAME curve as
    compute_projections.devig_anytime(). This MUST stay in sync — if the
    lock-time and closing-time de-vig methods diverge, CLV becomes
    apples-to-oranges and meaningless.

    Returns no_vig probability, or None if outside trust range (longshot
    filter — matches compute_projections behavior).
    """
    if american_odds is None or implied is None or implied <= 0 or implied >= 1:
        return None

    if market == "hits_yes":
        if american_odds >= HITS_LONGSHOT_THRESHOLD:
            return None
        if american_odds <= -150:
            vig = 0.04
        elif american_odds <= 100:
            vig = 0.05
        elif american_odds <= 300:
            vig = 0.06
        else:  # +300 to +600
            vig = 0.08
    else:
        # HR Anytime — wider price range, steeper longshot vig.
        # NOTE: HRR does NOT come through here; HRR is routed to the
        # "hits_yes" branch above by closing_market_for_devig() to match
        # how compute_projections.py handles HRR (see line ~1063 in
        # compute_projections, devig_anytime called with market="hits_yes").
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


def closing_market_for_devig(snapshot_market: str) -> str:
    """
    Map an odds_snapshots.market value to the market key used by devig_anytime.
    The vig curve differs per market — Hits has tighter vig than HR.

    Mapping (matches compute_projections.py exactly):
        hits_yes        → "hits_yes"        (hits curve)
        h_r_rbi_yes     → "hits_yes"        (HRR uses the hits curve per
                                             compute_projections.py ~line 1063
                                             where HRR projections call
                                             devig_anytime with market="hits_yes")
        rbi_yes         → "hits_yes"        (RBI props price similarly to hits;
                                             not currently written by projections
                                             but mapped here for future-proofing)
        hr_anytime_yes  → "hr_anytime"      (HR curve, steeper longshot vig)
        anything else   → "hr_anytime"      (defensive fallback)
    """
    if snapshot_market == "hits_yes":
        return "hits_yes"
    if snapshot_market == "h_r_rbi_yes":
        return "hits_yes"
    if snapshot_market == "rbi_yes":
        return "hits_yes"
    return "hr_anytime"


def market_lookup(pod_or_leg_market: str, line: float | None = None) -> tuple[str, float | None]:
    """
    Map a pods/card_legs market value to the (market, line) we use in
    odds_snapshots.

    Returns (snapshot_market, snapshot_line) tuple.
    Returns (None, None) if we can't match.
    """
    m = (pod_or_leg_market or "").lower()
    if m == "hr_anytime":
        return ("hr_anytime_yes", None)
    if m.startswith("h_r_rbi_"):
        # 'h_r_rbi_1.5' / 'h_r_rbi_2.5' / 'h_r_rbi_3.5' (with or without _yes suffix)
        # Extract the line number
        for ln in (1.5, 2.5, 3.5):
            if f"_{ln}" in m or m.endswith(f"_{ln}_yes"):
                return ("h_r_rbi_yes", ln)
        return (None, None)
    if m == "hits_yes":
        return ("hits_yes", 0.5)
    if m == "rbi_yes":
        return ("rbi_yes", 0.5)
    return (None, None)


def get_today_iso() -> str:
    """ET-relative slate date (matches pick_pod's convention)."""
    et_offset = timedelta(hours=-4)   # EDT (May = DST in effect)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


# ────────────────────────────────────────────────────────────────────────
# FETCH PENDING PICKS
# ────────────────────────────────────────────────────────────────────────

def fetch_pending_pods(date_iso: str) -> list[dict]:
    """
    All PODs from today/recent dates with status='pending' that haven't yet
    had closing odds captured. Filtering on closing_odds IS NULL at query
    time means we don't re-fetch already-processed rows.
    """
    res = sb.table("pods").select(
        "id, pod_date, game_id, player_id, market, american_odds, no_vig_prob"
    ).eq("status", "pending") \
     .gte("pod_date", date_iso) \
     .is_("closing_odds", "null") \
     .execute()
    return res.data or []


def fetch_pending_card_legs(date_iso: str) -> list[dict]:
    """All card_legs from pending cards on today/recent dates without closing_odds.

    Done as two queries (matches settle_cards.py pattern):
      1. Pull pending cards within date window
      2. Pull their legs filtered to those without closing_odds yet
    """
    cards_res = sb.table("cards").select("id") \
        .eq("status", "pending") \
        .gte("card_date", date_iso) \
        .execute()
    cards = cards_res.data or []
    if not cards:
        return []
    card_ids = [c["id"] for c in cards]

    legs_res = sb.table("card_legs").select(
        "id, card_id, game_id, player_id, market, line, american_odds, no_vig_prob"
    ).in_("card_id", card_ids) \
     .is_("closing_odds", "null") \
     .execute()
    return legs_res.data or []


def fetch_game_first_pitches(game_ids: list[int]) -> dict[int, datetime]:
    """
    Map game_id → first_pitch UTC datetime (always timezone-aware).

    Postgres TIMESTAMPTZ is returned by supabase-py as an ISO string with
    timezone offset (e.g. '2026-05-22T17:05:00+00:00' or '...Z'). We parse
    and normalize to UTC. If for any reason the string is naive (no offset
    AND no Z), we attach UTC explicitly so downstream comparisons with
    `datetime.now(timezone.utc)` don't TypeError.
    """
    if not game_ids:
        return {}
    res = sb.table("games").select("id, game_time_utc, status") \
        .in_("id", game_ids) \
        .execute()
    out = {}
    for g in res.data or []:
        raw = g.get("game_time_utc")
        if not raw:
            continue
        # Normalize trailing Z to +00:00 for fromisoformat compatibility.
        normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        try:
            fp = datetime.fromisoformat(normalized)
        except ValueError:
            log.warning("Could not parse game_time_utc for game %s: %r", g.get("id"), raw)
            continue
        # Force timezone-aware (defensive — if string somehow lacked offset)
        if fp.tzinfo is None:
            fp = fp.replace(tzinfo=timezone.utc)
        out[g["id"]] = fp
    return out


# ────────────────────────────────────────────────────────────────────────
# FETCH CLOSING SNAPSHOTS
# ────────────────────────────────────────────────────────────────────────

def fetch_closing_snapshot(
    game_id: int,
    player_id: int,
    snapshot_market: str,
    snapshot_line: float | None,
    first_pitch: datetime,
) -> dict | None:
    """
    Find the latest odds_snapshot for (game, player, market[, line]) where
    snapshot_time < first_pitch and snapshot_time >= (first_pitch - lookback).

    Returns the snapshot row dict, or None if no qualifying snapshot exists.
    """
    window_start = first_pitch - timedelta(minutes=CLOSING_LOOKBACK_MINUTES)

    q = sb.table("odds_snapshots").select(
        "american_odds"
    ).eq("game_id", game_id) \
     .eq("player_id", player_id) \
     .eq("market", snapshot_market) \
     .eq("book", "draftkings") \
     .lt("snapshot_time", first_pitch.isoformat()) \
     .gte("snapshot_time", window_start.isoformat()) \
     .order("snapshot_time", desc=True) \
     .limit(1)

    if snapshot_line is not None:
        q = q.eq("line", snapshot_line)

    res = q.execute()
    rows = res.data or []
    if not rows:
        return None

    return rows[0]


# ────────────────────────────────────────────────────────────────────────
# CLV COMPUTATION
# ────────────────────────────────────────────────────────────────────────

def compute_clv_fields(
    lock_american: int,
    lock_no_vig: float | None,
    closing_american: int,
    snapshot_market: str,
) -> dict:
    """
    Compute calibration columns for a single pick.

    Uses the SAME de-vig curve at closing time as compute_projections.py uses
    at lock time. This is critical — if the methods diverge, CLV becomes
    apples-to-oranges and meaningless.

    Args:
        lock_american:    American odds at lock time (from pod/leg row).
        lock_no_vig:      No-vig prob at lock time (from pod/leg row, computed
                          by compute_projections.devig_anytime at the time).
        closing_american: American odds at closing time (from odds_snapshots).
        snapshot_market:  The odds_snapshots.market value (e.g. 'hr_anytime_yes',
                          'hits_yes', 'h_r_rbi_yes'). Used to pick the right
                          vig curve for the closing de-vig.

    Returns:
        Dict with closing_implied, closing_no_vig, clv_raw, clv_no_vig.
        Any field that can't be computed is None.
    """
    closing_implied = american_to_implied(closing_american)
    lock_implied = american_to_implied(lock_american)

    # De-vig closing using the SAME curve compute_projections uses for lock.
    devig_market = closing_market_for_devig(snapshot_market)
    closing_no_vig = devig_anytime(closing_american, closing_implied, market=devig_market)

    clv_raw = None
    if closing_implied is not None and lock_implied is not None:
        clv_raw = closing_implied - lock_implied

    clv_no_vig = None
    if closing_no_vig is not None and lock_no_vig is not None:
        clv_no_vig = closing_no_vig - float(lock_no_vig)

    return {
        "closing_implied": round(closing_implied, 4) if closing_implied is not None else None,
        "closing_no_vig":  round(closing_no_vig,  4) if closing_no_vig  is not None else None,
        "clv_raw":         round(clv_raw,         5) if clv_raw         is not None else None,
        "clv_no_vig":      round(clv_no_vig,      5) if clv_no_vig      is not None else None,
    }


# ────────────────────────────────────────────────────────────────────────
# UPDATE
# ────────────────────────────────────────────────────────────────────────

def update_pod(pod_id: int, fields: dict, closing_odds: int):
    """Write closing fields back to the pod row."""
    payload = {
        "closing_odds": closing_odds,
        "closing_captured_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    sb.table("pods").update(payload).eq("id", pod_id).execute()


def update_card_leg(leg_id: int, fields: dict, closing_odds: int):
    """Write closing fields back to the card_leg row."""
    payload = {
        "closing_odds": closing_odds,
        "closing_captured_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    sb.table("card_legs").update(payload).eq("id", leg_id).execute()


# ────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────

def process_row(
    row: dict,
    table: str,
    first_pitches: dict[int, datetime],
):
    """
    Generic row processor for pods or card_legs.

    Returns one of:
      'captured'      — successfully wrote closing_odds + CLV fields
      'too_early'     — game is more than CLOSING_LOOKBACK_MINUTES out;
                        no closing snapshot exists yet
      'no_snapshot'   — game time is in/past the window but we have no
                        qualifying pre-FP snapshot (DK pulled the market,
                        rare data gap, or first pitch was missed)
      'no_lock_odds'  — pod/leg has no american_odds (unusual; shouldn't
                        happen for picks that made it through pick_pod)
      'skip'          — already captured, or missing context (no game row,
                        no market mapping)
    """
    if row.get("closing_odds") is not None and row.get("closing_captured_at") is not None:
        return "skip"   # already captured (also filtered at query time)

    if row.get("american_odds") is None:
        return "no_lock_odds"

    game_id = row.get("game_id")
    first_pitch = first_pitches.get(game_id)
    if not first_pitch:
        return "skip"   # game data missing

    now = datetime.now(timezone.utc)
    if now < first_pitch - timedelta(minutes=CLOSING_LOOKBACK_MINUTES):
        return "too_early"

    # Note: we DON'T early-return if game has already started. Closing
    # snapshots are taken BEFORE first_pitch and may be queryable for
    # hours/days afterward. If a previous capture run missed the window
    # we want to try again on later runs.

    market = row.get("market")
    line = row.get("line")   # only card_legs have a `line` field on the row itself

    # For pods, line is encoded in the market string (e.g. 'h_r_rbi_1.5').
    # market_lookup() handles both cases.
    snapshot_market, snapshot_line = market_lookup(market, line)
    if snapshot_market is None:
        log.warning("[%s id=%s] couldn't map market '%s' line=%s", table, row.get("id"), market, line)
        return "skip"

    # The `line` value in the snapshot query is normally derived from
    # market_lookup() based on the market string. card_legs ALSO carries
    # an explicit `line` column — if present, it overrides the lookup's
    # inferred line (defensive: trust the source row over our inference).
    if line is not None:
        snapshot_line = line

    snapshot = fetch_closing_snapshot(
        game_id=game_id,
        player_id=row["player_id"],
        snapshot_market=snapshot_market,
        snapshot_line=snapshot_line,
        first_pitch=first_pitch,
    )
    if not snapshot:
        return "no_snapshot"

    closing_american = snapshot.get("american_odds")
    if closing_american is None:
        return "no_snapshot"

    fields = compute_clv_fields(
        lock_american=row["american_odds"],
        lock_no_vig=row.get("no_vig_prob"),
        closing_american=closing_american,
        snapshot_market=snapshot_market,
    )

    if table == "pods":
        update_pod(row["id"], fields, closing_american)
    else:
        update_card_leg(row["id"], fields, closing_american)

    return "captured"


def main():
    log.info("🧅 Cebolla — capture_closing_lines starting")
    today = get_today_iso()
    # Look back 2 days in case some pending picks from yesterday haven't settled
    # and we missed their closing capture window.
    lookback = (datetime.fromisoformat(today) - timedelta(days=2)).date().isoformat()
    log.info("Scanning pending pods/cards from %s onward", lookback)

    pods = fetch_pending_pods(lookback)
    legs = fetch_pending_card_legs(lookback)
    log.info("Pending: %d pods, %d card_legs", len(pods), len(legs))

    if not pods and not legs:
        log.info("Nothing to capture. Done.")
        return

    # Collect all game IDs to look up first pitches
    game_ids = list({r["game_id"] for r in pods if r.get("game_id")} |
                    {r["game_id"] for r in legs if r.get("game_id")})
    first_pitches = fetch_game_first_pitches(game_ids)
    log.info("Loaded first-pitch times for %d games", len(first_pitches))

    counters = {
        "captured": 0, "too_early": 0,
        "no_snapshot": 0, "no_lock_odds": 0, "skip": 0,
    }

    for pod in pods:
        result = process_row(pod, "pods", first_pitches)
        counters[result] = counters.get(result, 0) + 1
        if result == "captured":
            log.info("  ✓ pod #%s (game %s, player %s) captured", pod["id"], pod["game_id"], pod["player_id"])

    for leg in legs:
        result = process_row(leg, "card_legs", first_pitches)
        counters[result] = counters.get(result, 0) + 1
        if result == "captured":
            log.info("  ✓ leg #%s (game %s, player %s) captured", leg["id"], leg["game_id"], leg["player_id"])

    log.info("─── Summary ───")
    log.info("  captured:     %d", counters["captured"])
    log.info("  too_early:    %d  (game not within %dmin of first pitch yet)", counters["too_early"], CLOSING_LOOKBACK_MINUTES)
    log.info("  no_snapshot:  %d  (no DK odds rows in window — book pulled the market?)", counters["no_snapshot"])
    log.info("  no_lock_odds: %d  (pod/leg had no american_odds — unusual)", counters["no_lock_odds"])
    log.info("  skip:         %d  (already captured or missing context)", counters["skip"])
    log.info("🧅 capture_closing_lines complete")


if __name__ == "__main__":
    main()
