"""
compute_feature_baselines.py — populate feature_baselines for v2 z-scores.

v2 rebuild Day 5. Computes leaguewide mean + sample-stdev (ddof=1, matching the
validation harness) per feature per window from current batter_stats /
pitcher_stats, and upserts into feature_baselines. Consumed by
scripts/v2/feature_z_scores.py → hr_model.py.

CRON: WEEKLY (these distributions drift slowly). Suggested GitHub Actions:
  schedule 'compute_feature_baselines' weekly (e.g. Mondays 09:00 UTC),
  after the savant/pitcher pulls have refreshed the underlying stats.
Re-run anytime; upsert on (feature, window) makes it idempotent.

Baselines seeded (feature, window, sample filter) — filters MATCH the Day-5
harness so the populated values reproduce the validated calibration:
  pulled_airball_rate  season  batter  pa  >= 50
  hr_per_9             l30     pitcher ip  >= 10
  hr_per_fb            l30     pitcher bbe >= 20
  fb_pct              l30     pitcher bbe >= 20
NOTE: these sample filters are for the LEAGUE BASELINE only; the per-pick
sample dampening in hr_model (ip<20 / bbe<30) is separate.

V2.1 ROADMAP (documented, not shipped): mixed-window pitcher features —
  L30 hr_per_9 + SEASON hr_per_fb + SEASON fb_pct (stabler batted-ball traits,
  less L30 noise). Requires reading pitcher_lookup at two windows, dampening
  only L30 hr_per_9, and a NEW calibration sweep (likely a new MODEL_INTERCEPT_C).
  Defer until 200 shadow bets validate v2.0.
"""

import os
import sys
import logging
import statistics as st
from datetime import datetime, timezone

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("compute_feature_baselines")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
SEASON = 2026
PAGE = 1000


def _paginate(table, select, **eqs):
    out, off = [], 0
    while True:
        q = sb.table(table).select(select).eq("season", SEASON)
        for k, v in eqs.items():
            q = q.eq(k, v)
        rows = q.range(off, off + PAGE - 1).execute().data or []
        out.extend(rows)
        if len(rows) < PAGE:
            break
        off += PAGE
    return out


def _mstd(vals):
    vals = [float(v) for v in vals if v is not None]
    if len(vals) < 2:
        return None, None, len(vals)
    return round(st.mean(vals), 5), round(st.stdev(vals), 5), len(vals)


def main():
    log.info("🧅 compute_feature_baselines — season %d", SEASON)
    rows_out = []

    # ── batter: pulled_airball_rate (season, pa>=50) ──
    brows = _paginate("batter_stats", "pa, pulled_airball_rate",
                      window_type="season", vs_hand="A")
    bvals = [r["pulled_airball_rate"] for r in brows
             if (r.get("pa") or 0) >= 50 and r.get("pulled_airball_rate") is not None]
    m, s, n = _mstd(bvals)
    rows_out.append({"feature": "pulled_airball_rate", "window": "season",
                     "mean": m, "std": s, "n": n})

    # ── pitcher (l30): hr_per_9 (ip>=10), hr_per_fb / fb_pct (bbe>=20) ──
    prows = _paginate("pitcher_stats", "innings_pitched, bbe, hr_per_9, hr_per_fb, fb_pct",
                      window_type="l30")
    m, s, n = _mstd([r["hr_per_9"] for r in prows
                     if (r.get("innings_pitched") or 0) >= 10 and r.get("hr_per_9") is not None])
    rows_out.append({"feature": "hr_per_9", "window": "l30", "mean": m, "std": s, "n": n})
    for feat in ("hr_per_fb", "fb_pct"):
        m, s, n = _mstd([r[feat] for r in prows
                         if (r.get("bbe") or 0) >= 20 and r.get(feat) is not None])
        rows_out.append({"feature": feat, "window": "l30", "mean": m, "std": s, "n": n})

    now = datetime.now(timezone.utc).isoformat()
    for r in rows_out:
        r["computed_at"] = now

    sb.table("feature_baselines").upsert(rows_out, on_conflict="feature,window").execute()
    log.info("✓ Upserted %d feature_baselines rows", len(rows_out))
    for r in rows_out:
        log.info("  %-22s %-7s mean=%-9s std=%-9s n=%s",
                 r["feature"], r["window"], r["mean"], r["std"], r["n"])


if __name__ == "__main__":
    main()
