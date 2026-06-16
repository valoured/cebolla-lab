"""
pull_park_factors.py — Savant Statcast park factors → park_factors table.

v2 rebuild Day 2. MANUAL / on-demand (NOT nightly cron — 3-yr rolling park
factors barely move intra-season). Scrapes the embedded `var data = [...];`
JSON from the Savant park-factors leaderboard for a given year (default 2026 =
the 2024-2026 3-yr rolling window), once per batSide (all / L / R), merges into
one row per park, maps Savant main_team_id (== teams.mlb_id) to our internal
teams.id, and upserts on (team_id, season, window_yrs, source).

Index scale: 100 = league average (Savant native). Stored as-is; the v2 model
(Day 5) converts to a multiplier (index / 100) when composing hr_score_v2.

Usage:
  python pull_park_factors.py --dry-run   # fetch + parse + map + print; NO DB writes
  python pull_park_factors.py             # prod upsert
"""

import os
import re
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pull_park_factors")

# ── Config (locked v2 Day 2 decisions) ───────────────────────────────────
YEAR = 2026                 # 2026 → 3-yr rolling window 2024-2026
SEASON = 2026
WINDOW_YRS = 3
SOURCE = "savant_3yr"

# Savant park-factors leaderboard. type=year + a year selects the 3-yr rolling
# window by default (key_num_years_rolling=3). batSide '' = all, 'L', 'R'.
SAVANT_URL = (
    "https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
    "?type=year&year={year}&batSide={bs}&stat=index_wOBA&condition=All&rolling="
)
_DATA_RE = re.compile(r"var data\s*=\s*(\[.*?\]);", re.DOTALL)


def _fetch(year: int, bs: str) -> list[dict]:
    """Fetch one batSide variant and extract the embedded `var data` array."""
    url = SAVANT_URL.format(year=year, bs=bs)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    m = _DATA_RE.search(html)
    if not m:
        raise RuntimeError(f"Savant park-factors: embedded data array not found (batSide={bs!r})")
    return json.loads(m.group(1))


def _to_num(v):
    """Savant returns index values as strings ('105'); coerce to float or None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_park_index(year: int) -> dict[int, dict]:
    """
    Fetch all/L/R and merge into {main_team_id: {hr, hr_lhb, hr_rhb, runs,
    venue_name, club}}. main_team_id is the MLB StatsAPI team id (== teams.mlb_id).
    """
    variants = {"": "all", "L": "lhb", "R": "rhb"}
    merged: dict[int, dict] = {}
    for bs, label in variants.items():
        rows = _fetch(year, bs)
        log.info("  batSide=%-3s → %d parks", bs or "all", len(rows))
        for r in rows:
            tid = int(r["main_team_id"])
            slot = merged.setdefault(tid, {
                "venue_name": r.get("venue_name"),
                "club": r.get("name_display_club"),
                "year_range": r.get("year_range"),
            })
            if label == "all":
                slot["hr"] = _to_num(r.get("index_hr"))
                slot["runs"] = _to_num(r.get("index_runs"))
            elif label == "lhb":
                slot["hr_lhb"] = _to_num(r.get("index_hr"))
            elif label == "rhb":
                slot["hr_rhb"] = _to_num(r.get("index_hr"))
    return merged


def load_team_map(sb) -> dict[int, dict]:
    """{mlb_id: {id, abbrev, name}} for mapping Savant main_team_id → teams.id."""
    res = sb.table("teams").select("id, mlb_id, abbrev, name").execute()
    return {r["mlb_id"]: r for r in (res.data or [])}


def build_rows(park_index: dict[int, dict], team_map: dict[int, dict]) -> tuple[list[dict], list[int]]:
    """Map to teams.id and assemble upsert rows. Returns (rows, unmapped_team_ids)."""
    rows, unmapped = [], []
    now = datetime.now(timezone.utc).isoformat()
    for mlb_id, pf in park_index.items():
        team = team_map.get(mlb_id)
        if not team:
            unmapped.append(mlb_id)
            continue
        rows.append({
            "team_id": team["id"],
            "season": SEASON,
            "window_yrs": WINDOW_YRS,
            "hr_factor": pf.get("hr"),
            "hr_factor_lhb": pf.get("hr_lhb"),
            "hr_factor_rhb": pf.get("hr_rhb"),
            "runs_factor": pf.get("runs"),
            "source": SOURCE,
            "updated_at": now,
            # carried for logging only — stripped before upsert:
            "_abbrev": team["abbrev"],
            "_venue": pf.get("venue_name"),
        })
    return rows, unmapped


def print_validation(park_index, team_map, rows, unmapped):
    """Read-only sanity dump: spread, top/bottom 5, named-park detail, quirks."""
    print(f"\n=== HARNESS: {len(park_index)} parks parsed (year={YEAR}, "
          f"window={WINDOW_YRS}yr, range={next(iter(park_index.values())).get('year_range')}) ===")
    print(f"mapped to teams.id: {len(rows)} / {len(park_index)}")
    if unmapped:
        print(f"UNMAPPED main_team_ids (no teams row): {unmapped}")

    # Which of the 30 teams got NO row (Savant-missing parks)
    have = {r["team_id"] for r in rows}
    missing = [(t["abbrev"], mlb) for mlb, t in sorted(team_map.items()) if t["id"] not in have]
    print(f"teams with NO park_factors row (fallback -> 100): {missing}")

    ranked = sorted([r for r in rows if r["hr_factor"] is not None],
                    key=lambda r: r["hr_factor"], reverse=True)
    print("\nTop 5 HR-friendly parks (overall index_hr):")
    for r in ranked[:5]:
        print(f"  {r['_abbrev']:<4} {r['_venue']:<28} HR={r['hr_factor']}")
    print("Bottom 5 HR-suppressing parks:")
    for r in ranked[-5:]:
        print(f"  {r['_abbrev']:<4} {r['_venue']:<28} HR={r['hr_factor']}")

    spread = [r["hr_factor"] for r in ranked]
    if spread:
        med = sorted(spread)[len(spread) // 2]
        print(f"\nleague spread: max={spread[0]}  median={med}  min={spread[-1]}")

    named = {"Coors Field", "Yankee Stadium", "Great American Ball Park",
             "Oracle Park", "Petco Park"}
    print("\nNamed-park sanity (all / LHB / RHB / runs):")
    print(f"{'park':<28}{'HR':>6}{'LHB':>6}{'RHB':>6}{'runs':>7}")
    for r in rows:
        if r["_venue"] in named:
            print(f"{r['_venue']:<28}{str(r['hr_factor']):>6}{str(r['hr_factor_lhb']):>6}"
                  f"{str(r['hr_factor_rhb']):>6}{str(r['runs_factor']):>7}")


def upsert_rows(sb, rows: list[dict]) -> int:
    """Strip log-only fields and upsert on (team_id, season, window_yrs, source)."""
    payload = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    sb.table("park_factors").upsert(
        payload, on_conflict="team_id,season,window_yrs,source"
    ).execute()
    return len(payload)


def main():
    # Windows consoles default to cp1252 and choke on non-ASCII in print();
    # force UTF-8 so the validation dump renders everywhere (no-op on Linux CI).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    dry_run = "--dry-run" in sys.argv
    log.info("🧅 Park factors %s — Savant year=%d (%dyr rolling, source=%s)",
             "DRY-RUN" if dry_run else "sync", YEAR, WINDOW_YRS, SOURCE)

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    log.info("Fetching Savant park factors (3 batSide variants)…")
    park_index = fetch_park_index(YEAR)
    team_map = load_team_map(sb)          # read-only
    rows, unmapped = build_rows(park_index, team_map)

    if dry_run:
        print_validation(park_index, team_map, rows, unmapped)
        log.info("DRY-RUN complete — NO DB writes. Re-run without --dry-run to upsert.")
        return

    written = upsert_rows(sb, rows)
    log.info("✓ Upserted %d park_factors rows (season=%d, window=%dyr, source=%s)",
             written, SEASON, WINDOW_YRS, SOURCE)


if __name__ == "__main__":
    main()
