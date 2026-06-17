"""
pull_savant.py — Pull batter season stats from Baseball Savant raw Statcast.

PHASE 7 (FanGraphs replication):
- Adds xBA, xSLG, xwOBA, sweet_spot_pct, ev_max columns
- Computes 4 rolling windows: season, l30, l14, l7
- Season window keeps vs-hand splits AND by_pitch_type breakdown
- Rolling windows (L30/L14/L7) write a single 'A' (all) row each, with their
  own by_pitch_type breakdown so the UI can switch windows on the pitch table.
  No vs-hand splits on rolling windows — sample too small to be reliable.

Strategy: one big Statcast pull → groupby batter for each window.

Runs twice daily via GitHub Actions.
"""

import os
import sys
import logging
from datetime import datetime, timezone, date, timedelta

import numpy as np
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

# Reference high-pull power hitters — spray-angle sanity check vs their public
# Baseball Savant pull%. Logged each run as permanent diagnostic output by
# _log_pull_validation (does not affect any written data).
PULL_VALIDATION_MLBAM = {
    663728: "Cal Raleigh",
    679529: "Spencer Torkelson",
    670623: "Isaac Paredes",
}

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


# PostgREST default response cap is 1,000 rows per .execute(). Players topped
# 1,000 in mid-May 2026 — the two callers below (get_known_batters and
# get_known_pitchers) both filter is_pitcher in Python AFTER the query, so
# the unbounded select silently truncated to the first 1,000 rows. The
# downstream upsert was bailed out by the players_mlbam_id_key UNIQUE
# constraint (no duplicate rows ever created), but the truncated lookup
# meant Statcast rows for batters/pitchers outside the 1,000-row window
# triggered re-upserts of existing rows on every cron tick — wasted writes,
# but harmless data-wise. Helper paginates via .range() in a loop; each
# caller filters in Python as before.
# DO NOT collapse the helper back into a single .select(...).execute() unless
# the PostgREST default has been raised or the row count has shrunk below 1,000.
_PLAYERS_PAGE = 1000


def _all_players() -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        res = sb.table("players").select("id, mlbam_id, is_pitcher") \
            .range(offset, offset + _PLAYERS_PAGE - 1).execute()
        rows = res.data or []
        out.extend(rows)
        if len(rows) < _PLAYERS_PAGE:
            break
        offset += _PLAYERS_PAGE
    return out


def get_known_batters() -> dict[int, int]:
    return {p["mlbam_id"]: p["id"] for p in _all_players() if not p["is_pitcher"]}


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

    # ── Pulled airball rates (v2 HR predictor) ──
    # Spray-angle pull-THIRD (Bill Petti convention), distinct from pull_pct
    # above — that's a half-field split, kept as a separate, coarser signal.
    # "Pulled" = adjusted spray angle > 15° toward the pull side (sign-flipped
    # for RHB so pull is always positive). Airball = fly_ball + line_drive
    # (popups excluded — ≈0% HR). Both rates are over BBE, matching pull_pct /
    # barrel_pct so the scale is comparable across metrics.
    pulled_fb_rate = None
    pulled_airball_rate = None
    if not bip.empty and {"hc_x", "hc_y", "stand", "bb_type"}.issubset(bip.columns):
        bb = bip.dropna(subset=["hc_x", "hc_y", "stand", "bb_type"])
        if len(bb):
            # Home plate at (125.42, 198.27); angle 0 = straight up the middle,
            # + toward RF, − toward LF.
            angle = np.degrees(np.arctan2(bb["hc_x"] - 125.42, 198.27 - bb["hc_y"]))
            # RHB pull to LF (negative) → flip so pull is positive for both hands.
            adj = np.where(bb["stand"] == "R", -angle, angle)
            is_pulled = adj > 15            # pull-third threshold
            fly = bb["bb_type"] == "fly_ball"
            air = bb["bb_type"].isin(("fly_ball", "line_drive"))
            denom = len(bb)
            pulled_fb_rate      = round(float((is_pulled & fly).sum()) / denom * 100, 2)
            pulled_airball_rate = round(float((is_pulled & air).sum()) / denom * 100, 2)

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
        # v2 Day 1: spray-angle pull-third airball rates (already rounded above)
        "pulled_fb_rate":      pulled_fb_rate,
        "pulled_airball_rate": pulled_airball_rate,
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


def _log_pull_validation(rows: list[dict], known: dict[int, int]) -> None:
    """
    Diagnostic (permanent): log spray-angle pulled-airball rates for the
    reference high-pull hitters (sanity vs their public Savant pull%) plus the
    top-5 leaguewide. Reads the in-memory season/'A' rows only — no DB
    dependency, no effect on written data.
    """
    id_to_mlbam = {pid: mlbam for mlbam, pid in known.items()}
    season = [r for r in rows
              if r.get("window_type") == "season" and r.get("vs_hand") == "A"]

    matched = 0
    for r in season:
        mlbam = id_to_mlbam.get(r["batter_id"])
        if mlbam in PULL_VALIDATION_MLBAM:
            log.info("PULL-VAL %-18s mlbam=%s  pulled_airball_rate=%s  "
                     "pulled_fb_rate=%s  pull_pct[half]=%s  bbe=%s pa=%s",
                     PULL_VALIDATION_MLBAM[mlbam], mlbam,
                     r.get("pulled_airball_rate"), r.get("pulled_fb_rate"),
                     r.get("pull_pct"), r.get("bbe"), r.get("pa"))
            matched += 1
    if not matched:
        log.warning("PULL-VAL: no reference hitters matched this run's data")

    ranked = sorted(
        [r for r in season
         if r.get("pulled_airball_rate") is not None and (r.get("pa") or 0) >= 100],
        key=lambda r: r["pulled_airball_rate"], reverse=True,
    )[:5]
    for r in ranked:
        log.info("PULL-VAL top  mlbam=%s  pulled_airball_rate=%.2f  "
                 "pulled_fb_rate=%.2f  pull_pct[half]=%.2f  pa=%d",
                 id_to_mlbam.get(r["batter_id"]),
                 r["pulled_airball_rate"], r["pulled_fb_rate"],
                 r.get("pull_pct") or 0.0, r["pa"])


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


# ────────────────────────────────────────────────────────────────
# PITCHER STATCAST AGGREGATION
# ────────────────────────────────────────────────────────────────

def get_known_pitchers() -> dict[int, int]:
    """{mlbam_id: players.id} for everyone we've registered as a pitcher."""
    # See the _all_players helper above for the PostgREST 1000-row cap context.
    return {p["mlbam_id"]: p["id"] for p in _all_players() if p["is_pitcher"]}


def aggregate_pitcher(group: pd.DataFrame,
                      pitcher_id: int,
                      window_type: str,
                      window_start: date | None,
                      window_end: date,
                      throws: str | None = None) -> dict | None:
    """
    Aggregate pitches a pitcher threw into pitcher-allowed Statcast stats.

    Note: we ONLY write the Statcast columns + identifiers + window metadata.
    Traditional stats (era, fip, whip, k_per_9, etc.) are populated by
    pull_pitcher_stats.py from MLB Stats API season totals — leaving those
    untouched preserves their values.

    The upsert key is (pitcher_id, season, window_type), and the upsert only
    overwrites the columns we explicitly include in the payload.
    """
    pa_rows = group[group["events"].notna()]
    pa = len(pa_rows)

    if window_type != "season" and pa < MIN_PA_FOR_WINDOW:
        return None

    bip = group[group["type"] == "X"] if "type" in group.columns else group.iloc[0:0]
    bbe = int(len(bip))

    # ── Barrel% allowed ──
    barrel_pct = None
    if not bip.empty and "launch_speed_angle" in bip.columns and bbe > 0:
        barrels = (bip["launch_speed_angle"] == 6).sum()
        barrel_pct = barrels / bbe * 100

    # ── Fly-ball metrics allowed (v2 Day 4): FB% + HR/FB ──
    # FanGraphs convention: FB% = fly balls / all BBE; HR/FB = HR / fly balls.
    # From Statcast bb_type + events. All 4 windows (this aggregator runs them).
    fb_pct = hr_per_fb = None
    if not bip.empty and "bb_type" in bip.columns:
        bb = bip.dropna(subset=["bb_type"])
        if len(bb):
            fly = int((bb["bb_type"] == "fly_ball").sum())
            fb_pct = fly / len(bb) * 100
            hr = int((group["events"] == "home_run").sum()) if "events" in group.columns else 0
            hr_per_fb = (hr / fly * 100) if fly else None

    # ── EV / Hard-Hit / Sweet Spot allowed ──
    hh_pct = ev_avg = ev_max = sweet_spot_pct = None
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
                sweet_balls = la_clean.between(8, 32).sum()
                sweet_spot_pct = sweet_balls / len(la_clean) * 100

    # ── xStats allowed ──
    xba = xslg = xwoba = None
    if not bip.empty:
        if "estimated_ba_using_speedangle" in bip.columns:
            x = bip["estimated_ba_using_speedangle"].dropna()
            if len(x): xba = float(x.mean())
        if "estimated_slg_using_speedangle" in bip.columns:
            x = bip["estimated_slg_using_speedangle"].dropna()
            if len(x): xslg = float(x.mean())
        if "estimated_woba_using_speedangle" in bip.columns:
            x = bip["estimated_woba_using_speedangle"].dropna()
            if len(x): xwoba = float(x.mean())

    return {
        "pitcher_id":     pitcher_id,
        "season":         CURRENT_SEASON,
        "window_type":    window_type,
        "barrel_pct":     round(barrel_pct, 2)     if barrel_pct     is not None else None,
        "hard_hit_pct":   round(hh_pct, 2)         if hh_pct         is not None else None,
        "sweet_spot_pct": round(sweet_spot_pct, 2) if sweet_spot_pct is not None else None,
        "ev_avg":         round(ev_avg, 1)         if ev_avg         is not None else None,
        "ev_max":         round(ev_max, 1)         if ev_max         is not None else None,
        # v2 Day 4: fly-ball metrics allowed
        "fb_pct":         round(fb_pct, 2)         if fb_pct         is not None else None,
        "hr_per_fb":      round(hr_per_fb, 2)      if hr_per_fb      is not None else None,
        "xba":            round(xba, 3)            if xba            is not None else None,
        "xslg":           round(xslg, 3)           if xslg            is not None else None,
        "xwoba":          round(xwoba, 3)          if xwoba          is not None else None,
        "bbe":            bbe,
        "window_start":   window_start.isoformat() if window_start else None,
        "window_end":     window_end.isoformat(),
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }


def process_pitchers(df: pd.DataFrame, today_date: date) -> int:
    """
    Aggregate pitcher-allowed Statcast for all 4 windows.
    Returns number of rows written.
    """
    if "pitcher" not in df.columns:
        log.warning("Statcast data missing 'pitcher' column — skipping pitcher aggregation")
        return 0

    known = get_known_pitchers()
    pitcher_ids = df["pitcher"].dropna().unique()
    log.info("Found %d unique pitchers in Statcast", len(pitcher_ids))

    # Pre-compute windowed dataframes
    pitcher_windows = []
    for window_type, days in WINDOWS:
        sub, w_start = filter_window(df, days, today_date)
        by_pitcher = sub.groupby("pitcher", sort=False)
        pitcher_windows.append((window_type, w_start, by_pitcher))

    rows = []
    skipped = 0
    unknown = 0
    for pitcher_mlbam in pitcher_ids:
        try:
            mlbam_int = int(pitcher_mlbam)
        except (ValueError, TypeError):
            continue

        if mlbam_int not in known:
            # Pitcher isn't in our players table — they don't have a starting
            # role on our slate, so we skip rather than backfill randomly.
            unknown += 1
            continue
        pitcher_id = known[mlbam_int]

        for window_type, w_start, grouped in pitcher_windows:
            try:
                group = grouped.get_group(pitcher_mlbam)
            except KeyError:
                continue
            row = aggregate_pitcher(group, pitcher_id, window_type,
                                    w_start, today_date)
            if row:
                rows.append(row)
            else:
                skipped += 1

    log.info("Prepared %d pitcher rows (%d unknown pitcher mlbam ids skipped, %d small-sample windows skipped)",
             len(rows), unknown, skipped)

    if not rows:
        return 0

    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        sb.table("pitcher_stats").upsert(
            chunk,
            on_conflict="pitcher_id,season,window_type",
        ).execute()
        written += len(chunk)
        if i % 500 == 0:
            log.info("  Wrote %d / %d pitcher rows", written, len(rows))

    log.info("✓ Wrote %d pitcher rows", written)
    return written


def main():
    log.info("🧅 Cebolla Lab — Savant sync starting (Phase 7: batters + pitchers, 4 windows + xStats)")

    df = fetch_all_statcast()
    if df is None:
        log.error("No Statcast data; aborting")
        sys.exit(1)

    today_date = date.today()

    # One-time/diagnostic: confirm bb_type strings (pulled-airball rates depend
    # on them) and flag if the column is missing. Kept as permanent output.
    if "bb_type" in df.columns:
        bbe_all = df[df["type"] == "X"] if "type" in df.columns else df
        vc = bbe_all["bb_type"].value_counts(dropna=False).to_dict()
        log.info("bb_type value counts (batted balls): %s", vc)
    else:
        log.warning("Statcast df has no 'bb_type' column — pulled_*_rate will be NULL")

    # ──────────────── BATTER PROCESSING ────────────────
    known = get_known_batters()

    if "batter" not in df.columns:
        log.error("Statcast data missing 'batter' column")
        sys.exit(1)

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
                # Rolling windows: single A row.
                # Pitch-type breakdown is now written for these too so the
                # PlayerView pitch table can switch between Season/L30/L14/L7.
                by_pitch_w = aggregate_by_pitch_type(group)
                row = aggregate_batter(group, player_id, "A", window_type,
                                        w_start, today_date, by_pitch=by_pitch_w)
                if row:
                    rows.append(row)
                else:
                    skipped_windows += 1

        processed += 1
        if processed % 100 == 0:
            log.info("  Processed %d / %d batters…", processed, len(batter_ids))

    log.info("Prepared %d rows (%d new batter records, %d window rows skipped for small samples)",
             len(rows), new_count, skipped_windows)

    _log_pull_validation(rows, known)

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

    log.info("✓ Wrote %d total batter rows", written)

    # ──────────────── PITCHER PROCESSING ────────────────
    log.info("─── Starting pitcher Statcast aggregation ───")
    pitcher_written = process_pitchers(df, today_date)
    log.info("✓ Wrote %d total pitcher rows", pitcher_written)

    log.info("🧅 Savant sync complete (%d batter + %d pitcher rows)",
             written, pitcher_written)


if __name__ == "__main__":
    main()
