"""
pitcher_lookup.py — v2 pitcher HR-metric lookup (Day 4).

get_pitcher_hr_metrics(pitcher_id, window='l30')
  -> {hr_per_9, fb_pct, hr_per_fb, data_age_days, is_fallback}

Recent-form homer-proneness for the v2 HR matchup. Reads pitcher_stats
(season=2026, given window). PER-FIELD league-average fallback (a window row
may carry fb_pct/hr_per_fb from Statcast but a null hr_per_9 if the MLB-API L30
pull didn't cover that pitcher) → is_fallback flags any defaulted field.

data_age_days = days since the pitcher's most recent FINAL start (from the games
table — there is no pitcher_game_log; games.home/away_pitcher_id is the
authoritative starter record, which is exactly the v2 use case). None if no
final start is found. Lets the Day-5 model warn on stale/injured arms.

Pure resolver (resolve_metrics) for DB-free unit tests + a cached DB wrapper,
mirroring park_factor_lookup / weather_lookup.
"""

import os
from datetime import datetime, timezone, timedelta, date
from typing import Optional

# Fallback values are STATCAST scale, not FanGraphs scale.
# Statcast classifies fly balls more strictly than FanGraphs (excludes line
# drives, separates popups), so measured Statcast values run lower on FB% and
# higher on HR/FB. Leaguewide L30 medians from bbe>=30 sample (2026 season).
# Recalibrate annually as part of season-rollover checklist.
LEAGUE_AVG = {"hr_per_9": 1.2, "fb_pct": 26.5, "hr_per_fb": 16.5}
_SEASON = 2026
_FINAL_STATUSES = ["Final", "Game Over", "Completed Early"]


def resolve_metrics(row: Optional[dict],
                    last_appearance_date: Optional[date] = None,
                    today: Optional[date] = None) -> dict:
    """
    Pure resolver. `row` is a pitcher_stats row (or None). Each metric falls back
    to its league average if missing; is_fallback is True if ANY field defaulted.
    data_age_days = (today - last_appearance_date).days when both are given.
    """
    out = {}
    fallback = False
    for key, default in LEAGUE_AVG.items():
        v = row.get(key) if row else None
        if v is None:
            out[key] = default
            fallback = True
        else:
            out[key] = float(v)
    age = None
    if last_appearance_date is not None and today is not None:
        age = (today - last_appearance_date).days
    out["data_age_days"] = age
    out["is_fallback"] = fallback
    return out


# ── DB-backed wrapper ────────────────────────────────────────────────────
_sb = None
_cache: dict = {}


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _sb


def _last_appearance_date(pitcher_id: int, sb, ) -> Optional[date]:
    """Most recent FINAL game this pitcher started (games.home/away_pitcher_id)."""
    res = sb.table("games").select("game_date, status") \
        .or_(f"home_pitcher_id.eq.{pitcher_id},away_pitcher_id.eq.{pitcher_id}") \
        .in_("status", _FINAL_STATUSES) \
        .order("game_date", desc=True).limit(1).execute()
    rows = res.data or []
    if not rows or not rows[0].get("game_date"):
        return None
    try:
        return date.fromisoformat(rows[0]["game_date"][:10])
    except (ValueError, TypeError):
        return None


def get_pitcher_hr_metrics(pitcher_id: int, window: str = "l30",
                           sb=None, today: Optional[date] = None) -> dict:
    """HR metrics + freshness for a pitcher. Cached per (pitcher_id, window)."""
    ck = (pitcher_id, window)
    if ck in _cache:
        return _cache[ck]
    client = sb or _client()
    if today is None:
        today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    res = client.table("pitcher_stats").select("hr_per_9, fb_pct, hr_per_fb") \
        .eq("pitcher_id", pitcher_id).eq("season", _SEASON) \
        .eq("window_type", window).limit(1).execute()
    row = (res.data or [None])[0]
    metrics = resolve_metrics(row, _last_appearance_date(pitcher_id, client), today)
    _cache[ck] = metrics
    return metrics


# ── Unit self-tests (pure resolver; no DB) ───────────────────────────────
def _run_selftests():
    today = date(2026, 6, 16)

    # Known pitcher, full row, fresh (started 2 days ago) → passthrough, no fallback.
    full = {"hr_per_9": 0.78, "fb_pct": 28.5, "hr_per_fb": 9.1}
    m = resolve_metrics(full, date(2026, 6, 14), today)
    assert m["hr_per_9"] == 0.78 and m["fb_pct"] == 28.5 and m["hr_per_fb"] == 9.1
    assert m["is_fallback"] is False and m["data_age_days"] == 2

    # Missing pitcher (no row) → all league averages (Statcast scale), is_fallback True.
    m = resolve_metrics(None, None, today)
    assert m == {"hr_per_9": 1.2, "fb_pct": 26.5, "hr_per_fb": 16.5,
                 "data_age_days": None, "is_fallback": True}

    # Partial data: Statcast gave fb_pct/hr_per_fb but hr_per_9 null (no MLB L30).
    part = {"hr_per_9": None, "fb_pct": 41.0, "hr_per_fb": 15.0}
    m = resolve_metrics(part, date(2026, 6, 10), today)
    assert m["hr_per_9"] == 1.2 and m["fb_pct"] == 41.0 and m["hr_per_fb"] == 15.0
    assert m["is_fallback"] is True and m["data_age_days"] == 6

    # Stale data scenario: last start 25 days ago.
    m = resolve_metrics(full, date(2026, 5, 22), today)
    assert m["data_age_days"] == 25 and m["is_fallback"] is False

    # Fresh, but no last-appearance info → data_age_days None.
    m = resolve_metrics(full, None, today)
    assert m["data_age_days"] is None and m["is_fallback"] is False

    print("pitcher_lookup self-tests: ALL PASSED")


if __name__ == "__main__":
    _run_selftests()
