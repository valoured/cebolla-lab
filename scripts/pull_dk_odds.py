"""
pull_dk_odds.py — Scrape DraftKings MLB player props (HR, Hits, RBI).

Uses DraftKings' current public web API (no auth, no cookies). The old
/api/v5/eventgroups/ endpoints are dead; the live one is:

  /sites/US-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets

Each request returns ONE subcategory's worth of markets across all events
in a league. We iterate over a small set of subcategory IDs that map to
HR / Hits / RBI props, then match player names to our `players` table.

PERSONAL USE ONLY. Rate-limited, throttled, not republished anywhere.

Runs hourly during slate window via GitHub Actions.
"""

import os
import sys
import time
import logging
import re
import urllib.parse
import unicodedata
from datetime import datetime, timezone, date

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ────────────────────────────────────────────────────────────────
# DK ENDPOINT CONFIG
# ────────────────────────────────────────────────────────────────
DK_LEAGUE_ID_MLB = 84240
DK_BASE = (
    "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/"
    "controldata/league/leagueSubcategory/v1/markets"
)

# Subcategory IDs we care about. Verified from live DK page:
#   17482, 17319, 17320 → some combination of HR/Hits/RBI player props
# We classify by the `name` field on each market in the response so we
# don't need to know which ID is which upfront. Add more IDs if needed.
DK_SUBCATEGORY_IDS = [17482, 17319, 17320]

# Headers that mirror the real Chrome request from DK's web UI.
# No auth tokens, but DK checks for x-client-* fingerprints.
DK_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://sportsbook.draftkings.com",
    "Referer": "https://sportsbook.draftkings.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "x-client-feature": "leagueSubcategory",
    "x-client-name": "web",
    "x-client-page": "league",
    "x-client-version": "2620.4.2.4",
}

# Be polite. 1 request every few seconds.
REQUEST_DELAY_SEC = 4

# Market name regex → (our market key, line)
# We pattern-match on the `name` field of each market.
# DK formats them as e.g. "Aaron Judge Home Runs", "Luis Arraez Hits", "Pete Alonso RBIs"
MARKET_PATTERNS = [
    # HR props
    (re.compile(r"home\s*runs?|to\s+hit\s+a\s+(?:hr|homer)", re.I), "hr_anytime", 0.5),
    # Hits
    (re.compile(r"\bhits?\b|player\s+hits|to\s+record\s+a\s+hit", re.I), "hits", 0.5),
    # RBI
    (re.compile(r"\brbis?\b|runs\s+batted\s+in", re.I), "rbi", 0.5),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# HTTP
# ────────────────────────────────────────────────────────────────

def build_url(subcat_id: int) -> str:
    """Construct one DK markets URL for a specific subcategory."""
    events_filter = (
        f"$filter=leagueId eq '{DK_LEAGUE_ID_MLB}' "
        f"AND clientMetadata/Subcategories/any(s: s/Id eq '{subcat_id}')"
    )
    markets_filter = (
        f"$filter=clientMetadata/subCategoryId eq '{subcat_id}' "
        f"AND tags/all(t: t ne 'SportcastBetBuilder')"
    )
    params = {
        "isBatchable": "false",
        "templateVars": f"{DK_LEAGUE_ID_MLB},{subcat_id}",
        "eventsQuery": events_filter,
        "marketsQuery": markets_filter,
        "include": "Events",
        "entity": "events",
    }
    return f"{DK_BASE}?{urllib.parse.urlencode(params)}"


def fetch_subcategory(subcat_id: int) -> dict | None:
    url = build_url(subcat_id)
    log.info("Fetching subcat %s …", subcat_id)
    try:
        r = requests.get(url, headers=DK_HEADERS, timeout=20)
        if not r.ok:
            log.warning("  status=%d", r.status_code)
            return None
        ct = r.headers.get("content-type", "")
        if "json" not in ct:
            log.warning("  unexpected content-type: %s", ct)
            return None
        return r.json()
    except Exception as e:
        log.warning("  exception: %s", e)
        return None


# ────────────────────────────────────────────────────────────────
# Player name matching
# ────────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Strip accents/punct/suffixes, lowercase."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    no_acc = "".join(c for c in nfkd if not unicodedata.combining(c))
    no_punct = re.sub(r"[.,'\-]", " ", no_acc)
    tokens = [
        t for t in no_punct.split()
        if t.lower() not in {"jr", "sr", "ii", "iii", "iv", "v"}
    ]
    return " ".join(tokens).lower().strip()


def build_player_index() -> dict[str, int]:
    res = sb.table("players").select("id, name").execute()
    return {
        normalize_name(p["name"]): p["id"]
        for p in res.data if p.get("name")
    }


# ────────────────────────────────────────────────────────────────
# Game matching (DK event → our games.id)
# ────────────────────────────────────────────────────────────────

# DK seoIdentifier format: "kc-royals-%40-stl-cardinals"
# That decodes to "kc-royals-@-stl-cardinals". We extract team tokens.

TEAM_TOKEN_TO_ABBREV = {
    "ari-diamondbacks": "ARI", "atl-braves": "ATL", "bal-orioles": "BAL",
    "bos-red-sox": "BOS", "chi-cubs": "CHC", "chi-white-sox": "CWS",
    "cin-reds": "CIN", "cle-guardians": "CLE", "col-rockies": "COL",
    "det-tigers": "DET", "hou-astros": "HOU", "kc-royals": "KC",
    "la-angels": "LAA", "la-dodgers": "LAD", "mia-marlins": "MIA",
    "mil-brewers": "MIL", "min-twins": "MIN", "ny-mets": "NYM",
    "ny-yankees": "NYY", "athletics": "ATH", "oak-athletics": "ATH",
    "phi-phillies": "PHI", "pit-pirates": "PIT", "sd-padres": "SD",
    "sf-giants": "SF", "sea-mariners": "SEA", "stl-cardinals": "STL",
    "tb-rays": "TB", "tex-rangers": "TEX", "tor-blue-jays": "TOR",
    "was-nationals": "WSH",
}


def parse_dk_event_teams(seo_id: str) -> tuple[str, str] | None:
    """'kc-royals-%40-stl-cardinals' → ('KC', 'STL')."""
    decoded = urllib.parse.unquote(seo_id or "").lower()
    # Split on '@' (away @ home convention)
    parts = decoded.split("-@-") if "-@-" in decoded else decoded.split("-vs-")
    if len(parts) != 2:
        return None
    away_token = parts[0].strip("-")
    home_token = parts[1].strip("-")
    away = TEAM_TOKEN_TO_ABBREV.get(away_token)
    home = TEAM_TOKEN_TO_ABBREV.get(home_token)
    if not (away and home):
        return None
    return (away, home)


def build_dk_event_to_game_id_map(dk_events: list) -> dict[str, int]:
    """{dk_event_id: our games.id} for events we recognize."""
    teams_res = sb.table("teams").select("id, abbrev").execute()
    abbrev_to_id = {t["abbrev"]: t["id"] for t in teams_res.data}

    today = date.today().isoformat()
    games_res = sb.table("games") \
        .select("id, away_team_id, home_team_id, game_date") \
        .gte("game_date", today) \
        .execute()

    # Index our games by (away_team_id, home_team_id) → game_id, prefer today's
    by_pair: dict[tuple[int, int], int] = {}
    for g in games_res.data:
        key = (g["away_team_id"], g["home_team_id"])
        # Prefer today's game over future ones
        if key not in by_pair or g["game_date"] == today:
            by_pair[key] = g["id"]

    mapping: dict[str, int] = {}
    for ev in dk_events:
        teams = parse_dk_event_teams(ev.get("seoIdentifier", ""))
        if not teams:
            continue
        a, h = teams
        away_id = abbrev_to_id.get(a)
        home_id = abbrev_to_id.get(h)
        if not (away_id and home_id):
            continue
        game_id = by_pair.get((away_id, home_id))
        if game_id:
            mapping[str(ev["id"])] = game_id
    return mapping


# ────────────────────────────────────────────────────────────────
# Odds math
# ────────────────────────────────────────────────────────────────

def american_to_implied(odds: int | None) -> float | None:
    if odds is None:
        return None
    if odds >= 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def american_to_decimal(odds: int | None) -> float | None:
    if odds is None:
        return None
    if odds >= 0:
        return 1 + (odds / 100)
    return 1 + (100 / -odds)


# ────────────────────────────────────────────────────────────────
# Parsing
# ────────────────────────────────────────────────────────────────

def classify_market(market_name: str) -> tuple[str, float] | None:
    """Pattern-match a market `name` to our internal market key."""
    if not market_name:
        return None
    for pattern, key, line in MARKET_PATTERNS:
        if pattern.search(market_name):
            return (key, line)
    return None


def extract_american_odds(selection: dict) -> int | None:
    """
    Selections can have odds in various shapes:
      {"displayOdds": {"american": "+250"}, ...}
      {"odds": {"american": "+250"}, ...}
      {"americanOdds": "+250", ...}
      {"oddsAmerican": "+250", ...}
    Be defensive.
    """
    candidates = []
    for path in (
        ("displayOdds", "american"),
        ("odds", "american"),
        ("americanOdds",),
        ("oddsAmerican",),
    ):
        v = selection
        for k in path:
            if isinstance(v, dict):
                v = v.get(k)
            else:
                v = None
                break
        if v is not None:
            candidates.append(v)

    for raw in candidates:
        try:
            s = str(raw).replace("+", "").replace("−", "-").strip()
            return int(s)
        except (ValueError, TypeError):
            continue
    return None


def detect_side(label: str) -> str:
    """Map an outcome label to over/under/yes/no/single-sided."""
    if not label:
        return "yes"
    l = label.lower()
    if "over" in l or l == "yes":
        return "over"
    if "under" in l or l == "no":
        return "under"
    return "yes"  # default for "Anytime HR" style single-side


def extract_player_from_market(market: dict) -> str | None:
    """
    DK markets look like 'Aaron Judge Home Runs', 'Luis Arraez Hits',
    'Pete Alonso RBIs', or 'Will Smith (LAD) Home Runs' for ambiguous names.
    We strip the trailing prop type and any team disambiguation parens.
    """
    name = market.get("name") or ""
    # Strip known prop suffixes (plurals included) from end of market name
    candidate = re.sub(
        r"\s+(?:home\s*runs?|to\s+hit\s+a\s+home\s*runs?|hits?|rbis?|"
        r"runs\s+batted\s+in|total\s+bases|stolen\s+bases?|"
        r"o/?u|over|under|0\.5|1\.5|2\.5)\b.*$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip(" -–|:")
    # Strip trailing team disambiguation like "Will Smith (LAD)"
    candidate = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", candidate).strip()
    return candidate or None


def parse_response(
    data: dict,
    player_index: dict[str, int],
    dk_event_to_game: dict[str, int],
) -> list[dict]:
    """Walk the response and produce odds_snapshots rows."""
    rows: list[dict] = []
    markets = data.get("markets") or []
    selections = data.get("selections") or []

    # Index selections by marketId
    sels_by_mkt: dict[str, list[dict]] = {}
    for sel in selections:
        mkt_id = str(sel.get("marketId", ""))
        sels_by_mkt.setdefault(mkt_id, []).append(sel)

    snapshot_time = datetime.now(timezone.utc).isoformat()

    for mkt in markets:
        market_name = mkt.get("name") or ""
        classified = classify_market(market_name)
        if not classified:
            continue
        market_key, line = classified

        dk_event_id = str(mkt.get("eventId", ""))
        game_id = dk_event_to_game.get(dk_event_id)
        if not game_id:
            continue

        # Determine player
        player_raw = extract_player_from_market(mkt)
        if not player_raw:
            continue
        player_normalized = normalize_name(player_raw)
        player_id = player_index.get(player_normalized)
        if not player_id:
            continue

        # Selections for this market (over/under or yes)
        mkt_sels = sels_by_mkt.get(str(mkt.get("id", "")), [])
        for sel in mkt_sels:
            american = extract_american_odds(sel)
            if american is None:
                continue
            side = detect_side(sel.get("label", ""))
            decimal_ = american_to_decimal(american)
            implied = american_to_implied(american)

            # Build market key with side
            full_market = (
                f"{market_key}_under" if side == "under"
                else f"{market_key}_over" if side == "over"
                else f"{market_key}_yes"
            )
            rows.append({
                "game_id": game_id,
                "player_id": player_id,
                "market": full_market,
                "book": "draftkings",
                "american_odds": american,
                "decimal_odds": round(decimal_, 3) if decimal_ else None,
                "implied_prob": round(implied, 4) if implied is not None else None,
                "line": line,
                "snapshot_time": snapshot_time,
                "is_current": True,
            })

    return rows


# ────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────

def main():
    log.info("🧅 Cebolla Lab — DK odds sync starting")

    player_index = build_player_index()
    log.info("Built player index: %d players", len(player_index))

    all_rows: list[dict] = []
    unmatched_players: set[str] = set()

    for subcat_id in DK_SUBCATEGORY_IDS:
        data = fetch_subcategory(subcat_id)
        if not data:
            continue

        events = data.get("events") or []
        markets = data.get("markets") or []
        log.info("  subcat %d: %d events, %d markets",
                 subcat_id, len(events), len(markets))

        dk_event_to_game = build_dk_event_to_game_id_map(events)
        log.info("  matched %d/%d DK events to our games",
                 len(dk_event_to_game), len(events))

        # Detect what kinds of markets came back (for visibility)
        kinds = {}
        for m in markets:
            nm = (m.get("name") or "").split(" - ")[-1][:40]
            kinds[nm] = kinds.get(nm, 0) + 1
        if kinds:
            log.info("  market name samples: %s",
                     dict(list(kinds.items())[:5]))

        rows = parse_response(data, player_index, dk_event_to_game)
        log.info("  parsed %d odds rows", len(rows))
        all_rows.extend(rows)

        # Diagnostic: which players showed up but weren't in our DB?
        for m in markets:
            nm = extract_player_from_market(m)
            if nm and normalize_name(nm) not in player_index:
                unmatched_players.add(nm)

        time.sleep(REQUEST_DELAY_SEC)

    if all_rows:
        # Insert in batches of 500
        for i in range(0, len(all_rows), 500):
            chunk = all_rows[i:i + 500]
            sb.table("odds_snapshots").insert(chunk).execute()
        log.info("✓ Inserted %d odds rows", len(all_rows))
    else:
        log.warning("✗ Zero odds rows inserted — check market patterns / player matching")

    if unmatched_players:
        log.info("Players seen but not in our DB (%d total, showing 10):",
                 len(unmatched_players))
        for nm in list(unmatched_players)[:10]:
            log.info("  - %s", nm)

    log.info("🧅 DK sync complete")


if __name__ == "__main__":
    main()
