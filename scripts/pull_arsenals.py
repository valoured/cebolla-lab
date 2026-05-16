"""
pull_arsenals.py — Pull each probable pitcher's arsenal split by batter stance.

For every probable pitcher in upcoming games, we hit Baseball Savant's
pitcher-level Statcast data and aggregate:
  - usage% by pitch type (vs LHB / vs RHB)
  - velo avg
  - BIP outcomes (HR%, Barrel%, HardHit%, EV, LA)

This is the heart of the KrashBoard "Krash rating" replica. The composite
grade gets computed in Phase 4; for now we store the raw inputs.

Runs once daily (Statcast updates overnight).
"""

import os
import sys
import logging
from datetime import datetime, timezone, date, timedelta

import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

import warnings
warnings.filterwarnings("ignore")

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

CURRENT_SEASON = 2026
SEASON_START = f"{CURRENT_SEASON}-03-15"  # ~spring training end / opening day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def get_probable_pitchers() -> list[dict]:
    """Pitchers slated to start in today's or upcoming games."""
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=3)).isoformat()

    res = sb.table("games") \
        .select("away_pitcher_id, home_pitcher_id") \
        .gte("game_date", today) \
        .lte("game_date", cutoff) \
        .execute()

    pitcher_ids = set()
    for g in res.data:
        if g["away_pitcher_id"]:
            pitcher_ids.add(g["away_pitcher_id"])
        if g["home_pitcher_id"]:
            pitcher_ids.add(g["home_pitcher_id"])

    if not pitcher_ids:
        return []

    p = sb.table("players") \
        .select("id, mlbam_id, name") \
        .in_("id", list(pitcher_ids)) \
        .execute()
    return p.data


def fetch_arsenal_for_pitcher(mlbam_id: int) -> pd.DataFrame | None:
    """Pull raw pitch-level Statcast data for one pitcher this season."""
    from pybaseball import statcast_pitcher
    today = date.today().isoformat()
    try:
        df = statcast_pitcher(SEASON_START, today, mlbam_id)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        log.warning("  Failed to fetch pitcher %d: %s", mlbam_id, e)
        return None


def aggregate_arsenal(df: pd.DataFrame, pitcher_id: int) -> list[dict]:
    """
    Group raw pitches into (pitch_type × batter_stance) rows.
    Returns a list of dicts ready for the pitcher_arsenals table.
    """
    if df is None or df.empty:
        return []

    # Statcast pitch_type codes: FF=4SM, SI=SI, FC=CT, CH=CH, SL=SL, CU=CU, KC=KC, FS=FS, ST=SW, etc.
    pitch_type_map = {
        "FF": "4SM", "FA": "4SM",  # 4-seam fastball
        "SI": "SI", "FT": "SI",     # sinker / 2-seam
        "FC": "CT",                  # cutter
        "CH": "CH",                  # changeup
        "SL": "SL",                  # slider
        "CU": "CU", "KC": "KC",     # curve / knuckle curve
        "FS": "FS",                  # splitter
        "ST": "SW",                  # sweeper
        "SV": "SV",                  # slurve
        "KN": "KN",                  # knuckleball
    }

    df = df.copy()
    df["pitch_label"] = df["pitch_type"].map(pitch_type_map).fillna(df["pitch_type"])

    # Total pitches by stance for usage% denominator
    total_by_stance = df.groupby("stand").size().to_dict()

    rows = []
    for (stance, pitch), group in df.groupby(["stand", "pitch_label"]):
        if pd.isna(stance) or stance not in ("L", "R"):
            continue

        pitches = len(group)
        denom = total_by_stance.get(stance, 0)
        usage_pct = (pitches / denom * 100) if denom else None

        # Only consider BIP rows for outcome stats
        bip = group[group["type"] == "X"]  # X = ball in play

        velo = group["release_speed"].mean() if "release_speed" in group else None

        # Plate appearances (rough): unique at_bat events
        pa = group[group["events"].notna()].shape[0]
        hr_count = (group["events"] == "home_run").sum()
        hr_pct = (hr_count / pa * 100) if pa > 0 else None

        # Statcast barrel: classified by launch_speed_angle
        # 6 = "barreled" in Savant's classification
        if not bip.empty and "launch_speed_angle" in bip.columns:
            barrels = (bip["launch_speed_angle"] == 6).sum()
            barrel_pct = barrels / len(bip) * 100
        else:
            barrel_pct = None

        # Hard hit = EV >= 95 mph
        if not bip.empty and "launch_speed" in bip.columns:
            ev_clean = bip["launch_speed"].dropna()
            hh = (ev_clean >= 95).sum()
            hh_pct = hh / len(ev_clean) * 100 if len(ev_clean) else None
            ev_avg = ev_clean.mean() if len(ev_clean) else None
            la_avg = (
                bip["launch_angle"].dropna().mean()
                if "launch_angle" in bip.columns else None
            )
        else:
            hh_pct = None
            ev_avg = None
            la_avg = None

        # Whiff% = swings_missed / swings
        # Swing descriptions: 'swinging_strike', 'swinging_strike_blocked', 'foul_tip', etc.
        swing_descs = {
            "swinging_strike", "swinging_strike_blocked",
            "foul", "foul_tip", "hit_into_play",
        }
        miss_descs = {"swinging_strike", "swinging_strike_blocked"}
        if "description" in group.columns:
            swings = group[group["description"].isin(swing_descs)]
            misses = group[group["description"].isin(miss_descs)]
            whiff_pct = (
                len(misses) / len(swings) * 100 if len(swings) > 0 else None
            )
        else:
            whiff_pct = None

        rows.append({
            "pitcher_id": pitcher_id,
            "season": CURRENT_SEASON,
            "window_type": "season",
            "vs_stance": stance,
            "pitch_type": pitch,
            "usage_pct": round(usage_pct, 2) if usage_pct is not None else None,
            "velo_avg": round(velo, 1) if pd.notna(velo) else None,
            "pitches": pitches,
            "pa": int(pa),
            "hr": int(hr_count),
            "hr_pct": round(hr_pct, 2) if hr_pct is not None else None,
            "barrel_pct": round(barrel_pct, 2) if barrel_pct is not None else None,
            "hard_hit_pct": round(hh_pct, 2) if hh_pct is not None else None,
            "ev_avg": round(ev_avg, 1) if ev_avg is not None else None,
            "la_avg": round(la_avg, 1) if la_avg is not None else None,
            "whiff_pct": round(whiff_pct, 2) if whiff_pct is not None else None,
            # krash_rating computed in Phase 4
            "krash_rating": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    return rows


def main():
    log.info("🧅 Cebolla Lab — Arsenal sync starting")

    pitchers = get_probable_pitchers()
    log.info("Found %d probable pitchers in upcoming 3 days", len(pitchers))

    if not pitchers:
        log.info("Nothing to do.")
        return

    total_rows = 0
    failed = 0

    for i, p in enumerate(pitchers, 1):
        log.info("[%d/%d] %s (mlbam=%d)", i, len(pitchers), p["name"], p["mlbam_id"])
        df = fetch_arsenal_for_pitcher(p["mlbam_id"])
        if df is None:
            failed += 1
            continue

        rows = aggregate_arsenal(df, p["id"])
        if not rows:
            log.info("   (no arsenal data)")
            continue

        # Upsert
        sb.table("pitcher_arsenals").upsert(
            rows,
            on_conflict="pitcher_id,season,window_type,vs_stance,pitch_type",
        ).execute()
        log.info("   ✓ %d arsenal rows", len(rows))
        total_rows += len(rows)

    log.info("🧅 Arsenal sync complete: %d rows written; %d failed",
             total_rows, failed)


if __name__ == "__main__":
    main()
