"""
pull_savant.py — Pull batter season stats from Baseball Savant raw Statcast.

Now also computes per-pitch-type batter performance for the
`batter_stats.by_pitch_type` JSONB column — needed for the Phase 4
arsenal-weighted projection model.

Strategy: one big Statcast pull → groupby batter for season totals →
groupby (batter, pitch_type) for per-pitch breakdown.

Runs twice daily via GitHub Actions.
"""

import os
import sys
import logging
from datetime import datetime, timezone, date

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# Pitch type normalization (Statcast 'pitch_type' code → our label)
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
        return df
    except Exception as e:
        log.exception("Statcast pull failed: %s", e)
        return None


def aggregate_batter(group: pd.DataFrame, batter_id: int, vs_hand: str,
                     by_pitch: dict | None = None) -> dict:
    """Aggregate one batter's pitches into season totals. Includes by_pitch_type JSON."""
    pa_rows = group[group["events"].notna()]
    pa = len(pa_rows)

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

    barrel_pct = None
    if not bip.empty and "launch_speed_angle" in bip.columns:
        barrels = (bip["launch_speed_angle"] == 6).sum()
        if len(bip):
            barrel_pct = barrels / len(bip) * 100

    hh_pct = ev_avg = la_avg = None
    if not bip.empty and "launch_speed" in bip.columns:
        ev_clean = bip["launch_speed"].dropna()
        if len(ev_clean):
            hh = (ev_clean >= 95).sum()
            hh_pct = hh / len(ev_clean) * 100
            ev_avg = float(ev_clean.mean())
        if "launch_angle" in bip.columns:
            la_clean = bip["launch_angle"].dropna()
            if len(la_clean):
                la_avg = float(la_clean.mean())

    pull_pct = None
    if not bip.empty and "hc_x" in bip.columns and "stand" in bip.columns:
        bip_clean = bip.dropna(subset=["hc_x", "stand"])
        if len(bip_clean):
            pulled = bip_clean[
                ((bip_clean["stand"] == "L") & (bip_clean["hc_x"] > 125)) |
                ((bip_clean["stand"] == "R") & (bip_clean["hc_x"] < 125))
            ]
            pull_pct = len(pulled) / len(bip_clean) * 100

    return {
        "batter_id": batter_id,
        "season": CURRENT_SEASON,
        "window_type": "season",
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
        "barrel_pct": round(barrel_pct, 2) if barrel_pct is not None else None,
        "hard_hit_pct": round(hh_pct, 2) if hh_pct is not None else None,
        "ev_avg": round(ev_avg, 1) if ev_avg is not None else None,
        "la_avg": round(la_avg, 1) if la_avg is not None else None,
        "pull_pct": round(pull_pct, 2) if pull_pct is not None else None,
        "by_pitch_type": by_pitch,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def aggregate_by_pitch_type(group: pd.DataFrame) -> dict:
    """
    Group one batter's pitches by pitch_type and compute key metrics.
    Returns {pitch_label: {pa, hr, hr_pct, ev_avg, brl_pct}}.
    Pitch labels match our pitcher_arsenals table.
    """
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
            continue  # too few PAs to be meaningful

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


def main():
    log.info("🧅 Cebolla Lab — Savant batter sync starting (with pitch-type breakdown)")

    df = fetch_all_statcast()
    if df is None:
        log.error("No Statcast data; aborting")
        sys.exit(1)

    known = get_known_batters()

    if "batter" not in df.columns:
        log.error("Statcast data missing 'batter' column")
        sys.exit(1)

    batter_ids = df["batter"].dropna().unique()
    log.info("Found %d unique batters in season Statcast", len(batter_ids))

    by_batter = df.groupby("batter", sort=False)
    by_batter_hand = df.groupby(["batter", "p_throws"], sort=False) \
        if "p_throws" in df.columns else None

    rows = []
    new_count = 0
    processed = 0

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

        group_all = by_batter.get_group(batter_mlbam)

        # by_pitch_type breakdown (used for arsenal-weighted projection)
        by_pitch = aggregate_by_pitch_type(group_all)

        # Season totals vs ALL pitchers
        rows.append(aggregate_batter(group_all, player_id, "A", by_pitch=by_pitch))

        # Splits vs L and R
        if by_batter_hand is not None:
            try:
                group_l = by_batter_hand.get_group((batter_mlbam, "L"))
                if len(group_l) >= 10:
                    by_pitch_l = aggregate_by_pitch_type(group_l)
                    rows.append(aggregate_batter(group_l, player_id, "L", by_pitch=by_pitch_l))
            except KeyError:
                pass
            try:
                group_r = by_batter_hand.get_group((batter_mlbam, "R"))
                if len(group_r) >= 10:
                    by_pitch_r = aggregate_by_pitch_type(group_r)
                    rows.append(aggregate_batter(group_r, player_id, "R", by_pitch=by_pitch_r))
            except KeyError:
                pass

        processed += 1
        if processed % 100 == 0:
            log.info("  Processed %d / %d batters…", processed, len(batter_ids))

    log.info("Prepared %d batter stat rows (%d new batter records)",
             len(rows), new_count)

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

    log.info("✓ Wrote %d total", written)
    log.info("🧅 Savant batter sync complete")


if __name__ == "__main__":
    main()
