"""
backfill_bats.py — One-time repair for null players.bats.

Background
----------
For most of the season, pull_lineups.py upserted player rows with bats=null
whenever the lineup source (boxscore / last-known lineup) lacked batSide. Since
a PostgREST upsert overwrites every supplied column on conflict, that null
clobbered the correct value pull_rosters.py had set — leaving ~72% of position
players with bats=null. The frontend then defaulted unknown handedness to 'R',
mislabeling lefties like Olson/Devers/Duran as right-handed.

The regression itself is fixed in pull_lineups.py (it no longer writes a null
bats). This script repairs the rows that were already damaged.

What it does
------------
1. Pages through `players` where bats IS NULL and has an mlbam_id.
2. Looks their batSide up from the MLB Stats API /people endpoint, batched.
3. Writes the resolved code ('L' / 'R' / 'S') back to players.bats.

Idempotent: a successful row no longer matches `bats IS NULL`, so re-running is
harmless and simply finds fewer (eventually zero) rows. Players the MLB API has
no batSide for (rare) are left null and reported.

Usage
-----
    python scripts/backfill_bats.py

Requires SUPABASE_URL + SUPABASE_SERVICE_KEY in the environment (writes use the
service role, like settle_pods.py / settle_cards.py).
"""

import os
import sys
import time
import logging

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_bats")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MLB_API = "https://statsapi.mlb.com/api/v1"

# MLB's /people endpoint accepts a comma-separated personIds list. 100 keeps the
# URL well under any practical length limit and the response small.
CHUNK_SIZE = 100
# Be polite to a free public API — a brief pause between batched calls.
SLEEP_BETWEEN_CHUNKS = 0.3
PAGE_SIZE = 1000


def fetch_null_bats_players() -> list[dict]:
    """All players with bats IS NULL and a non-null mlbam_id, paged."""
    out: list[dict] = []
    start = 0
    while True:
        res = (
            sb.table("players")
            .select("id, mlbam_id, name")
            .is_("bats", "null")
            .not_.is_("mlbam_id", "null")
            .order("id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows = res.data or []
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


def chunked(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_bat_sides(mlbam_ids: list[int]) -> dict[int, str]:
    """{mlbam_id: batSide_code} from MLB /people, for one batch of ids."""
    ids_param = ",".join(str(i) for i in mlbam_ids)
    url = f"{MLB_API}/people"
    try:
        r = requests.get(url, params={"personIds": ids_param}, timeout=20)
        r.raise_for_status()
        people = r.json().get("people", [])
    except Exception as e:
        log.warning("  /people batch failed (%d ids): %s", len(mlbam_ids), e)
        return {}

    out: dict[int, str] = {}
    for p in people:
        pid = p.get("id")
        code = (p.get("batSide") or {}).get("code")
        if pid is not None and code:
            out[pid] = code
    return out


def main():
    log.info("🧅 bats backfill — scanning for null players.bats")
    players = fetch_null_bats_players()
    log.info("Found %d player(s) with null bats", len(players))
    if not players:
        log.info("Nothing to backfill — done.")
        return

    by_mlbam = {p["mlbam_id"]: p for p in players}
    mlbam_ids = list(by_mlbam.keys())

    resolved = 0
    unresolved: list[str] = []

    for batch in chunked(mlbam_ids, CHUNK_SIZE):
        sides = fetch_bat_sides(batch)

        updates = []
        for mlbam_id in batch:
            code = sides.get(mlbam_id)
            row = by_mlbam[mlbam_id]
            if not code:
                unresolved.append(f'{row["name"]} (mlbam {mlbam_id})')
                continue
            updates.append({"id": row["id"], "bats": code})

        # UPDATE (not upsert) is the right verb here: PostgREST upsert is an
        # INSERT .. ON CONFLICT, so a partial {id, bats} payload fails the
        # NOT NULL check on mlbam_id before the conflict-update can fire. Group
        # the batch by code (only L/R/S exist) and update each group by id list,
        # which touches the bats column alone and leaves every other column be.
        if updates:
            by_code: dict[str, list[int]] = {}
            for u in updates:
                by_code.setdefault(u["bats"], []).append(u["id"])
            for code, ids in by_code.items():
                sb.table("players").update({"bats": code}).in_("id", ids).execute()
            resolved += len(updates)
            log.info("  updated %d (running total %d/%d)",
                     len(updates), resolved, len(players))

        time.sleep(SLEEP_BETWEEN_CHUNKS)

    log.info("✓ Backfill complete — resolved %d/%d", resolved, len(players))
    if unresolved:
        log.warning("MLB API returned no batSide for %d player(s):", len(unresolved))
        for name in unresolved:
            log.warning("    - %s", name)


if __name__ == "__main__":
    main()
