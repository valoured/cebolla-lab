# Cebolla Laboratory — Master Spec

> "Peel back the layers." Personal MLB betting research platform inspired by KrashBoard.
> Built for HR unders and 1+ hits parlay strategy.

## Project Identity
- **Name:** Cebolla Laboratory (es. "Onion Laboratory")
- **Tagline:** Layers of data, one bet at a time
- **Theme:** Spanish UI accents, off-black "lab notebook" aesthetic — sister to osadia.xyz
- **Owner:** Valoured (solo build)
- **Status:** Live at https://cebolla-lab.pages.dev (Phase 5 complete)
- **Stack cost:** $0/month, all free tiers

## Locations
- **Local repo:** `C:\Users\Jahn\Desktop\cebolla-lab\`
- **Frontend folder:** `cebolla-lab/cebolla-frontend/` (Vue 3 + Vite + Tailwind)
- **GitHub:** https://github.com/valoured/cebolla-lab (public, unlimited Actions)
- **Live URL:** https://cebolla-lab.pages.dev
- **Supabase project:** `aggkeltpfsmufixkjadh.supabase.co`

## North Star Goals
1. **Replace KrashBoard sub** for personal MLB research ✅
2. **Surface real edges** with honest devig and longshot filtering ✅ (Phase 4 v0.1.3)
3. **Track every bet** with edge-at-placement and ROI by edge bucket ✅ (Phase 5)
4. **Zero monthly cost** — entire stack free ✅

## Stack (Verified Free)
| Layer | Service | Notes |
|---|---|---|
| Frontend | Cloudflare Pages | Auto-deploys from main branch push |
| Database | Supabase Postgres | 500 MB limit; pauses after 1wk inactivity |
| Cron / data jobs | GitHub Actions | Unlimited minutes (public repo) |
| Statcast | pybaseball.statcast() | Raw pitch-level — bypasses broken FanGraphs |
| Pitcher season stats | MLB Stats API | Clean HR/9, K/9, BF, IP |
| Lineups & schedules | MLB Stats API | statsapi.mlb.com |
| Weather | Open-Meteo | Per-stadium lat/lng |
| Odds (HR, hits) | DraftKings public JSON | New endpoint (see below) |
| Future books | FanDuel JSON | Phase 9+ |

## Architecture
```
┌────────────────────┐
│  GitHub Actions    │  Scheduled Python jobs
│  (cron, free)      │  pull data → write to Supabase
└──────────┬─────────┘
           │
           ▼
┌────────────────────┐  11 tables + 5 views
│  Supabase Postgres │  teams, players, games, pitcher_arsenals,
│  (free 500MB)      │  pitcher_stats, batter_stats, bvp_history,
│                    │  odds_snapshots, projections, bet_log,
│                    │  lineups
└──────────┬─────────┘
           │  REST API
           ▼
┌────────────────────┐  Vue 3 SPA, osadia "lab notebook" aesthetic
│  Cloudflare Pages  │  Off-black #08080A, signal red #FF2A2A,
│  (free)            │  bracketed labels, JetBrains Mono numerics
└────────────────────┘
```

## Data Sources

### MLB Stats API (statsapi.mlb.com, no auth)
- `GET /api/v1/schedule?date=YYYY-MM-DD` — daily slate
- `GET /api/v1/game/{gamePk}/boxscore` — lineups + post-game batting lines (for bet settlement)
- `GET /api/v1/people/{mlbam_id}/stats?stats=season&group=pitching&season=YYYY` — clean HR/9, K/9
- `GET /api/v1/teams` — team metadata

### Baseball Savant (via pybaseball)
- `statcast(start_dt, end_dt)` — pitch-level, the only reliable bulk source
- `statcast_pitcher(start_dt, end_dt, mlbam_id)` — pitcher arsenal data
- **DO NOT** use `batting_stats()` or `pitching_stats()` — FanGraphs blocks pybaseball UA (403)

### DraftKings (no auth, ToS gray area — personal use only)
Old `/api/v5/eventgroups/` endpoint is **DEAD**. Live endpoint:
```
https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets
```
- MLB league ID: **84240**
- HR subcategory IDs: 17482, 17319 (regex on market name to disambiguate)
- Hits subcategory ID: 17320
- Required headers: `x-client-name`, `x-client-version`, `x-client-feature`, `x-client-page`, `Origin`, `Referer`
- Accepts GitHub Actions cloud IPs

### Open-Meteo (no auth)
- `GET /v1/forecast?latitude=X&longitude=Y&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability`

## Database Schema (Supabase Postgres)

**Migrations** (run in order in Supabase SQL Editor):
1. `sql/01_schema.sql` — Initial 10 tables + 2 views
2. `sql/02_lineups.sql` — Lineups table
3. `sql/03_pitcher_stats.sql` — Clean pitcher season stats
4. `sql/04_bet_tracker.sql` — Phase 5 bet log enhancements + ROI views

**Tables:**
- `teams` — 30 MLB teams + stadium coords + park HR factors by handedness
- `players` — MLBAM ID, name, team, bats/throws, is_pitcher
- `games` — daily slate + weather snapshot + computed hr_factor_overall/lhb/rhb
- `pitcher_arsenals` — pitcher × pitch_type × stance: usage%, velo, HR%, Barrel%, etc.
- `pitcher_stats` — pitcher × season: clean HR/9, K/9, BF, IP from MLB API ⚠️ **DO NOT recompute from arsenal — see Lessons**
- `batter_stats` — batter × window × handedness: HR/PA, hit/PA + `by_pitch_type` JSONB
- `bvp_history` — career batter-vs-pitcher (currently empty, scaffolded)
- `lineups` — game × team × batting_order × player
- `odds_snapshots` — game × player × market × book × american_odds, `is_current` flag
- `projections` — game × player × market × model_version: projected_prob + components + edge
- `bet_log` — placed_at, game, player, market, side, line, odds, stake, edge_at_placement, projected_prob, model_version, result, pnl, parlay_id, settled_at

**Views:**
- `today_slate` — today's games joined with team/pitcher/weather
- `today_lineups` — today's lineups with player names
- `bet_log_enriched` — bet_log joined with player/game/team info for frontend
- `roi_by_edge_bucket` — ROI grouped by edge bucket (5%+, 3-5%, 1-3%, 0-1%, neg) + win_rate
- `roi_by_model_version` — ROI grouped by model_version + win_rate

## Projection Model — Current: v0.1.3

**Formula (HR Anytime):**
```
shrunk_batter_hr_per_pa = (PA × observed + 200 × LEAGUE) / (PA + 200)
shrunk_pitcher_hr_per_9 = (BF × observed + 80 × LEAGUE) / (BF + 80)
pitcher_factor         = clamp(shrunk_p_hr9 / 1.15, [0.75, 1.40])

arsenal_adj    = weighted sum over pitcher's pitch usage% of batter's HR-rate-by-pitch
                 (clamped to [0.85, 1.15])
park_factor    = handedness-specific from teams table (clamped to [0.80, 1.20])

projected_per_pa  = shrunk_batter × pitcher_factor × park × arsenal_adj
                    (capped at 0.08)
projected_anytime = 1 - (1 - per_pa)^expected_PAs   (PA by lineup spot, table)
```

**Dynamic vig curve (Yes-side, replaces flat 6%):**
| Odds | Vig |
|---|---|
| ≤ +200 | 5% |
| +200 to +500 | 7% |
| +500 to +1000 | 10% |
| +1000 to +2000 | 13% |
| > +2000 | **Filtered: edge=null, bucket='longshot_unrated'** |

**Edge calculation:** `edge = projected_anytime - no_vig_implied` (only when devig possible).

**Edge buckets:** strong_back ≥+5%, lean_back +2 to +5%, flat ±2%, lean_fade -5 to -2%, strong_fade ≤-5%, longshot_unrated (filtered).

**Model version log:**
- **v0.1.0** — Initial. Flat 6% vig, no shrinkage. Edges ±15%. Abandoned.
- **v0.1.1** — Added Bayesian K=200 batter shrinkage. Still ±13%. Abandoned.
- **v0.1.2** — Fixed pitcher_factor source (new `pitcher_stats` table, MLB API). Still ±12% — vig curve still wrong.
- **v0.1.3** — Dynamic vig curve + longshot filter. **CURRENT.** Edges typically ±5%, median ~0. Median is honest (devig matches market on average).

**Status:** v0.1.3 is production. Don't tune the math without real settled-bet data. Let bet_log accumulate ≥50 settled bets across edge buckets before adjusting.

## Cron Jobs (`scripts/`)

| Script | Trigger | Purpose |
|---|---|---|
| `pull_schedule.py` | 3x daily (06/14/22 UTC) | MLB schedule + probable pitchers |
| `pull_savant.py` | 2x daily | Statcast batter stats with `by_pitch_type` JSONB |
| `pull_arsenals.py` | 1x daily (06 UTC) | Per-pitcher arsenal split by stance |
| `pull_pitcher_stats.py` | 1x daily (06 UTC) | MLB API clean season stats |
| `pull_weather.py` | 3x daily | Open-Meteo, wind→HR multiplier |
| `pull_lineups.py` | Hourly during slate | Boxscore lineups |
| `pull_dk_odds.py` | Hourly during slate | DK HR + Hits prop odds |
| `compute_projections.py` | Hourly after odds/lineups | The model |
| `settle_bets.py` | Hourly post-game + 14 UTC | Grade pending bets from boxscore |

Workflow file: `.github/workflows/daily-pulls.yml`

## Frontend (`cebolla-frontend/src/`)

**Routes (`router.js`):**
- `/` → `SlateView` (5-col grid of GameCards) [name: `slate`]
- `/game/:gameId` → `HRReportView` (matchup deep-dive) [name: `hr-report`]
- `/player/:playerId` → `PlayerView` ⚠️ still stub [name: `player`]
- `/bets` → `BetTrackerView` [name: `bets`]

**Component map:**
- `TopNav.vue` — Brand + Slate/Bet Log tabs + UTC clock
- `GameCard.vue` — Slate tile
- `BatterTable.vue` — Lineup × stats × edge × LOG button
- `ArsenalGrid.vue` — Pitcher × pitch types × stance
- `LogBetModal.vue` — Bet-logging modal (red onion-themed)

**Composables:**
- `useSlate.js` — Today's games
- `useGame.js` — Single matchup (lineups + arsenals + odds + projections + bvp)
- `useBetLog.js` — bet_log CRUD + ROI views

**Aesthetic locks** — don't drift:
- Background: `#08080A` off-black
- Accent: `#FF2A2A` signal red (use hex literals for critical UI — Tailwind tokens have been flaky in scoped styles)
- Complement: `#5F9EA0` lab teal
- Fonts: Syne (display), IBM Plex Sans (body), JetBrains Mono (numerics)
- Labels: `[ BRACKETED ALL CAPS ]` in label-bracket class
- Module codes: `[ M.01 ]` style identifiers
- Reticle corner brackets on hover/modals
- Spanish copy in empty states ("Sin partidos", "Sin alineación")
- 5-col fixed grid for slate

## Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Scaffolding, schema, repo, GH Actions | ✅ Done |
| 1 | Statcast batter/pitcher pulls + arsenals + weather | ✅ Done |
| 2 | DK odds scraper (new endpoint) | ✅ Done |
| 3 | Slate frontend (5-col, osadia aesthetic) | ✅ Done |
| 3.5 | Lineups job + HR Report grid | ✅ Done |
| 4 | Projection model (HR Anytime, v0.1.3) | ✅ Done |
| 5 | Bet Tracker + ROI views + settle_bets cron | ✅ Done |
| 6 | 1+ Hits market end-to-end | 🟡 Next |
| 7 | Realtime auto-refresh (Supabase subs) | Pending |
| 8 | Player Deep Dive page (stub today) | Pending |
| 9 | Multi-book (FanDuel) | Future |
| 10 | Backtesting harness | Future |

## Strategy Bias
- **Primary 1:** HR unders (Coors flag = highest priority; fade favorable HR%)
- **Primary 2:** 1+ hits prop parlays (Phase 6)
- **Secondary:** K prop research (future)
- **Out of scope:** moneylines, totals, RBI/total bases

## Lessons Learned — Don't Repeat

1. **FanGraphs is blocked.** `pybaseball.batting_stats()` and `pitching_stats()` return 403. Use raw `statcast()` + aggregate manually instead.

2. **DK's old eventgroups endpoint is dead.** Use the new `sportscontent/controldata` endpoint with full headers.

3. **Don't compute pitcher HR/9 from arsenal data.** `pitcher_arsenals.pa` double-counts (one PA spans multiple pitch types). Use `pitcher_stats.hr_per_9` from MLB Stats API instead. This bug cost a full session of debugging.

4. **Flat vig assumptions are wrong on longshots.** HR Anytime +5000 doesn't carry the same vig as -110. Use the dynamic curve. Filter +2000+ entirely; the model can't distinguish a true +5000 from a +8000 line.

5. **UTC date != local date.** Late-night sessions (post-8 PM ET) pull tomorrow's slate. Lineups won't be confirmed yet — expect many "0 projections" in cron output. Not a bug.

6. **Cloudflare directory pitfall.** Project root must be `cebolla-frontend/`, NOT `cebolla-frontend/cebolla-frontend/`. Folder-nesting bug burned 30 minutes.

7. **Tailwind tokens can fail in scoped Vue styles.** When `text-bg-50` refused to resolve in LogBetModal, fell back to hex literals in scoped CSS. Use hex for critical UI (submit buttons, etc.) to avoid silent invisibility.

8. **Schema migrations don't auto-run.** Always remind: paste SQL in Supabase Editor → Run. A 404 PGRST205 means the table doesn't exist yet.

9. **Router name must match TopNav references.** TopNav uses `name: 'bets'`, so the route must be named `'bets'` (not `'bet-tracker'`). Mismatch silently breaks the navbar link.

10. **The model is a research tool, not an oracle.** Even calibrated to ±5% edges, real edges of 2-3% are hard to confirm in <100 bets due to variance. Use the bet_log feedback loop for ground truth.

## Naming Conventions
- Code in English; UI strings may mix English/Spanish
- Tables: snake_case, plural
- Cron filenames: `pull_<source>.py`, `compute_<thing>.py`, `settle_<thing>.py`
- SQL migrations: `NN_description.sql` (zero-padded order)
- Branch naming: `feat/`, `fix/`, `data/`

## Critical Rules
1. **Never republish odds publicly.** Personal use only.
2. **Rate limit DK scraper.** Cron runs hourly during slate, that's the safe cadence.
3. **Always devig before computing edge.** Dynamic curve, not flat.
4. **Log model_version on every projection AND every bet_log row.** When the model changes, old projections + old bets stay attributable.
5. **No backtests on training data.** Walk-forward only.
6. **Service role key for writes, anon for reads.** Frontend never sees service role.
7. **`.env` is in `.gitignore`.** If keys accidentally commit, rotate immediately.

## Production State (May 2026)
- ✅ Live at https://cebolla-lab.pages.dev
- ✅ Model v0.1.3 producing rational edges
- ✅ Bet tracking + auto-settlement live
- ✅ All cron jobs green
- 🟡 Awaiting first real-money bets to populate ROI tables
- 🟡 1+ Hits market deferred to Phase 6
