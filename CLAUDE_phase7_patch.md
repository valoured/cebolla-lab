# Phase 7 — FanGraphs replication (COMPLETED 2026-05-18)

Append this section to CLAUDE.md or your project docs.

## Architecture decisions

**Why we extended existing tables instead of creating `batter_statcast_stats`.**
The `batter_stats` table already had `window_type` (text) and `vs_hand` (char) columns plus most Statcast fields (`barrel_pct`, `hard_hit_pct`, `ev_avg`, `la_avg`, `pull_pct`). Splitting Statcast into a separate table would create two sources of truth for the same batter-window key. Same logic for `pitcher_stats`.

**Why we did 4 windows (Season / L30 / L14 / L7) and dropped L3.**
L3 = ~12 PAs for most batters. Pure noise. L7 (~28 PAs) is the smallest window with any statistical meaning. Below MIN_PA_FOR_WINDOW=5, rows are skipped entirely.

**Why vs-hand splits ONLY exist in the season window.**
At L14, a batter might have ~14 PAs vs LHP — splitting them into L/R/A rows is meaningless. Rolling windows write a single 'A' (all) row each.

**Why `by_pitch_type` JSONB only populates for the season window.**
Same logic. Per-pitch-type breakdown at small samples is mostly empty bins. The arsenal-weighted projection model only reads the season row anyway.

**Why pitchers are aggregated from the same Statcast pull.**
`pull_savant.py` already pulls ~245k pitches per run. Grouping that data by `pitcher` instead of `batter` is free — no second API hit. Pitcher-allowed rows write only the Statcast columns; non-Statcast columns (era, fip, whip, k_per_9) come from `pull_pitcher_stats.py` and stay intact.

**Why BatterTable fetches its own Statcast instead of receiving it as prop.**
Window-switching needs to trigger a refetch. Putting that logic inside the component keeps the data flow self-contained. `useGame.js` no longer fetches `batter_stats` — that was redundant data in Phase 7.

**Default window is L14.**
Big enough sample to mean something (~50-60 PAs). Recent enough to capture form. Detects mid-season slumps/heaters that Season hides.

**Why color thresholds are hardcoded instead of computed.**
League-wide percentiles are reasonably stable across seasons. Hardcoded ranges in `percentileColors.js` are directionally correct and require zero DB lookups. If we want to refine later, we can compute thresholds nightly from `batter_stats` itself.

## Critical lessons learned

### MLB date assignment — use `officialDate`, never UTC date slice
**Bug:** `pull_schedule.py` was using `game["gameDate"][:10]`. For a 9:40 PM PT game in San Diego, `gameDate` is `2026-05-19T04:40:00Z`. UTC date slice = May 19, but MLB considers it May 18. Result: 5 West Coast games disappeared from the May 18 slate.

**Fix:** MLB's API gives us `officialDate` directly. Use that. Pull a 3-day window to be safe across cron timing edges.

### GitHub Actions runs in UTC
`date.today()` on the runner returns UTC date. For "today in baseball terms" use `datetime.now(timezone.utc) - timedelta(hours=4)` for ET.

### Supabase upsert payload columns are partial-update
When the same row gets multiple upserts from different scripts (e.g. pull_savant writes Statcast columns to pitcher_stats, pull_pitcher_stats writes era/fip/whip), Supabase only updates the columns present in each payload. The other columns retain their values. Confirmed working in production.

### Always verify Supabase project ID before running diagnostics
Easy to be in the wrong project's SQL editor. Check the URL or project ID in the sidebar before assuming a query failed.

### Statcast aggregation only writes for known pitchers
`pull_savant.py` skips pitcher_ids not in `players` table. This is intentional — we only care about probable starters, not random middle relievers. Of 996 unique pitchers in Statcast data, only 92 of our tracked starters get Statcast rows. The 904 skipped are reliever noise.

## File map

**Backend:**
- `sql/07_statcast_stats.sql` — migration adding 19 new columns
- `scripts/pull_savant.py` — rewritten for 4 windows + xStats + pitcher aggregation

**Frontend:**
- `cebolla-frontend/src/composables/useStatcast.js` — `useStatcastBatters(playerIdsRef, window)` + `useStatcastPitcher(pitcherIdRef, window)`
- `cebolla-frontend/src/utils/percentileColors.js` — `statColor()` + `fmtStat()` with separate batter/pitcher thresholds
- `cebolla-frontend/src/components/StatcastWindowToggle.vue` — Season / L30 / L14 / L7 pill toggle
- `cebolla-frontend/src/components/BatterTable.vue` — full rewrite, owns Statcast fetch
- `cebolla-frontend/src/components/PitcherAllowedStats.vue` — inline strip below pitcher arsenal
- `cebolla-frontend/src/views/HRReportView.vue` — wraps pitchers in unified cards
- `cebolla-frontend/src/composables/useGame.js` — removed dead `batter_stats` fetch

## Phase 7 verification (from production data)

**Aaron Judge L7 vs Season trajectory** — verified actual data showing his real-time decline:
- Season: 22.81% Barrel%, .574 xwOBA (generational)
- L30: 18.33% Barrel%, .573 xwOBA (still elite)
- L14: 9.38% Barrel%, .480 xwOBA (slumping)
- L7: 0.00% Barrel%, .327 xwOBA (ice cold)

**Framber Valdez allowed L14 vs Season** — same story, opposite side:
- Season: 5.73% Brl%, .513 xSLG, .359 xwOBA (pitching well)
- L14: 11.76% Brl%, .671 xSLG, .446 xwOBA (getting hammered)

These are the narratives Cebolla can tell that Krashboard cannot.

## Open follow-ups for Phase 8+

- **Player Deep Dive page** — `/player/:playerId` with the full Statcast trend history
- **Per-pitch-type batter splits in non-season windows** — currently only season has by_pitch_type, expanding would enable "this hitter crushes sweepers in L14"
- **Computed percentile thresholds** — refresh weekly from production data instead of hardcoded
- **Pinnacle as second odds source + market-based edges** — Phase 11+, the EV Sharps style integration
- **LAUNCH-CHECKLIST.md** — track confusion points as they surface (Phase 12)
