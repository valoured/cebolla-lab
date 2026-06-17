"""
hr_model.py — v2 HR probability model (hr_score_v2). Day 5.

compute_hr_probability(batter_id, game_id, pitcher_id) -> dict | None

Bounded multiplicative model anchored on the batter's shrunk observed HR/PA,
with z-scored batter & pitcher sub-factors and native park/weather multipliers,
times a global calibration intercept c so the slate median edge ≈ 0:

  per_pa  = c × shrunk_obs_hr_per_pa × batter_profile_factor × pitcher_factor
              × park_mult × weather_mult           (capped at PROJ_PER_PA_CAP)
  per_game = 1 - (1 - per_pa)^E[PA from lineup spot]
  edge     = per_game - no_vig (single-sided dynamic vig curve)

Calibration (c=0.88) validated 2026-06-17 on the 270-batter confirmed slate:
median edge +0.02%, strong_back 6%, median per_game 10.9%, 0 picks >0.30.

ALL WEIGHTS ARE UNVALIDATED PRIORS — REFINE AFTER 200 SHADOW BETS.

V2.1 roadmap: mixed-window pitcher features (L30 hr_per_9 + SEASON hr_per_fb +
SEASON fb_pct) — stabler traits, needs a fresh calibration sweep. See
compute_feature_baselines.py header. Shipping all-L30 (what we validated).
"""

import os
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from compute_projections import (
    shrink_batter_hr_per_pa, one_minus_pow_per_pa, edge_bucket,
    PA_BY_LINEUP_SPOT, HR_LONGSHOT_THRESHOLD,
)
from v2 import feature_z_scores as FZ
from v2 import park_factor_lookup as PFL
from v2 import weather_lookup as WL
from v2 import pitcher_lookup as PL
from v2 import devig as DV

# ─── Locked constants (UNVALIDATED PRIORS — REFINE AFTER 200 SHADOW BETS) ───
MODEL_VERSION            = "hr_v2.0"
MODEL_INTERCEPT_C        = 0.88     # calibrated so slate median edge ≈ 0; refine annually from shadow data
LEAGUE_HR_PER_PA         = 0.029    # baseline anchor (matches compute_projections)
PROJ_PER_PA_CAP          = 0.08
PER_GAME_HIGH_THRESHOLD  = 0.20     # warning flag (per_game_high)
PER_GAME_REJECT_THRESHOLD = 0.30    # hard reject → picks_v2_rejected

BATTER_W_PULLED_AIRBALL  = 0.20
BATTER_FACTOR_CLAMP      = (0.92, 1.12)

PITCHER_W_HR_PER_9       = 0.15
PITCHER_W_HR_PER_FB      = 0.12
PITCHER_W_FB_PCT         = 0.10
PITCHER_FACTOR_CLAMP     = (0.85, 1.20)
PITCHER_Z_CLAMP          = (-2.0, 2.0)
PITCHER_MIN_IP           = 20       # below → sample-weight dampening
PITCHER_MIN_BBE          = 30

PARK_MULT_CLAMP          = (0.85, 1.20)
WEATHER_MULT_CLAMP       = (0.70, 1.30)

_FINAL_STATUSES = ["Final", "Game Over", "Completed Early"]


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _fetch_ctx(batter_id, game_id, pitcher_id, sb):
    """Self-fetch the per-pick context (used when pick_v2 doesn't pass ctx)."""
    g = sb.table("games").select("home_team_id, away_team_id").eq("id", game_id).single().execute().data
    lu = sb.table("lineups").select("batting_order, source, is_confirmed, team_id") \
        .eq("game_id", game_id).eq("player_id", batter_id).limit(1).execute().data
    lu = (lu or [None])[0]
    # lineup_source is a derived provenance label (not a DB column); confirmed
    # MLB postings → 'confirmed', else pass the source through.
    lsrc = None
    if lu:
        lsrc = "confirmed" if (lu.get("is_confirmed") and lu.get("source") == "mlb_api") else lu.get("source")
    b = sb.table("batter_stats").select("pa, hr_per_pa, pulled_airball_rate") \
        .eq("batter_id", batter_id).eq("season", 2026).eq("window_type", "season") \
        .eq("vs_hand", "A").limit(1).execute().data
    pl = {p["id"]: p for p in sb.table("players").select("id, bats, throws")
          .in_("id", [batter_id, pitcher_id]).execute().data or []}
    prow = sb.table("pitcher_stats").select("innings_pitched, bbe") \
        .eq("pitcher_id", pitcher_id).eq("season", 2026).eq("window_type", "l30").limit(1).execute().data
    prow = (prow or [{}])[0]
    od = sb.table("odds_snapshots").select("american_odds").eq("game_id", game_id) \
        .eq("player_id", batter_id).eq("market", "hr_anytime_yes").eq("is_current", True) \
        .order("snapshot_time", desc=True).limit(1).execute().data
    return {
        "bstats": (b or [None])[0],
        "batting_order": (lu or {}).get("batting_order") if lu else None,
        "lineup_source": lsrc,
        "bats": (pl.get(batter_id) or {}).get("bats") or "R",
        "throws": (pl.get(pitcher_id) or {}).get("throws"),
        "home_team_id": (g or {}).get("home_team_id"),
        "american_odds": (od[0]["american_odds"] if od else None),
        "pitcher_ip": float(prow.get("innings_pitched") or 0),
        "pitcher_bbe": int(prow.get("bbe") or 0),
    }


def _pitcher_factor(pitcher_id, ip, bbe, sb):
    """z-scored pitcher factor (all L30): hr_per_9, hr_per_fb, fb_pct, with
    winsorize [-2,2] + sample dampening (ip<20 or bbe<30 → scale by ip/20)."""
    pm = PL.get_pitcher_hr_metrics(pitcher_id, "l30", sb=sb) if pitcher_id else \
        {"hr_per_9": None, "fb_pct": None, "hr_per_fb": None, "is_fallback": True, "data_age_days": None}
    small = (ip < PITCHER_MIN_IP) or (bbe < PITCHER_MIN_BBE)
    sw = min(1.0, ip / float(PITCHER_MIN_IP)) if PITCHER_MIN_IP else 1.0
    contrib, zs = 0.0, {}
    for feat, w in (("hr_per_9", PITCHER_W_HR_PER_9),
                    ("hr_per_fb", PITCHER_W_HR_PER_FB),
                    ("fb_pct", PITCHER_W_FB_PCT)):
        zraw = _clamp(FZ.z(feat, pm.get(feat), "l30", sb=sb), *PITCHER_Z_CLAMP)
        zeff = zraw * sw if small else zraw
        zs[feat] = round(zeff, 4)
        contrib += w * zeff
    pf = _clamp(1.0 + contrib, *PITCHER_FACTOR_CLAMP)
    return pf, zs, pm


def compute_hr_probability(batter_id, game_id, pitcher_id, *, sb=None, ctx=None):
    """
    Full v2 HR probability for one (batter, game, opposing pitcher). Returns the
    pick dict (model probs, edge, components, warnings) or None when the batter
    has no usable season HR rate. Pass `ctx` (pre-fetched) for batch efficiency;
    otherwise it self-fetches via `sb`.
    """
    if sb is None and ctx is None:
        raise ValueError("compute_hr_probability needs sb or ctx")
    if ctx is None:
        ctx = _fetch_ctx(batter_id, game_id, pitcher_id, sb)
    b = ctx.get("bstats")
    if not b or b.get("hr_per_pa") is None:
        return None

    shrunk = shrink_batter_hr_per_pa(float(b["hr_per_pa"]), b.get("pa") or 0)

    # batter profile factor — pulled-airball z only (observed rate already
    # captures raw power; pulled-airball is the distinct residual signal).
    paz = FZ.z("pulled_airball_rate", b.get("pulled_airball_rate"), "season", sb=sb)
    batter_factor = _clamp(1.0 + BATTER_W_PULLED_AIRBALL * paz, *BATTER_FACTOR_CLAMP)

    pitcher_factor, pz, pm = _pitcher_factor(
        pitcher_id, ctx.get("pitcher_ip", 0.0), ctx.get("pitcher_bbe", 0), sb)

    idx = PFL.get_park_hr_factor(ctx.get("home_team_id"), ctx.get("bats") or "R",
                                 ctx.get("throws"), sb=sb)
    park_mult = _clamp(idx / 100.0, *PARK_MULT_CLAMP)
    weather_mult = _clamp(WL.get_weather_hr_index(game_id, sb=sb), *WEATHER_MULT_CLAMP)

    per_pa = min(MODEL_INTERCEPT_C * shrunk * batter_factor * pitcher_factor
                 * park_mult * weather_mult, PROJ_PER_PA_CAP)
    expected_pas = PA_BY_LINEUP_SPOT.get(ctx.get("batting_order") or 6, 4.0)
    per_game = one_minus_pow_per_pa(per_pa, expected_pas)

    # devig + edge
    am = ctx.get("american_odds")
    dv = DV.devig_yes_only(am, market="hr_anytime")
    no_vig = dv["no_vig_prob"]
    longshot = (am is not None and am >= HR_LONGSHOT_THRESHOLD)
    edge = (per_game - no_vig) if no_vig is not None else None
    if am is None:
        status = None
    elif longshot or no_vig is None:
        status = "longshot_unrated"
    else:
        status = edge_bucket(edge)

    warnings = {
        "is_fallback": bool(pm.get("is_fallback")),
        "data_age_days": pm.get("data_age_days"),
        "lineup_source": ctx.get("lineup_source"),
        "longshot": longshot,
        "per_game_high": per_game > PER_GAME_HIGH_THRESHOLD,
    }
    return {
        "batter_id": batter_id, "game_id": game_id, "pitcher_id": pitcher_id,
        "model_version": MODEL_VERSION,
        "model_prob_per_pa": round(per_pa, 5),
        "model_prob_per_game": round(per_game, 5),
        "book_odds": am,
        "no_vig_prob": no_vig,
        "edge_pct": round(edge, 4) if edge is not None else None,
        "edge_status": status,
        "components": {
            "shrunk_observed_hr_per_pa": round(shrunk, 5),
            "batter_profile_factor": round(batter_factor, 4),
            "pitcher_factor": round(pitcher_factor, 4),
            "park_mult": round(park_mult, 4), "park_index": idx,
            "weather_mult": round(weather_mult, 4),
            "combined_factor": round(batter_factor * pitcher_factor * park_mult * weather_mult, 4),
            "expected_pas": expected_pas,
            "feature_zs": {"pulled_airball_z": round(paz, 4), **pz},
        },
        "warnings": warnings,
        "hard_reject": per_game > PER_GAME_REJECT_THRESHOLD,
    }


# ─── Integration check: reproduce harness numbers for the 5 named picks ───
def _run_selftests():
    from datetime import date
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    SLATE = "2026-06-16"
    games = {g["id"]: g for g in sb.table("games").select(
        "id, home_team_id, away_team_id, home_pitcher_id, away_pitcher_id")
        .eq("game_date", SLATE).execute().data or []}
    lus = sb.table("lineups").select("game_id, team_id, player_id, source, is_confirmed") \
        .in_("game_id", list(games)).execute().data or []
    names = {"Willson Contreras": 18.6, "Endy Rodríguez": 18.5, "Blaze Jordan": 10.5,
             "Blake Dunn": 11.5, "Xavier Edwards": None}
    pmap = {p["name"]: p["id"] for p in sb.table("players").select("id, name")
            .in_("name", list(names)).execute().data or []}
    ok = True
    for nm, expect_pg in names.items():
        pid = pmap.get(nm)
        lu = next((l for l in lus if l["player_id"] == pid and
                   (l.get("is_confirmed") or l.get("source") == "mlb_api")), None)
        if not lu:
            print(f"  {nm}: not in confirmed slate"); continue
        g = games[lu["game_id"]]
        opp = g["away_pitcher_id"] if lu["team_id"] == g["home_team_id"] else g["home_pitcher_id"]
        r = compute_hr_probability(pid, lu["game_id"], opp, sb=sb)
        pg = r["model_prob_per_game"] * 100
        edge = (r["edge_pct"] * 100) if r["edge_pct"] is not None else None
        tag = "" if expect_pg is None else f" (harness {expect_pg}%)"
        within = expect_pg is None or abs(pg - expect_pg) <= 0.5
        ok = ok and within
        print(f"  {nm:<20} per_game={pg:.1f}%{tag} edge={edge if edge is None else round(edge,2)}"
              f" status={r['edge_status']} {'OK' if within else 'DRIFT!'}")
    print("hr_model self-tests:", "ALL PASSED" if ok else "DRIFT DETECTED — investigate")


if __name__ == "__main__":
    _run_selftests()
