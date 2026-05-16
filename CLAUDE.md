# Cebolla Laboratory — Master Spec

> "Peel back the layers." Personal MLB betting research platform inspired by KrashBoard.
> Built for HR unders and 1+ hits parlay strategy.

## Project Identity
- **Name:** Cebolla Laboratory (es. "Onion Laboratory")
- **Tagline:** Layers of data, one bet at a time
- **Theme:** Spanish, dark UI, data-dense — sister project to osadia.xyz
- **Owner:** Valoured (solo build)
- **Status:** Pre-alpha, scaffolding

## North Star Goals
1. **Replace KrashBoard sub** for personal MLB research
2. **Surface edges** the public sportsbook lines don't price correctly, with focus on HR unders and 1+ hits
3. **Track every bet** with edge-at-placement and ROI by edge bucket (the feedback loop KrashBoard lacks)
4. **Zero monthly cost** — entire stack free

## Stack (Verified Free)
| Layer | Service | Limit | Notes |
|---|---|---|---|
| Frontend | Cloudflare Pages | Unlimited | Same as osadia |
| Database | Supabase Postgres | 500 MB | Pauses after 1wk inactivity |
| Cron / data jobs | GitHub Actions | 2000 min/mo (or unlimited if public repo) | UTC scheduling, can be delayed |
| Statcast / FanGraphs / BRef | pybaseball (Python) | n/a | Scrapes Savant, FG, BRef |
| Lineups & schedules | MLB Stats API | Free, no key | statsapi.mlb.com |
| Weather | Open-Meteo | Free, no key | Per-stadium lat/lng |
| Odds (HR, hits, K props) | DraftKings public JSON | Free, no auth | sportsbook.draftkings.com endpoint |
| Future books | FanDuel JSON | Free, no auth | Phase 6+ |

## Architecture
```
┌────────────────────┐
│  GitHub Actions    │  Scheduled Python jobs
│  (cron, free)      │  pull data → write to Supabase
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  Supabase Postgres │  Tables: games, players, arsenals, batter_stats,
│  (free 500MB)      │  bvp_history, odds_snapshots, weather, projections, bet_log
└─────────┬──────────┘
          │  REST API (auto-generated)
          ▼
┌────────────────────┐
│  Cloudflare Pages  │  Static frontend reads from Supabase,
│  (free)            │  renders dashboards in browser
└────────────────────┘
```

## Data Sources (All Free, Verified)
### pybaseball (Python lib, scrapes 3 sites)
- `statcast(start_dt, end_dt)` — pitch-level Statcast
- `statcast_batter(start_dt, end_dt, player_id)` — batter Statcast
- `statcast_pitcher(start_dt, end_dt, player_id)` — pitcher Statcast w/ pitch types
- `batting_stats(year)` — FanGraphs season stats (300+ cols)
- `pitching_stats(year)` — FanGraphs pitching (Barrel%, HardHit%, xERA, etc.)
- `playerid_lookup(last, first)` — name → MLBAM ID

### MLB Stats API (statsapi.mlb.com, no auth)
- `GET /api/v1/schedule?date=YYYY-MM-DD` — today's slate
- `GET /api/v1/game/{gamePk}/boxscore` — confirmed lineups when available
- `GET /api/v1/teams` — team metadata

### DraftKings Public JSON (no auth, ToS gray area, personal use only)
- `GET https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240?format=json` — MLB event group
- MLB event group ID: **84240** (subject to change, re-verify each season)
- Player props live as sub-categories: HR, Hits, Total Bases, K's Thrown, etc.

### Open-Meteo (no auth)
- `GET https://api.open-meteo.com/v1/forecast?latitude=X&longitude=Y&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability`

## Database Schema (Supabase Postgres)
See `sql/01_schema.sql` for full DDL. Tables:
- `teams` — 30 MLB teams + stadium lat/lng + park factors
- `players` — MLBAM ID, name, position, bats/throws
- `games` — daily slate (date, away/home, time, pitchers, weather)
- `pitcher_arsenals` — per pitcher, per pitch type, vs LHB/RHB usage% and stats
- `batter_stats` — rolling window batter stats (L15, L50, season)
- `bvp_history` — career batter vs pitcher matchups
- `odds_snapshots` — timestamped odds for HR/hits/K's per player per book
- `projections` — model output: projected HR%, projected hit%, edge vs book
- `bet_log` — your bets with edge-at-placement, result, ROI

## Projection Model (Phase 4)
### HR%
```
projected_HR% = batter_HR_per_PA_L100
              × (pitcher_HR_per_9 / league_avg_HR_per_9)
              × park_HR_factor_by_handedness
              × weather_multiplier
              × arsenal_matchup_adjustment
```

`arsenal_matchup_adjustment` = weighted sum of batter's HR%-by-pitch-type, weighted by pitcher's usage% of that pitch vs the batter's handedness. **This is the "Krash rating" equivalent.**

### 1+ Hits %
```
projected_hit_prob = 1 - (1 - per_PA_hit_rate)^expected_PAs
where per_PA_hit_rate = batter_BA_vs_handedness × pitcher_BAA_factor × park_BA_factor
expected_PAs = lineup_spot_adjusted (1-3 hole: 4.4, 4-6: 4.1, 7-9: 3.7)
```

### Edge
```
edge = projected_prob - implied_prob_from_odds
implied_prob = 100 / (american_odds + 100)    [for positive odds]
implied_prob = -odds / (-odds + 100)          [for negative odds]
```
Then no-vig: divide each side's implied by sum of both implieds. Pure edge = projected - no_vig_implied.

## Roadmap
| Phase | Deliverable | Status |
|---|---|---|
| 0 | Scaffolding, schema, repo, GH Actions setup | IN PROGRESS |
| 1 | pybaseball pulls → Supabase (batter/pitcher stats, arsenals) | Pending |
| 2 | MLB lineups + Open-Meteo weather + DK odds scraper | Pending |
| 3 | Matchup grid view (Image 1 KrashBoard style) | Pending |
| 4 | Pitcher + batter deep-dive views (Images 2, 3) | Pending |
| 5 | Projection model + edge calculation | Pending |
| 6 | Bet tracker + ROI by edge bucket | Pending |
| 7 | Multi-book (add FanDuel, BetMGM JSON endpoints) | Future |
| 8 | Backtesting harness | Future |

## Strategy Bias
- **Primary use case 1:** HR unders (Coors flag is highest priority; favorable HR%/edge for fades)
- **Primary use case 2:** 1+ hits prop parlays
- **Secondary:** K prop research (strikeout overs/unders on starters)
- **Out of scope (for now):** moneylines, totals, RBI/total bases props

## Naming Conventions
- Code in English, UI strings can mix English/Spanish (osadia parallel)
- Tables: snake_case, plural (`pitcher_arsenals`)
- Cron job filenames: `pull_<source>.py` (e.g. `pull_savant.py`, `pull_dk_odds.py`)
- Branch naming: `feat/`, `fix/`, `data/`

## Critical Rules
1. **Never republish odds publicly.** Personal use only.
2. **Rate limit DK scraper.** Max 1 request / 10s. Cache aggressively.
3. **Always devig before computing edge.** Raw odds include vig; vs-vig "edges" are illusions.
4. **Log model version in projections table.** When model changes, old projections still attributable.
5. **No backtests on data the model trained on.** Walk-forward only.
