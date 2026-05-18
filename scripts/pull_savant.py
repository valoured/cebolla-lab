"""
pull_savant.py — Pull batter season stats from Baseball Savant raw Statcast.

PHASE 7 (FanGraphs replication):
- Adds xBA, xSLG, xwOBA, sweet_spot_pct, ev_max columns
- Computes 4 rolling windows: season, l30, l14, l7
- Season window keeps vs-hand splits AND by_pitch_type breakdown
- Rolling windows (L30/L14/L7) write a single 'A' (all) row each — no splits

Strategy: one big Statcast pull → groupby batter for each window.

Runs twice daily via GitHub Actions.
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
SEASON_START = f"{CURRENT_SEASON}-03-15"

# Rolling windows in days. None = full season.
WINDOWS = [
    ("season", None),
    ("l30", 30),
    ("l14", 14),
    ("l7",  7),
]

# Minimum PAs we'll bother writing for a non-season window. Below this it's noise.
MIN_PA_FOR_WINDOW = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

PITCH_TYPE_MAP = {
    "FF": "4SM", "FA": "4SM",
    "SI": "SI",  "FT": "SI",
    "FC": "CT",
    "CH": "CH",
    "SL": "SL",
    "CU": "CU",  "KC": "KC",
    "FS": "FS",
    "ST": "SW",
    "SV": "SV",
    "KN": "KN",
}


def get_known_batters() -> dict[int, int]:
    res = sb.table("players").select("id, mlbam_id, is_pitcher").execute()
    return {p["mlbam_id"]: p["id"] for p in res.data if not p["is_pitcher"]}


def upsert_batter(mlbam_id: int, name: str) -> int:
    payload = {
        "mlbam_id": mlbam_id,
        "name": name,
        "is_pitcher": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("players").upsert(payload, on_conflict="mlbam_id").execute()
    return res.data[0]["id"]


def fetch_all_statcast() -> pd.DataFrame | None:
    from pybaseball import statcast
    today = date.today().isoformat()
    log.info("Fetching Statcast %s → %s (this can take 1-3 minutes)…",
             SEASON_START, today)
    try:
        df = statcast(start_dt=SEASON_START, end_dt=today)
        if df is None or df.empty:
            log.warning("Empty Statcast response")
            return None
        log.info("  Got %d pitches", len(df))
        # Coerce game_date once for window filtering
        if "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
        return df
    except Exception as e:
        log.exception("Statcast pull failed: %s", e)
        return None


def aggregate_batter(group: pd.DataFrame,
                     batter_id: int,
                     vs_hand: str,
                     window_type: str,
                     window_start: date | None,
                     window_end: date,
                     by_pitch: dict | None = None) -> dict | None:
    """
    Aggregate one batter's pitches into a stats row.
    Returns None if the sample is too small to be meaningful for a non-season window.
    """
    pa_rows = group[group["events"].notna()]
    pa = len(pa_rows)

    if window_type != "season" and pa < MIN_PA_FOR_WINDOW:
        return None

    non_ab_events = {
        "walk", "hit_by_pitch", "sac_fly", "sac_bunt",
        "intent_walk", "catcher_interf",
    }
    ab = pa_rows[~pa_rows["events"].isin(non_ab_events)].shape[0]

    hit_events = {"single", "double", "triple", "home_run"}
    hits = pa_rows[pa_rows["events"].isin(hit_events)].shape[0]
    hr = int((pa_rows["events"] == "home_run").sum())
    singles = int((pa_rows["events"] == "single").sum())
    doubles = int((pa_rows["events"] == "double").sum())
    triples = int((pa_rows["events"] == "triple").sum())

    bb = int(pa_rows["events"].isin(["walk", "intent_walk"]).sum())
    hbp = int((pa_rows["events"] == "hit_by_pitch").sum())
    sf = int((pa_rows["events"] == "sac_fly").sum())

    avg = hits / ab if ab > 0 else None
    obp_denom = ab + bb + hbp + sf
    obp = (hits + bb + hbp) / obp_denom if obp_denom > 0 else None
    tb = singles + 2 * doubles + 3 * triples + 4 * hr
    slg = tb / ab if ab > 0 else None
    iso = (slg - avg) if (slg is not None and avg is not None) else None

    bip = group[group["type"] == "X"] if "type" in group.columns else group.iloc[0:0]
    bbe = int(len(bip))

    # ── Barrel% ──
    barrel_pct = None
    if not bip.empty and "launch_speed_angle" in bip.columns and bbe > 0:
        barrels = (bip["launch_speed_angle"] == 6).sum()
        barrel_pct = barrels / bbe * 100

    # ── EV / LA / Hard-Hit / Sweet Spot ──
    hh_pct = ev_avg = ev_max = la_avg = sweet_spot_pct = None
    if not bip.empty and "launch_speed" in bip.columns:
        ev_clean = bip["launch_speed"].dropna()
        if len(ev_clean):
            hh = (ev_clean >= 95).sum()
            hh_pct = hh / len(ev_clean) * 100
            ev_avg = float(ev_clean.mean())
            ev_max = float(ev_clean.max())
        if "launch_angle" in bip.columns:
            la_clean = bip["launch_angle"].dropna()
            if len(la_clean):
                la_avg = float(la_clean.mean())
                # Sweet spot = launch_angle 8-32 degrees, per MLB Statcast definition
                sweet_balls = la_clean.between(8, 32).sum()
                sweet_spot_pct = sweet_balls / len(la_clean) * 100

    # ── Pull% ──
    pull_pct = None
    if not bip.empty and "hc_x" in bip.columns and "stand" in bip.columns:
        bip_clean = bip.dropna(subset=["hc_x", "stand"])
        if len(bip_clean):
            pulled = bip_clean[
                ((bip_clean["stand"] == "L") & (bip_clean["hc_x"] > 125)) |
                ((bip_clean["stand"] == "R") & (bip_clean["hc_x"] < 125))
            ]
            pull_pct = len(pulled) / len(bip_clean) * 100

    # ── xStats (mean of Statcast estimated metrics across the window) ──
    # These columns exist for every batted ball with sufficient data.
    xba = xslg = xwoba = None
    if not bip.empty:
        if "estimated_ba_using_speedangle" in bip.columns:
            xba_clean = bip["estimated_ba_using_speedangle"].dropna()
            if len(xba_clean):
                xba = float(xba_clean.mean())
        if "estimated_slg_using_speedangle" in bip.columns:
            xslg_clean = bip["estimated_slg_using_speedangle"].dropna()
            if len(xslg_clean):
                xslg = float(xslg_clean.mean())
        if "estimated_woba_using_speedangle" in bip.columns:
            xwoba_clean = bip["estimated_woba_using_speedangle"].dropna()
            if len(xwoba_clean):
                xwoba = float(xwoba_clean.mean())

    row = {
        "batter_id": batter_id,
        "season": CURRENT_SEASON,
        "window_type": window_type,
        "vs_hand": vs_hand,
        "pa": int(pa),
        "ab": int(ab),
        "hits": int(hits),
        "hr": int(hr),
        "avg": round(avg, 3) if avg is not None else None,
        "obp": round(obp, 3) if obp is not None else None,
        "slg": round(slg, 3) if slg is not None else None,
        "iso": round(iso, 3) if iso is not None else None,
        "hr_per_pa": round(hr / pa, 4) if pa > 0 else None,
        "hit_per_pa": round(hits / pa, 4) if pa > 0 else None,
        "barrel_pct":     round(barrel_pct, 2)     if barrel_pct     is not None else None,
        "hard_hit_pct":   round(hh_pct, 2)         if hh_pct         is not None else None,
        "sweet_spot_pct": round(sweet_spot_pct, 2) if sweet_spot_pct is not None else None,
        "ev_avg":         round(ev_avg, 1)         if ev_avg         is not None else None,
        "ev_max":         round(ev_max, 1)         if ev_max         is not None else None,
        "la_avg":         round(la_avg, 1)         if la_avg         is not None else None,
        "pull_pct":       round(pull_pct, 2)       if pull_pct       is not None else None,
        "xba":            round(xba, 3)            if xba            is not None else None,
        "xslg":           round(xslg, 3)           if xslg           is not None else None,
        "xwoba":          round(xwoba, 3)          if xwoba          is not None else None,
        "bbe":            bbe,
        "by_pitch_type":  by_pitch,
        "window_start":   window_start.isoformat() if window_start else None,
        "window_end":     window_end.isoformat(),
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }
    return row


def aggregate_by_pitch_type(group: pd.DataFrame) -> dict:
    """Per-pitch-type breakdown for the season window. Unchanged from before."""
    if "pitch_type" not in group.columns or group.empty:
        return {}

    g = group.copy()
    g["pitch_label"] = g["pitch_type"].map(PITCH_TYPE_MAP).fillna(g["pitch_type"])

    out = {}
    for label, sub in g.groupby("pitch_label"):
        if not label or label == "nan":
            continue

        pa_rows = sub[sub["events"].notna()]
        pa = int(len(pa_rows))
        if pa < 5:
            continue

        hr = int((pa_rows["events"] == "home_run").sum())
        bip = sub[sub["type"] == "X"] if "type" in sub.columns else sub.iloc[0:0]

        ev_avg = None
        if not bip.empty and "launch_speed" in bip.columns:
            ev_clean = bip["launch_speed"].dropna()
            if len(ev_clean):
                ev_avg = round(float(ev_clean.mean()), 1)

        brl_pct = None
        if not bip.empty and "launch_speed_angle" in bip.columns and len(bip):
            barrels = (bip["launch_speed_angle"] == 6).sum()
            brl_pct = round(float(barrels / len(bip) * 100), 2)

        out[label] = {
            "pa": pa,
            "hr": hr,
            "hr_pct": round(hr / pa * 100, 2),
            "ev_avg": ev_avg,
            "brl_pct": brl_pct,
        }

    return out


def lookup_player_name(mlbam_id: int) -> str:
    try:
        from pybaseball import playerid_reverse_lookup
        df = playerid_reverse_lookup([mlbam_id], key_type="mlbam")
        if not df.empty:
            first = df.iloc[0].get("name_first", "")
            last = df.iloc[0].get("name_last", "")
            name = f"{first} {last}".strip().title()
            if name:
                return name
    except Exception:
        pass
    try:
        import requests
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{mlbam_id}", timeout=5
        )
        if r.ok:
            data = r.json()
            if data.get("people"):
                return data["people"][0]["fullName"]
    except Exception:
        pass
    return f"Player {mlbam_id}"


def filter_window(df: pd.DataFrame, days: int | None, today: date) -> tuple[pd.DataFrame, date | None]:
    """Return (filtered_df, window_start_date)."""
    if days is None or "game_date" not in df.columns:
        return df, None
    cutoff = today - timedelta(days=days)
    cutoff_ts = pd.Timestamp(cutoff)
    return df[df["game_date"] >= cutoff_ts], cutoff


def main():
    log.info("🧅 Cebolla Lab — Savant batter sync starting (Phase 7: 4 windows + xStats)")

    df = fetch_all_statcast()
    if df is None:
        log.error("No Statcast data; aborting")
        sys.exit(1)

    known = get_known_batters()

    if "batter" not in df.columns:
        log.error("Statcast data missing 'batter' column")
        sys.exit(1)

    today_date = date.today()
    batter_ids = df["batter"].dropna().unique()
    log.info("Found %d unique batters in season Statcast", len(batter_ids))

    rows = []
    new_count = 0
    processed = 0
    skipped_windows = 0

    # Pre-group season data once
    df_season = df  # already covers full season
    by_batter_season = df_season.groupby("batter", sort=False)
    by_batter_hand_season = df_season.groupby(["batter", "p_throws"], sort=False) \
        if "p_throws" in df_season.columns else None

    # Pre-compute the windowed dataframes once for all batters
    window_data = []
    for window_type, days in WINDOWS:
        sub, w_start = filter_window(df, days, today_date)
        by_batter_window = sub.groupby("batter", sort=False)
        window_data.append((window_type, days, w_start, by_batter_window))

    for batter_mlbam in batter_ids:
        try:
            batter_mlbam_int = int(batter_mlbam)
        except (ValueError, TypeError):
            continue

        if batter_mlbam_int in known:
            player_id = known[batter_mlbam_int]
        else:
            name = lookup_player_name(batter_mlbam_int)
            player_id = upsert_batter(batter_mlbam_int, name)
            known[batter_mlbam_int] = player_id
            new_count += 1

        for window_type, days, w_start, grouped in window_data:
            try:
                group = grouped.get_group(batter_mlbam)
            except KeyError:
                continue

            if window_type == "season":
                # Season: vs-hand splits + by_pitch_type
                by_pitch = aggregate_by_pitch_type(group)
                row = aggregate_batter(group, player_id, "A", "season",
                                        w_start, today_date, by_pitch=by_pitch)
                if row:
                    rows.append(row)

                if by_batter_hand_season is not None:
                    for hand in ("L", "R"):
                        try:
                            sub = by_batter_hand_season.get_group((batter_mlbam, hand))
                            if len(sub) >= 10:
                                by_pitch_h = aggregate_by_pitch_type(sub)
                                row_h = aggregate_batter(sub, player_id, hand, "season",
                                                          w_start, today_date,
                                                          by_pitch=by_pitch_h)
                                if row_h:
                                    rows.append(row_h)
                        except KeyError:
                            pass
            else:
                # Rolling windows: single A row, no by_pitch
                row = aggregate_batter(group, player_id, "A", window_type,
                                        w_start, today_date, by_pitch=None)
                if row:
                    rows.append(row)
                else:
                    skipped_windows += 1

        processed += 1
        if processed % 100 == 0:
            log.info("  Processed %d / %d batters…", processed, len(batter_ids))

    log.info("Prepared %d rows (%d new batter records, %d window rows skipped for small samples)",
             len(rows), new_count, skipped_windows)

    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        sb.table("batter_stats").upsert(
            chunk,
            on_conflict="batter_id,season,window_type,vs_hand",
        ).execute()
        written += len(chunk)
        if i % 500 == 0:
            log.info("  Wrote %d / %d", written, len(rows))

    log.info("✓ Wrote %d total rows", written)
    log.info("🧅 Savant batter sync complete")


if __name__ == "__main__":
    main()
