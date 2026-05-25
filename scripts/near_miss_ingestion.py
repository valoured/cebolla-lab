"""
near_miss_ingestion.py — STUB (Patch 6).

Populates the `near_miss_events` table with "would-be home run" batted balls:
hard contact that stayed in the park / would have been a HR in N of 30 parks.
These feed getNearMissBoost() in pick_pod.py (a recent-hard-contact boost).

STATUS: STUB ONLY. No data source is wired up yet — by design (the schema and
the boost reader ship first; the feed is wired separately). Running this today
is a safe no-op.

═══════════════════════════════════════════════════════════════════════════════
TODO — wire a real near-miss data source, then upsert into near_miss_events:
  · Option A: Statcast (pybaseball.statcast) — filter recent batted balls by
    exit_velocity_mph >= ~100 and a launch_angle window (~20-35°) that didn't
    result in a HR but had high hit-probability / long projected distance.
  · Option B: an "MLBNearHR"-style feed of robbed/warning-track/doinked HRs.

  For each event, insert a row matching the migration-23 schema:
    batter_id (INTEGER FK players.id), pitcher_id, game_id, game_date,
    pitch_type, exit_velocity_mph, launch_angle_deg, distance_ft, ballpark,
    hr_in_n_parks, result_text.

  Map external player ids (MLBAM) → players.id before insert (the boost reader
  joins on players.id). Make ingestion idempotent (don't double-insert the same
  batted-ball event across runs).
═══════════════════════════════════════════════════════════════════════════════
"""

import logging

log = logging.getLogger("near_miss_ingestion")


def ingest_near_misses(sb, lookback_days: int = 7) -> int:
    """
    STUB. Will pull recent near-miss batted balls and upsert into
    near_miss_events. Returns the number of rows written (0 until wired up).

    Args:
        sb: configured Supabase client (service role).
        lookback_days: how far back to scan the source feed.

    Returns:
        int — rows inserted (always 0 in the stub).
    """
    # TODO: implement per the module docstring. Intentionally a no-op for now.
    log.info("near_miss_ingestion is a stub — no data source wired yet (no-op).")
    return 0


def main():
    """Entry point for the (future) GitHub Actions / Worker-dispatched job."""
    log.info("near_miss_ingestion stub invoked — nothing to do yet.")


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    main()
