"""
park_factor_lookup.py — v2 HR-model park-factor lookup (Day 2).

get_park_hr_factor(team_id, batter_bats, pitcher_throws) -> float

Returns the handedness-appropriate Statcast park HR factor on the INDEX scale
(100 = league average) for a batter playing at `team_id`'s park. The caller
(hr_score_v2, Day 5) converts index → multiplier (index / 100) as needed.

Switch-hitter handling (Option B): resolve S to the effective batting side from
the pitcher's hand — S vs RHP bats L (→ hr_factor_lhb), S vs LHP bats R
(→ hr_factor_rhb). Falls back to a neutral 100.0 when the park row is missing
(e.g. Savant-omitted relocated/temp venues), the side-specific value is null,
or the stance is unknown.

The pure resolver (`resolve_park_factor`) takes a park-row dict so it's unit
testable with no DB. `get_park_hr_factor` wraps it with a cached Supabase read
of the park_factors table.
"""

import os
from typing import Optional

NEUTRAL = 100.0                  # league-average index → no park effect
_SEASON = 2026
_WINDOW_YRS = 3
_SOURCE = "savant_3yr"


def _effective_side(batter_bats: Optional[str],
                    pitcher_throws: Optional[str]) -> Optional[str]:
    """
    Effective batting side. 'L'/'R' pass through; 'S' (switch) resolves to the
    opposite of the pitcher's hand (Option B): S vs RHP → 'L', S vs LHP → 'R'.
    Returns None when undeterminable (unknown bats, or S with unknown throws).
    """
    if batter_bats in ("L", "R"):
        return batter_bats
    if batter_bats == "S":
        if pitcher_throws == "R":
            return "L"
        if pitcher_throws == "L":
            return "R"
    return None


def resolve_park_factor(park_row: Optional[dict],
                        batter_bats: Optional[str],
                        pitcher_throws: Optional[str]) -> float:
    """
    Pure resolver: pick the right HR index from a park row given batter/pitcher
    handedness. No DB. `park_row` has hr_factor / hr_factor_lhb / hr_factor_rhb
    (any may be None). Fallback chain: side-specific → overall → NEUTRAL.
    """
    if not park_row:
        return NEUTRAL
    side = _effective_side(batter_bats, pitcher_throws)
    if side == "L":
        val = park_row.get("hr_factor_lhb")
    elif side == "R":
        val = park_row.get("hr_factor_rhb")
    else:
        val = park_row.get("hr_factor")          # unknown stance → overall
    if val is None:                              # side-specific missing → overall
        val = park_row.get("hr_factor")
    return float(val) if val is not None else NEUTRAL


# ── DB-backed wrapper ────────────────────────────────────────────────────
_sb = None
_row_cache: dict[int, Optional[dict]] = {}


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _sb


def _fetch_park_row(team_id: int, sb=None) -> Optional[dict]:
    if team_id in _row_cache:
        return _row_cache[team_id]
    client = sb or _client()
    res = client.table("park_factors") \
        .select("hr_factor, hr_factor_lhb, hr_factor_rhb, runs_factor") \
        .eq("team_id", team_id).eq("season", _SEASON) \
        .eq("window_yrs", _WINDOW_YRS).eq("source", _SOURCE) \
        .limit(1).execute()
    row = (res.data or [None])[0]
    _row_cache[team_id] = row
    return row


def get_park_hr_factor(team_id: int,
                       batter_bats: Optional[str],
                       pitcher_throws: Optional[str],
                       sb=None) -> float:
    """
    HR park factor (index, 100 = avg) for `batter_bats` at `team_id`'s park vs a
    `pitcher_throws` pitcher. Neutral 100.0 if no row exists. `sb` injectable for
    tests; results cached per team_id within the process.
    """
    return resolve_park_factor(_fetch_park_row(team_id, sb), batter_bats, pitcher_throws)


# ── Unit self-tests (pure resolver; no DB) ───────────────────────────────
def _run_selftests():
    # Fixture rows (illustrative index values; real numbers after prod upsert).
    coors  = {"hr_factor": 105.0, "hr_factor_lhb": 105.0, "hr_factor_rhb": 106.0}
    yankee = {"hr_factor": 119.0, "hr_factor_lhb": 124.0, "hr_factor_rhb": 112.0}
    citi   = {"hr_factor": 104.0, "hr_factor_lhb": 108.0, "hr_factor_rhb": 101.0}
    # GABP retired to REAL Day-2 DB values (CIN, season=2026, savant_3yr).
    gabp   = {"hr_factor": 123.0, "hr_factor_lhb": 130.0, "hr_factor_rhb": 118.0}

    # RHB at Coors → hr_factor_rhb
    assert resolve_park_factor(coors, "R", "R") == 106.0
    # LHB at Yankee Stadium → hr_factor_lhb
    assert resolve_park_factor(yankee, "L", "R") == 124.0
    # S + RHP at Citi Field → bats L → hr_factor_lhb
    assert resolve_park_factor(citi, "S", "R") == 108.0
    # S + LHP → bats R → hr_factor_rhb
    assert resolve_park_factor(citi, "S", "L") == 101.0
    # Athletics game (no row / Savant-omitted) → neutral 100
    assert resolve_park_factor(None, "R", "L") == NEUTRAL
    # Reds at GABP, RHB vs LHP → hr_factor_rhb (real verified DB value 118.0)
    assert resolve_park_factor(gabp, "R", "L") == 118.0
    # Unknown stance / S with unknown pitcher hand → overall
    assert resolve_park_factor(yankee, "S", None) == 119.0
    assert resolve_park_factor(yankee, None, None) == 119.0
    # Side value missing → fall back to overall
    assert resolve_park_factor({"hr_factor": 110.0, "hr_factor_lhb": None,
                                "hr_factor_rhb": None}, "L", "R") == 110.0
    print("park_factor_lookup self-tests: ALL PASSED")


if __name__ == "__main__":
    _run_selftests()
