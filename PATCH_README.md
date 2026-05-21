# Cebolla Trends Patch — May 20, 2026

Real changes from tonight's session, packaged without the CRLF/LF
zip-extraction noise that polluted the git diff. Drop this into your
local repo root.

## What's in here

### NEW files (safe to drop in)
- `cebolla-frontend/src/composables/useTrends.js`  — data layer
- `cebolla-frontend/src/components/TrendRow.vue`   — single batter row
- `cebolla-frontend/src/views/TrendsView.vue`      — `/trends` page
- `sql/18_batter_game_log.sql`                     — migration 18
- `scripts/pull_batter_game_log.py`                — per-game pull script

### MODIFIED files (overwrite the existing ones)
- `cebolla-frontend/src/router.js`           — adds the `/trends` route
- `cebolla-frontend/src/components/TopNav.vue` — adds the Trends tab (M.07)
- `.github/workflows/daily-pulls.yml`        — wires the new pull script
                                               into the 2:13 AM ET nightly job

## Install order

1. **Copy all files** from this patch into your local `cebolla-lab/` repo,
   preserving the directory structure. Overwrite when prompted.

2. **Run the SQL migration**
   - Open Supabase SQL Editor
   - Paste the contents of `sql/18_batter_game_log.sql`
   - Run. Creates `batter_game_log` table + 2 helper views
     (`batter_recent_form`, `batter_active_streaks`).

3. **Test the frontend locally**
   ```
   cd cebolla-frontend
   npm run dev
   ```
   Visit `http://localhost:5173/trends`. Should render with whatever
   batters/lineups data is currently in Supabase. The L14-vs-Season
   divergence is computed from the existing `batter_stats` table
   (window_type='l14' and 'season') — no new cron run needed for the
   page itself to work.

4. **Commit + push**
   ```
   git add cebolla-frontend/src/composables/useTrends.js \
           cebolla-frontend/src/components/TrendRow.vue \
           cebolla-frontend/src/views/TrendsView.vue \
           cebolla-frontend/src/router.js \
           cebolla-frontend/src/components/TopNav.vue \
           sql/18_batter_game_log.sql \
           scripts/pull_batter_game_log.py \
           .github/workflows/daily-pulls.yml
   git commit -m "feat(trends): add /trends page + batter_game_log pipeline"
   git push
   ```

5. **Wait for the 2:13 AM ET cron** to populate `batter_game_log` with
   season-to-date games. Watch GitHub Actions for the
   `pull-batter-game-log` job. First run does ~700-game backfill in
   under 10 minutes.

## What works tonight vs. tomorrow

**Tonight (immediately after deploy):**
- `/trends` page renders using L14-vs-Season divergence from existing data
- 4 metric toggles: HR / Hits / Barrel% / ISO
- Direction toggle (Hot / Cold)
- "Playing Today" filter (uses lineups already in Supabase)
- Min PA slider
- Platoon-advantage pip on rows with confirmed matchups
- Click any row → goes to `/player/:id` (existing PlayerView)

**Tomorrow (after first 2:13 AM ET run of pull_batter_game_log):**
- `batter_game_log` has all season-to-date games
- `batter_recent_form` view gives you L5/L10/L15/L20/SZN hit-game-rate
- `batter_active_streaks` view gives you current hit / HR streaks
- Ready to build the Krashboard-style ring chart panel as a
  follow-on feature (separate session — not in tonight's scope)

## Sanity checks I ran on my end

- `npx vite build` passes cleanly with all new files
- All 7 trend-tier color classes verified in the production CSS bundle
- The Python pull script parses as valid Python 3.12
- The YAML workflow file parses as valid YAML
- The SQL migration uses `CREATE TABLE IF NOT EXISTS` and `CREATE OR
  REPLACE VIEW` — safe to re-run

## Known gaps / things deliberately left for a follow-up

- The Streaks page doesn't yet show L5/L10/L15/L20 ring charts (Krashboard
  Image 4 style) — needs `batter_game_log` populated first. Schema and
  views are ready; UI is the next session.
- No HR Feed / Dinger Feed yet. Would need a separate `hr_events` table
  populated from Statcast (EV, LA, distance, spray angle) — also a
  separate session.
- TrendRow click currently routes to `/player/:id`. PlayerView is the
  existing stub-ish view, so the destination isn't yet a "deep dive"
  in the Krashboard sense. Future polish.
