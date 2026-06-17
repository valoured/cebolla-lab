"""
weather_lookup.py — v2 weather HR-index lookup (Day 3).

get_weather_hr_index(game_id) -> float

Returns the latest available weather_hr_index for a game from game_weather,
preferring the '1h_before_first_pitch' snapshot over 'cron_330am' (and, within a
snapshot_type, the most recently fetched row). Falls back to a neutral 1.000
when no row exists. Mirrors park_factor_lookup: a pure resolver
(resolve_latest_index) for DB-free unit tests + a cached DB wrapper.
"""

import os
from typing import Optional

NEUTRAL = 1.000
# Preference order, best first: the closer-to-first-pitch forecast wins.
_PRIORITY = ("1h_before_first_pitch", "cron_330am")


def resolve_latest_index(rows: Optional[list]) -> float:
    """
    Pick the weather_hr_index from the best snapshot. `rows` is a list of dicts
    with snapshot_type, weather_hr_index, fetched_at. No DB.
    """
    if not rows:
        return NEUTRAL
    for st in _PRIORITY:
        cand = [r for r in rows
                if r.get("snapshot_type") == st and r.get("weather_hr_index") is not None]
        if cand:
            best = max(cand, key=lambda r: r.get("fetched_at") or "")
            return float(best["weather_hr_index"])
    # Unknown snapshot types but a usable value present → most recent non-null.
    valued = [r for r in rows if r.get("weather_hr_index") is not None]
    if valued:
        best = max(valued, key=lambda r: r.get("fetched_at") or "")
        return float(best["weather_hr_index"])
    return NEUTRAL


# ── DB-backed wrapper ────────────────────────────────────────────────────
_sb = None
_cache: dict[int, tuple[float, bool]] = {}


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _sb


def _lookup(game_id: int, sb=None) -> tuple[float, bool]:
    """(index, found) for game_id. found=False when no usable row exists → the
    neutral 1.000 returned is a FALLBACK, not a measured neutral. Cached."""
    if game_id in _cache:
        return _cache[game_id]
    client = sb or _client()
    res = client.table("game_weather") \
        .select("snapshot_type, weather_hr_index, fetched_at") \
        .eq("game_id", game_id).execute()
    rows = res.data or []
    found = any(r.get("weather_hr_index") is not None for r in rows)
    val = resolve_latest_index(rows)
    _cache[game_id] = (val, found)
    return val, found


def get_weather_hr_index(game_id: int, sb=None) -> float:
    """Latest weather_hr_index for game_id (neutral 1.000 if none). Cached per process."""
    return _lookup(game_id, sb)[0]


def get_weather_index_and_found(game_id: int, sb=None) -> tuple[float, bool]:
    """Like get_weather_hr_index but also reports whether a usable row existed."""
    return _lookup(game_id, sb)


# ── Unit self-tests (pure resolver; no DB) ───────────────────────────────
def _run_selftests():
    # Prefer 1h_before_first_pitch over cron_330am
    rows = [
        {"snapshot_type": "cron_330am", "weather_hr_index": 1.10, "fetched_at": "2026-06-16T07:30:00Z"},
        {"snapshot_type": "1h_before_first_pitch", "weather_hr_index": 1.22, "fetched_at": "2026-06-16T22:05:00Z"},
    ]
    assert resolve_latest_index(rows) == 1.22
    # Only cron_330am present → that value
    assert resolve_latest_index([
        {"snapshot_type": "cron_330am", "weather_hr_index": 0.93, "fetched_at": "x"}]) == 0.93
    # No rows → neutral 1.000
    assert resolve_latest_index([]) == NEUTRAL
    assert resolve_latest_index(None) == NEUTRAL
    # Null index in the preferred snapshot → fall through to cron_330am
    assert resolve_latest_index([
        {"snapshot_type": "1h_before_first_pitch", "weather_hr_index": None, "fetched_at": "z"},
        {"snapshot_type": "cron_330am", "weather_hr_index": 1.05, "fetched_at": "y"}]) == 1.05
    # Multiple of same type → most recent fetched_at wins
    assert resolve_latest_index([
        {"snapshot_type": "cron_330am", "weather_hr_index": 1.01, "fetched_at": "2026-06-16T07:00:00Z"},
        {"snapshot_type": "cron_330am", "weather_hr_index": 1.07, "fetched_at": "2026-06-16T07:30:00Z"}]) == 1.07
    print("weather_lookup self-tests: ALL PASSED")


if __name__ == "__main__":
    _run_selftests()
