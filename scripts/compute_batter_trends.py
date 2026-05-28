#!/usr/bin/env python3
"""
compute_batter_trends.py
─────────────────────────────────────────────────────────────────

Computes per-batter multi-signal trend scores and writes them to
batter_trends. Designed to run daily before card selection so that
pick_cards can use Combined Heat as a tiebreaker for parlay legs.

Math mirrors useTrends.js exactly (computeTrendScore + computeCombinedTrend)
so the frontend Combined Heat number and the backend stored value will
always match.

USAGE:
    python compute_batter_trends.py

REQUIREMENTS:
    - Migration 19 applied (batter_trends table + batter_heat_today view)
    - batter_stats populated for both window_type='season' AND
      window_type='l14' (pull_savant.py handles this)

ENV VARS:
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY — required, standard
"""

from __future__ import annotations

import logging
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from supabase import create_client, Client

# ──────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("trends")

# Min L14 PA to compute trends for a batter. Below this the rate is
# too noisy to trust. Matches the frontend default in useTrends.js
# so client and server agree on who qualifies.
MIN_L14_PA = 20

# Clamp bounds for individual metric trends before geometric mean.
# Match useTrends.js computeCombinedTrend.
CLAMP_FLOOR = -0.75
CLAMP_CEIL  = 2.0

# Minimum base metrics required for a Combined score. Below this we
# emit NULL rather than report a misleadingly-high "combined" that's
# really only based on 1-2 signals.
MIN_BASE_METRICS = 3

# Base metric keys, ordered. The first non-null metric becomes the
# anchor when HR/PA is zero (slap hitter case).
BASE_METRICS = ["hr", "hits", "barrel", "iso"]

# Tier thresholds (match useTrends.js).
TIER_BLAZING = 0.50
TIER_HOT     = 0.25
TIER_WARM    = 0.10
TIER_COOL    = -0.10
TIER_COLD    = -0.25
TIER_FROZEN  = -0.50


def get_supabase() -> Client:
    """Standard Supabase client setup. Service role required for upserts."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY required")
        sys.exit(1)
    return create_client(url, key)


def get_today_iso() -> str:
    """
    ET-relative slate date. Matches pick_cards.py / pick_pod.py exactly so
    a trend snapshot's trend_date aligns with the card_date for joins.
    -4 = EDT (summer); -5 would be EST. Currently May 2026 = EDT.
    """
    et_offset = timedelta(hours=-4)
    return (datetime.now(timezone.utc) + et_offset).date().isoformat()


# ──────────────────────────────────────────────────────────────────
# Metric extractors — must match useTrends.js METRIC_EXTRACTORS
#
# Each takes a batter_stats row dict and returns the raw metric value
# normalized to a decimal (0-1) scale. Returns None when missing.
# ──────────────────────────────────────────────────────────────────

def extract_hr(row: dict) -> Optional[float]:
    v = row.get("hr_per_pa")
    return float(v) if v is not None else None

def extract_hits(row: dict) -> Optional[float]:
    v = row.get("hit_per_pa")
    return float(v) if v is not None else None

def extract_barrel(row: dict) -> Optional[float]:
    """
    barrel_pct in batter_stats is stored as percent (e.g. 12.3, not 0.123).
    Divide by 100 to keep all metrics on the same 0-1 scale.
    """
    v = row.get("barrel_pct")
    return float(v) / 100.0 if v is not None else None

def extract_iso(row: dict) -> Optional[float]:
    v = row.get("iso")
    return float(v) if v is not None else None

EXTRACTORS = {
    "hr":     extract_hr,
    "hits":   extract_hits,
    "barrel": extract_barrel,
    "iso":    extract_iso,
}


# ──────────────────────────────────────────────────────────────────
# Math — must match useTrends.js exactly
# ──────────────────────────────────────────────────────────────────

def compute_trend_score(l14: Optional[float], season: Optional[float]) -> Optional[float]:
    """
    Relative divergence of L14 from season baseline.
    Returns None when either window is None or season is essentially zero.

    Mirrors useTrends.js computeTrendScore exactly.
    """
    if l14 is None or season is None:
        return None
    if season < 0.0001:
        return None
    return (l14 - season) / season


def compute_combined_trend(per_metric_scores: dict) -> Optional[float]:
    """
    Geometric mean of (1 + clamped trend scores) - 1.

    Mirrors useTrends.js computeCombinedTrend exactly.
    Returns None when fewer than MIN_BASE_METRICS valid contributors.
    """
    valid = []
    for m in BASE_METRICS:
        ts = per_metric_scores.get(m)
        if ts is None or not math.isfinite(ts):
            continue
        clamped = max(CLAMP_FLOOR, min(CLAMP_CEIL, ts))
        valid.append(1.0 + clamped)

    if len(valid) < MIN_BASE_METRICS:
        return None

    # log-space for numerical stability
    log_sum = sum(math.log(x) for x in valid)
    geo_mean = math.exp(log_sum / len(valid))
    return geo_mean - 1.0


def classify_tier(combined: Optional[float]) -> Optional[str]:
    """Tier string from combined trend. Matches useTrends.js tier system."""
    if combined is None:
        return None
    if combined >= TIER_BLAZING:
        return "BLAZING"
    if combined >= TIER_HOT:
        return "HOT"
    if combined >= TIER_WARM:
        return "WARM"
    if combined <= TIER_FROZEN:
        return "FROZEN"
    if combined <= TIER_COLD:
        return "COLD"
    if combined <= TIER_COOL:
        return "COOL"
    return "FLAT"


def pick_anchor_metric(per_metric_data: dict) -> str:
    """
    Choose which base metric drives the L14/SZN display bars for
    Combined view. Prefer HR/PA — the most interpretable — but fall
    back to any other base metric that has non-zero data when HR is
    zero (slap hitters with no homers).
    """
    hr = per_metric_data.get("hr", {})
    if (hr.get("l14") or 0) > 0 or (hr.get("season") or 0) > 0:
        return "hr"
    for m in BASE_METRICS:
        d = per_metric_data.get(m, {})
        if (d.get("l14") or 0) > 0 or (d.get("season") or 0) > 0:
            return m
    return "hr"  # last resort, render will just show zeros


# ──────────────────────────────────────────────────────────────────
# Data load
# ──────────────────────────────────────────────────────────────────

# PostgREST default response cap is 1,000 rows per .execute(). The season
# window of batter_stats (vs_hand='A', current season) crossed 1,000 rows
# in mid-May 2026 — was 1,348 rows when this fix landed. Without pagination,
# ~348 batters silently lose their season baseline here, which breaks
# compute_trend_score (needs both season and l14) and drops them from
# batter_trends entirely (frontend heat display goes blank for those names).
# The picker no longer reads heat post-Phase 1, so this is a frontend-only
# repair. Paginate via .range() in a loop; PostgREST applies the ORDER BY
# before slicing so pagination is stable across pages and the dedupe-by-
# batter logic below still picks the most recent row per batter.
# DO NOT collapse this back to a single .select(...).execute().
_BATTER_STATS_PAGE = 1000


def load_batter_stats(sb: Client, window_type: str) -> list[dict]:
    """
    Pull all batter_stats rows for a given window. Returns one row per
    batter (the most recent snapshot for that window).

    Filters match useTrends.js exactly:
      - window_type matches the requested window ('season' or 'l14')
      - vs_hand='A' — All-handedness rows only (not L/R splits)
      - implicit current-season — batter_stats only stores current year per pull_savant
    """
    current_year = datetime.now(timezone.utc).year
    rows: list[dict] = []
    offset = 0
    while True:
        res = sb.table("batter_stats") \
            .select("*") \
            .eq("window_type", window_type) \
            .eq("vs_hand", "A") \
            .eq("season", current_year) \
            .order("window_end", desc=True) \
            .range(offset, offset + _BATTER_STATS_PAGE - 1) \
            .execute()
        chunk = res.data or []
        rows.extend(chunk)
        if len(chunk) < _BATTER_STATS_PAGE:
            break
        offset += _BATTER_STATS_PAGE

    # batter_stats can technically have multiple historical rows per
    # batter per window if the upsert key isn't tight. Dedupe by batter
    # keeping the most recent (already ordered desc, stable across pages).
    seen = set()
    out = []
    for r in rows:
        bid = r["batter_id"]
        if bid in seen:
            continue
        seen.add(bid)
        out.append(r)
    return out


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main():
    sb = get_supabase()
    today = get_today_iso()

    log.info("Loading batter_stats (season + L14)…")
    season_rows = load_batter_stats(sb, "season")
    l14_rows    = load_batter_stats(sb, "l14")

    season_by_batter = {r["batter_id"]: r for r in season_rows}
    l14_by_batter    = {r["batter_id"]: r for r in l14_rows}

    log.info("  season rows: %d", len(season_rows))
    log.info("  l14 rows:    %d", len(l14_rows))

    out_rows = []
    skipped_no_l14 = 0
    skipped_low_pa = 0
    skipped_no_combined = 0

    for batter_id, season_row in season_by_batter.items():
        l14_row = l14_by_batter.get(batter_id)
        if l14_row is None:
            skipped_no_l14 += 1
            continue

        pa_l14 = l14_row.get("pa") or 0
        if pa_l14 < MIN_L14_PA:
            skipped_low_pa += 1
            continue

        # ── Pass 1: per-metric trend scores ──
        per_metric_data = {}
        per_metric_ts = {}
        for m in BASE_METRICS:
            ext = EXTRACTORS[m]
            ml14 = ext(l14_row)
            mseason = ext(season_row)
            ts = compute_trend_score(ml14, mseason)
            per_metric_data[m] = {"l14": ml14, "season": mseason, "ts": ts}
            per_metric_ts[m] = ts

        # ── Pass 2: combined ──
        combined = compute_combined_trend(per_metric_ts)
        if combined is None:
            skipped_no_combined += 1
            # We still write the row — the per-metric trends are
            # useful even when Combined is null. Frontend can decide
            # whether to display.

        # ── Pass 3: anchor + tier ──
        anchor = pick_anchor_metric(per_metric_data)
        tier = classify_tier(combined)

        # NUMERIC clamp: Postgres NUMERIC(8,4) means we can safely store
        # values up to 9999.9999. Trend scores are typically -1 to +5,
        # so we're well within range. But protect against runaway data
        # by clamping to ±9999 before insert just in case.
        def safe_num(v):
            if v is None:
                return None
            if not math.isfinite(v):
                return None
            return max(-9999.0, min(9999.0, v))

        out_rows.append({
            "batter_id": batter_id,
            "trend_date": today,
            "hr_trend":     safe_num(per_metric_ts["hr"]),
            "hits_trend":   safe_num(per_metric_ts["hits"]),
            "barrel_trend": safe_num(per_metric_ts["barrel"]),
            "iso_trend":    safe_num(per_metric_ts["iso"]),
            "combined_trend": safe_num(combined),
            "combined_tier":  tier,
            "pa_l14":    pa_l14,
            "pa_season": season_row.get("pa") or 0,
            "anchor_metric": anchor,
            # Raw values for transparency / debugging
            "hr_per_pa_l14":     per_metric_data["hr"]["l14"],
            "hr_per_pa_season":  per_metric_data["hr"]["season"],
            "hit_per_pa_l14":    per_metric_data["hits"]["l14"],
            "hit_per_pa_season": per_metric_data["hits"]["season"],
            # barrel was divided by 100 for math; restore to percent for storage
            "barrel_pct_l14":    (per_metric_data["barrel"]["l14"] or 0) * 100 if per_metric_data["barrel"]["l14"] is not None else None,
            "barrel_pct_season": (per_metric_data["barrel"]["season"] or 0) * 100 if per_metric_data["barrel"]["season"] is not None else None,
            "iso_l14":    per_metric_data["iso"]["l14"],
            "iso_season": per_metric_data["iso"]["season"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    log.info("Computed %d trend rows", len(out_rows))
    log.info("  skipped: no L14 data = %d", skipped_no_l14)
    log.info("  skipped: low PA      = %d", skipped_low_pa)
    log.info("  combined=NULL (insufficient base metrics) = %d", skipped_no_combined)

    if not out_rows:
        log.warning("No rows to write. Exiting.")
        return

    # Tier distribution diagnostics
    tier_counts = {}
    for r in out_rows:
        t = r["combined_tier"] or "NULL"
        tier_counts[t] = tier_counts.get(t, 0) + 1
    log.info("Tier distribution:")
    for t in ["BLAZING", "HOT", "WARM", "FLAT", "COOL", "COLD", "FROZEN", "NULL"]:
        if t in tier_counts:
            log.info("  %-8s = %d", t, tier_counts[t])

    # Top 10 combined heat for sanity check
    sorted_rows = sorted(
        [r for r in out_rows if r["combined_trend"] is not None],
        key=lambda r: r["combined_trend"],
        reverse=True
    )
    log.info("Top 10 Combined Heat (sanity check):")
    name_map = {}
    if sorted_rows:
        ids = [r["batter_id"] for r in sorted_rows[:10]]
        pres = sb.table("players").select("id, name").in_("id", ids).execute()
        name_map = {p["id"]: p["name"] for p in pres.data or []}
    for r in sorted_rows[:10]:
        name = name_map.get(r["batter_id"], f"id={r['batter_id']}")
        log.info("  %-25s combined=%+6.1f%%  tier=%-8s  pa_l14=%d",
                 name[:25], r["combined_trend"] * 100, r["combined_tier"], r["pa_l14"])

    # Upsert in chunks (Supabase tolerates ~1000/req, we use 200 to be safe)
    log.info("Writing to batter_trends…")
    written = 0
    for i in range(0, len(out_rows), 200):
        chunk = out_rows[i:i+200]
        sb.table("batter_trends").upsert(
            chunk,
            on_conflict="batter_id,trend_date",
        ).execute()
        written += len(chunk)
    log.info("✓ Wrote %d rows to batter_trends", written)


if __name__ == "__main__":
    main()
