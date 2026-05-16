"""
pull_savant.py — Pull batter and pitcher season stats via pybaseball.

This script pulls FanGraphs season-level stats (which include Statcast columns
like Barrel%, HardHit%, EV, LA, etc.) and writes them to the `batter_stats`
and `pitcher_arsenals` (base row) tables.

Only pulls for players currently in the `players` table (probable pitchers
and lineup-relevant batters — keeps us under 500MB).

Runs twice daily via GitHub Actions.
"""

import os
import sys
import logging
from datetime import datetime, timezone

import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

# pybaseball has noisy progress bars; suppress
import warnings
warnings.filterwarnings("ignore")

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

CURRENT_SEASON = 2026

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# BATTER STATS
# ────────────────────────────────────────────────────────────────

def fetch_batter_stats() -> pd.DataFrame:
    """Pull FanGraphs season batting stats. ~500 qualified batters."""
    from pybaseball import batting_stats
    log.info("Fetching FanGraphs batting stats for %d…", CURRENT_SEASON)
    # qual=1 means at least 1 PA; we want everyone, we'll filter later
    df = batting_stats(CURRENT_SEASON, CURRENT_SEASON, qual=1)
    log.info("  Got %d batters", len(df))
    return df


def transform_batter_row(row: pd.Series, player_id: int) -> dict:
    """Convert a FanGraphs row to our `batter_stats` schema."""
    return {
        "batter_id": player_id,
        "season": CURRENT_SEASON,
        "window_type": "season",
        "vs_hand": "A",  # season totals vs all pitchers
        "pa": int(row.get("PA", 0)),
        "ab": int(row.get("AB", 0)),
        "hits": int(row.get("H", 0)),
        "hr": int(row.get("HR", 0)),
        "avg": float(row.get("AVG", 0)) if pd.notna(row.get("AVG")) else None,
        "obp": float(row.get("OBP", 0)) if pd.notna(row.get("OBP")) else None,
        "slg": float(row.get("SLG", 0)) if pd.notna(row.get("SLG")) else None,
        "iso": float(row.get("ISO", 0)) if pd.notna(row.get("ISO")) else None,
        "hr_per_pa": (
            float(row["HR"]) / float(row["PA"])
            if row.get("PA", 0) > 0 else None
        ),
        "hit_per_pa": (
            float(row["H"]) / float(row["PA"])
            if row.get("PA", 0) > 0 else None
        ),
        "barrel_pct": _pct(row.get("Barrel%")),
        "hard_hit_pct": _pct(row.get("HardHit%")),
        "ev_avg": _num(row.get("EV")),
        "la_avg": _num(row.get("LA")),
        "pull_pct": _pct(row.get("Pull%")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ────────────────────────────────────────────────────────────────
# PITCHER STATS (base row, arsenal details come from pull_arsenals.py)
# ────────────────────────────────────────────────────────────────

def fetch_pitcher_stats() -> pd.DataFrame:
    """Pull FanGraphs season pitching stats."""
    from pybaseball import pitching_stats
    log.info("Fetching FanGraphs pitching stats for %d…", CURRENT_SEASON)
    df = pitching_stats(CURRENT_SEASON, CURRENT_SEASON, qual=1)
    log.info("  Got %d pitchers", len(df))
    return df


# ────────────────────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────────────────────

def _pct(v):
    """FanGraphs returns percentages as 0.123 or '12.3%'. Normalize to 0-100."""
    if v is None or pd.isna(v):
        return None
    if isinstance(v, str):
        v = v.strip().rstrip("%")
        try:
            v = float(v)
        except ValueError:
            return None
    v = float(v)
    # If less than 1, it's a decimal; multiply by 100
    return v * 100 if v <= 1 else v


def _num(v):
    if v is None or pd.isna(v):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def get_known_players() -> dict[int, int]:
    """Return {mlbam_id: players.id} for everyone we've already seen."""
    res = sb.table("players").select("id, mlbam_id").execute()
    return {p["mlbam_id"]: p["id"] for p in res.data}


def upsert_batter(mlbam_id: int, name: str, team_abbrev: str | None) -> int:
    """Create a batter record if we haven't seen them, return players.id."""
    payload = {
        "mlbam_id": mlbam_id,
        "name": name,
        "is_pitcher": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("players").upsert(payload, on_conflict="mlbam_id").execute()
    return res.data[0]["id"]


def lookup_mlbam_id(fg_name: str, fg_team: str) -> int | None:
    """
    FanGraphs uses 'IDfg' but we key by mlbam_id. Use playerid_lookup to bridge.
    Names from FanGraphs can be 'Aaron Judge' style.
    """
    from pybaseball import playerid_lookup
    parts = fg_name.split(" ", 1)
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[1]
    try:
        df = playerid_lookup(last, first, fuzzy=False)
        if df.empty:
            return None
        # Active players (still playing in current season)
        active = df[df["mlb_played_last"] >= CURRENT_SEASON - 1]
        if active.empty:
            return None
        return int(active.iloc[0]["key_mlbam"])
    except Exception as e:
        log.debug("  playerid_lookup failed for %s: %s", fg_name, e)
        return None


# ────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────

def sync_batters():
    """Pull batting stats and write to batter_stats for every batter."""
    df = fetch_batter_stats()
    known = get_known_players()

    rows = []
    new_batters = 0
    skipped = 0

    for _, fg_row in df.iterrows():
        # FanGraphs has IDfg, we need mlbam_id
        name = fg_row.get("Name")
        if not name or pd.isna(name):
            continue

        # Try to find this player in our DB by mlbam_id
        # FanGraphs sometimes includes mlbam id via the 'xMLBAMID' column
        mlbam_id = None
        for col in ("MLBAMID", "xMLBAMID", "mlbam_id"):
            if col in fg_row and pd.notna(fg_row[col]):
                try:
                    mlbam_id = int(fg_row[col])
                    break
                except (ValueError, TypeError):
                    pass

        if mlbam_id is None:
            # Fallback: lookup by name
            mlbam_id = lookup_mlbam_id(name, fg_row.get("Team", ""))

        if mlbam_id is None:
            skipped += 1
            continue

        if mlbam_id in known:
            player_id = known[mlbam_id]
        else:
            player_id = upsert_batter(mlbam_id, name, fg_row.get("Team"))
            known[mlbam_id] = player_id
            new_batters += 1

        try:
            rows.append(transform_batter_row(fg_row, player_id))
        except Exception as e:
            log.warning("  Skipped %s: %s", name, e)

    log.info("  %d batter stat rows ready; %d new batters; %d skipped (no mlbam)",
             len(rows), new_batters, skipped)

    # Batch upsert in chunks of 100
    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        sb.table("batter_stats").upsert(
            chunk,
            on_conflict="batter_id,season,window_type,vs_hand",
        ).execute()
        written += len(chunk)
        log.info("  Wrote %d / %d batter stat rows", written, len(rows))

    log.info("✓ Batter stats sync complete")


def sync_pitchers_metadata():
    """
    Just ensure every FanGraphs-tracked pitcher exists in our players table.
    Per-pitch arsenal data comes from pull_arsenals.py.
    """
    df = fetch_pitcher_stats()
    known = get_known_players()

    new_count = 0
    for _, row in df.iterrows():
        name = row.get("Name")
        if not name or pd.isna(name):
            continue

        mlbam_id = None
        for col in ("MLBAMID", "xMLBAMID", "mlbam_id"):
            if col in row and pd.notna(row[col]):
                try:
                    mlbam_id = int(row[col])
                    break
                except (ValueError, TypeError):
                    pass

        if mlbam_id is None or mlbam_id in known:
            continue

        payload = {
            "mlbam_id": mlbam_id,
            "name": name,
            "is_pitcher": True,
            "position": "P",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        res = sb.table("players").upsert(
            payload, on_conflict="mlbam_id"
        ).execute()
        known[mlbam_id] = res.data[0]["id"]
        new_count += 1

    log.info("✓ Registered %d new pitchers", new_count)


def main():
    log.info("🧅 Cebolla Lab — Savant/FanGraphs sync starting")
    try:
        sync_pitchers_metadata()
        sync_batters()
    except Exception as e:
        log.exception("Fatal error: %s", e)
        sys.exit(1)
    log.info("🧅 Savant sync complete")


if __name__ == "__main__":
    main()
