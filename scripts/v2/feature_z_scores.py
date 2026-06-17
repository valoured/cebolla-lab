"""
feature_z_scores.py — v2 z-score normalization from feature_baselines (Day 5).

z(feature, value, window='season') -> float

Reads the feature_baselines table (mean/std per feature per window, refreshed
weekly by compute_feature_baselines.py) once and caches it per process.

MISSING-BASELINE POLICY (documented choice): return **0.0 (neutral z)** and log
a one-time WARNING — never raise. A missing baseline means "this feature exerts
no effect this run" rather than crashing the whole slate; the warning surfaces
the config gap so it gets fixed. Same neutral-0.0 for std==0 or value is None.
"""

import os
import logging
from typing import Optional

log = logging.getLogger("feature_z_scores")

_sb = None
_cache: Optional[dict] = None      # {(feature, window): (mean, std, n)}
_warned: set = set()


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _sb


def _load(sb=None) -> dict:
    global _cache
    if _cache is not None:
        return _cache
    rows = (sb or _client()).table("feature_baselines") \
        .select("feature, window, mean, std, n").execute().data or []
    _cache = {
        (r["feature"], r["window"]): (
            float(r["mean"]) if r.get("mean") is not None else None,
            float(r["std"]) if r.get("std") is not None else None,
            r.get("n"),
        )
        for r in rows
    }
    return _cache


def reset_cache():
    """Clear the per-process cache (tests / after a baseline refresh)."""
    global _cache
    _cache = None


def z(feature: str, value, window: str = "season", sb=None) -> float:
    """Standardize `value` for (feature, window). 0.0 (neutral) on any
    non-computable case (missing baseline, std==0, or value None)."""
    if value is None:
        return 0.0
    base = _load(sb).get((feature, window))
    if base is None:
        key = (feature, window)
        if key not in _warned:
            log.warning("feature_baselines missing for %s/%s — z=0.0 (neutral). "
                        "Run compute_feature_baselines.py.", feature, window)
            _warned.add(key)
        return 0.0
    mean, std, _ = base
    if mean is None or std is None or std == 0:
        return 0.0
    return (float(value) - mean) / float(std)


def _run_selftests():
    global _cache
    _cache = {
        ("pulled_airball_rate", "season"): (17.294, 5.708, 231),
        ("hr_per_9", "l30"): (1.207, 0.806, 306),
        ("flat_feature", "season"): (10.0, 0.0, 5),   # std 0 → neutral
    }
    def approx(a, b, t=1e-3): return abs(a - b) < t
    # normal z
    assert approx(z("pulled_airball_rate", 23.0, "season"), (23.0 - 17.294) / 5.708)
    assert approx(z("hr_per_9", 1.207, "l30"), 0.0)         # at the mean → 0
    assert approx(z("hr_per_9", 2.013, "l30"), 1.0)         # +1 std
    # graceful cases → 0.0
    assert z("missing_feature", 5.0, "season") == 0.0       # no baseline row
    assert z("pulled_airball_rate", 23.0, "l30") == 0.0     # wrong window, no row
    assert z("flat_feature", 99.0, "season") == 0.0         # std 0
    assert z("hr_per_9", None, "l30") == 0.0                # value None
    print("feature_z_scores self-tests: ALL PASSED")
    _cache = None


if __name__ == "__main__":
    _run_selftests()
