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

When this should run:
  - Cron every 15 min during slate window (e.g. 1pm-11pm ET)
  - OR once after the slate is fully complete (more reliable, less stale)
  - Currently we run every 30 min from 1pm-11pm ET via daily-pulls.yml

What gets captured:
  - For each pending pod/card_leg
  - Find the latest odds_snapshot for that (game_id, player_id, market) tuple
  - Where snapshot_time < first_pitch_utc (game hasn't started yet)
  - Where snapshot_time >= first_pitch_utc - CLOSING_WINDOW (recent enough)
  - Copy american_odds → closing_odds
  - Compute closing_implied + closing_no_vig
  - Compute clv_raw + clv_no_vig (vs the lock-time american_odds/no_vig_prob)

Output columns written:
  - closing_odds (INT)
  - closing_implied (NUMERIC)
  - closing_no_vig (NUMERIC) — for one-sided markets, uses standard overround estimate
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
# CONSTANTS
# ────────────────────────────────────────────────────────────────────────

# How recent does the closing snapshot need to be (before first pitch)?
# A snapshot from 4 hours ago isn't really "closing odds" — it's mid-day odds.
# We want the LAST snapshot before first pitch, which on our hourly cron is
# at most 60 minutes old. 90 min ceiling gives some slack.
CLOSING_WINDOW_MINUTES = 90

# How many minutes before first pitch does the "closing line" window start?
# After this point, snapshots count as "closing odds" — we'll prefer the
# latest one in the window.
CLOSING_LOOKBACK_MINUTES = 90

# HR Anytime is a one-sided market on DK (no "won't HR" leg posted).
# We approximate de-vigging by dividing by the typical HR market overround.
# Standard DK juice on HR props is ~108-110% total book — i.e. ~8-10% over-
# round on a single-sided market is the implicit "yes + no" inflation.
# We use 4.5% as a conservative single-side overround estimate.
HR_ANYTIME_OVERROUND = 0.045

# Market name mapping: pods.market and card_legs.market sometimes differ
# from odds_snapshots.market by suffix. Build a lookup so we match cleanly.
#
# pods.market values seen in production:
#   'hr_anytime'         — single-sided
#   'h_r_rbi_1.5_yes'    — HRR line 1.5
#   'h_r_rbi_2.5_yes'    — HRR line 2.5
#   'h_r_rbi_3.5_yes'    — HRR line 3.5
#   'hits_yes'           — 1+ hits
#   'rbi_yes'            — 1+ RBI
#
# odds_snapshots.market values used by pull_dk_odds.py:
#   'hr_anytime_yes', 'hr_anytime_no'
#   'h_r_rbi_yes', 'h_r_rbi_no'  (with .line column for the 1.5/2.5/3.5 split)
#   'hits_yes', 'hits_no'
#   'rbi_yes', 'rbi_no'
#
# So the join is:
#   - For HR: market='hr_anytime_yes'
#   - For HRR: market='h_r_rbi_yes' AND line=<1.5|2.5|3.5>
#   - For hits: market='hits_yes' AND line=0.5
#   - For rbi: market='rbi_yes' AND line=0.5


def american_to_implied(american: int | None) -> float | None:
    """American odds → raw implied probability (still has vig)."""
    if american is None:
        return None
    if american > 0:
        return 100.0 / (american + 100.0)
    if american < 0:
        return abs(american) / (abs(american) + 100.0)
    return None


def devig_two_sided(yes_implied: float, no_implied: float | None) -> float | None:
    """
    De-vig a two-sided market by normalizing yes+no=1.

    If we don't have the 'no' side, fall back to single-side approximation
    using HR_ANYTIME_OVERROUND.
    """
    if yes_implied is None:
        return None
    if no_implied is None:
        # Single-sided — approximate
        return yes_implied / (1.0 + HR_ANYTIME_OVERROUND)
    total = yes_implied + no_implied
    if total <= 0:
        return None
    return yes_implied / total


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
    """All PODs from today/recent dates with status='pending' and no closing_odds yet."""
    res = sb.table("pods").select(
        "id, pod_date, game_id, player_id, market, american_odds, no_vig_prob, "
        "closing_odds, closing_captured_at"
    ).eq("status", "pending") \
     .gte("pod_date", date_iso) \
     .execute()
    return res.data or []


def fetch_pending_card_legs(date_iso: str) -> list[dict]:
    """All card_legs from pending cards on today/recent dates without closing_odds.

    Done as two queries (matches settle_cards.py pattern):
      1. Pull pending cards within date window
      2. Pull their legs by card_id
    """
    cards_res = sb.table("cards").select("id, card_date, status") \
        .eq("status", "pending") \
        .gte("card_date", date_iso) \
        .execute()
    cards = cards_res.data or []
    if not cards:
        return []
    card_ids = [c["id"] for c in cards]

    legs_res = sb.table("card_legs").select(
        "id, card_id, game_id, player_id, market, line, american_odds, no_vig_prob, "
        "closing_odds, closing_captured_at"
    ).in_("card_id", card_ids).execute()
    return legs_res.data or []


def fetch_game_first_pitches(game_ids: list[int]) -> dict[int, datetime]:
    """Map game_id → first_pitch UTC datetime."""
    if not game_ids:
        return {}
    res = sb.table("games").select("id, game_time_utc, status") \
        .in_("id", game_ids) \
        .execute()
    out = {}
    for g in res.data or []:
        if g.get("game_time_utc"):
            # Postgres TIMESTAMPTZ comes back as ISO string with offset
            fp = datetime.fromisoformat(g["game_time_utc"].replace("Z", "+00:00"))
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
    Also looks up the "no" side counterpart for de-vigging.
    """
    window_start = first_pitch - timedelta(minutes=CLOSING_LOOKBACK_MINUTES)

    q = sb.table("odds_snapshots").select(
        "american_odds, decimal_odds, implied_prob, line, market, snapshot_time"
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

    snapshot = rows[0]

    # Try to also find the "no" side at the same snapshot_time for de-vig.
    # Markets ending in "_yes" have a corresponding "_no" sister.
    no_implied = None
    if snapshot_market.endswith("_yes"):
        no_market = snapshot_market[:-4] + "_no"
        q2 = sb.table("odds_snapshots").select("implied_prob") \
            .eq("game_id", game_id) \
            .eq("player_id", player_id) \
            .eq("market", no_market) \
            .eq("book", "draftkings") \
            .lt("snapshot_time", first_pitch.isoformat()) \
            .gte("snapshot_time", window_start.isoformat()) \
            .order("snapshot_time", desc=True) \
            .limit(1)
        if snapshot_line is not None:
            q2 = q2.eq("line", snapshot_line)
        res2 = q2.execute()
        if res2.data:
            no_implied = res2.data[0].get("implied_prob")
            if no_implied is not None:
                no_implied = float(no_implied)

    snapshot["_no_implied"] = no_implied
    return snapshot


# ────────────────────────────────────────────────────────────────────────
# CLV COMPUTATION
# ────────────────────────────────────────────────────────────────────────

def compute_clv_fields(
    lock_american: int,
    lock_no_vig: float | None,
    closing_american: int,
    closing_no_implied: float | None,
) -> dict:
    """
    Given lock-time american odds + lock-time no_vig probability,
    and closing american odds + closing 'no' side implied (for de-vig),
    compute all the calibration columns.

    Returns dict with keys: closing_implied, closing_no_vig, clv_raw, clv_no_vig.
    """
    closing_implied = american_to_implied(closing_american)
    lock_implied = american_to_implied(lock_american)

    closing_no_vig = devig_two_sided(closing_implied, closing_no_implied)

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
    Returns 'captured' | 'too_early' | 'too_late' | 'no_snapshot' | 'no_lock_odds' | 'skip'.
    """
    if row.get("closing_odds") is not None and row.get("closing_captured_at") is not None:
        return "skip"   # already captured

    if row.get("american_odds") is None:
        return "no_lock_odds"

    game_id = row.get("game_id")
    first_pitch = first_pitches.get(game_id)
    if not first_pitch:
        return "skip"   # game data missing

    now = datetime.now(timezone.utc)
    if now < first_pitch - timedelta(minutes=CLOSING_LOOKBACK_MINUTES):
        return "too_early"

    if now >= first_pitch + timedelta(minutes=120):
        # Game well in progress or over — no point capturing now,
        # but we COULD if a snapshot exists from before first pitch.
        # We allow it because the snapshot itself is timestamped pre-FP.
        pass

    market = row.get("market")
    line = row.get("line")   # only card_legs have a `line` field on the row itself

    # For pods, line is encoded in the market string (e.g. 'h_r_rbi_1.5_yes').
    # market_lookup() handles both cases.
    snapshot_market, snapshot_line = market_lookup(market, line)
    if snapshot_market is None:
        log.warning("[%s id=%s] couldn't map market '%s' line=%s", table, row.get("id"), market, line)
        return "skip"

    # If the row's `line` was None but market_lookup inferred one (e.g. 1.5),
    # use the inferred one. If row.line is set, prefer it (card_legs).
    if line is None and snapshot_line is not None:
        snapshot_line = snapshot_line
    elif line is not None:
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
        closing_no_implied=snapshot.get("_no_implied"),
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
        "captured": 0, "too_early": 0, "too_late": 0,
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
