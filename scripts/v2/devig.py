"""
devig.py — v2 single-sided no-vig wrapper (Day 5).

Thin wrapper around compute_projections.devig_anytime (the calibrated dynamic
vig curve). We only have the YES price for HR props (DK posts no No/Under side),
so two-sided multiplicative devig isn't possible — single-sided is the method
(Decision 1). Returns the implied prob, the vig-stripped fair prob, and the vig
that was removed. no_vig_prob is None when the odds are longshot-filtered
(beyond the market's trust threshold) or missing.
"""

import os
import sys

# compute_projections lives in scripts/ (our parent); ensure it's importable
# whether this module is imported as v2.devig or run directly for self-tests.
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from compute_projections import american_to_implied, devig_anytime


def devig_yes_only(american_odds, market: str = "hr_anytime") -> dict:
    """
    Single-sided no-vig for a Yes prop.

    Returns {implied_prob, no_vig_prob, vig_pct}. no_vig_prob/vig_pct are None
    when the line is longshot-filtered (devig_anytime returns None) or the odds
    are missing/invalid. vig_pct is the fraction removed (implied/no_vig − 1).
    """
    if american_odds is None:
        return {"implied_prob": None, "no_vig_prob": None, "vig_pct": None}
    implied = american_to_implied(american_odds)
    no_vig = devig_anytime(american_odds, implied, market=market)
    vig_pct = None
    if implied is not None and no_vig is not None and no_vig > 0:
        vig_pct = implied / no_vig - 1.0
    return {
        "implied_prob": round(implied, 5) if implied is not None else None,
        "no_vig_prob":  round(no_vig, 5) if no_vig is not None else None,
        "vig_pct":      round(vig_pct, 5) if vig_pct is not None else None,
    }


def _run_selftests():
    def approx(a, b, t=1e-4): return a is not None and abs(a - b) < t

    # Even odds +100 → implied .5, HR curve vig 5% → no_vig .5/1.05
    r = devig_yes_only(100)
    assert approx(r["implied_prob"], 0.5) and approx(r["no_vig_prob"], 0.47619) and approx(r["vig_pct"], 0.05)
    # Deep favorite -300 → implied .75, vig 5% (<=200) → .75/1.05
    r = devig_yes_only(-300)
    assert approx(r["implied_prob"], 0.75) and approx(r["no_vig_prob"], 0.71429)
    # Mid +600 → implied .142857, vig 10% (500<x<=1000) → /1.10
    r = devig_yes_only(600)
    assert approx(r["implied_prob"], 0.142857) and approx(r["no_vig_prob"], 0.12987) and approx(r["vig_pct"], 0.10)
    # Longshot +2500 (>= HR threshold 2000) → no_vig None (filtered)
    r = devig_yes_only(2500)
    assert r["implied_prob"] is not None and r["no_vig_prob"] is None and r["vig_pct"] is None
    # Missing odds → all None
    assert devig_yes_only(None) == {"implied_prob": None, "no_vig_prob": None, "vig_pct": None}
    print("devig self-tests: ALL PASSED")


if __name__ == "__main__":
    _run_selftests()
