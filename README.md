# 🧅 Cebolla Laboratory

> Personal MLB betting research platform. Peel back the layers.

A free, self-hosted alternative to KrashBoard built for HR unders + 1+ hits parlay strategy. Inspired by KrashBoard's UI, supercharged with personal bet tracking and ROI-by-edge analysis.

**Status:** Phase 0 — scaffolding complete. Phase 1 (data pulls) ready to test.

## Stack
- **Frontend:** Cloudflare Pages (free)
- **Database:** Supabase Postgres (free, 500 MB)
- **Cron:** GitHub Actions (free, 2000 min/mo)
- **Data:** pybaseball + MLB Stats API + DraftKings JSON + Open-Meteo
- **Total monthly cost:** $0

## Phase 0 Setup (do this once)

### 1. Create Supabase project
1. Go to https://supabase.com → New project
2. Pick a name like `cebolla-lab`, set a strong DB password
3. Wait ~2 min for provisioning
4. Go to **Settings → API** and copy:
   - Project URL (`SUPABASE_URL`)
   - `service_role` key (`SUPABASE_SERVICE_KEY`) — keep secret
   - `anon` key (`SUPABASE_ANON_KEY`) — frontend use

### 2. Run the schema
1. Supabase Dashboard → **SQL Editor**
2. Paste contents of `sql/01_schema.sql`
3. Click **Run** — should create 8 tables + 2 views

### 3. Create the GitHub repo
```bash
cd /path/to/cebolla-lab
git init
git add .
git commit -m "Initial scaffolding"
gh repo create cebolla-lab --public --source=. --push
```
Public repo = unlimited GitHub Actions minutes.

### 4. Add secrets to GitHub
On the repo: **Settings → Secrets and variables → Actions → New repository secret**
Add three secrets:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_ANON_KEY`

### 5. Seed teams (one-time)
On your local machine:
```bash
cd scripts
pip install -r requirements.txt
cp ../.env.example ../.env  # then fill in real values
python seed_teams.py
```
You should see `Seeded 30 teams.`

### 6. Test the schedule puller
```bash
python pull_schedule.py
```
You should see something like `🧅 Cebolla Lab — pulling schedule for 2026-05-16` and then a count of games upserted.

Check Supabase: **Table Editor → games** should show today's games.

### 7. Verify GitHub Actions
1. Push to GitHub if you haven't
2. Repo → **Actions tab** → "Cebolla — Daily Data Pulls" → **Run workflow**
3. Should complete in ~30s. Re-check Supabase to confirm data refreshed.

## What's Next (Phase 1)
Once Phase 0 is verified, we build:
- `pull_savant.py` — batter/pitcher Statcast via pybaseball
- `pull_arsenals.py` — per-pitch usage and stats by stance
- `pull_weather.py` — Open-Meteo per stadium
- `pull_dk_odds.py` — DraftKings public JSON for HR/hits props

See `CLAUDE.md` for the full roadmap.

## Project Structure
```
cebolla-lab/
├── CLAUDE.md                    # Master spec (always read first)
├── README.md                    # This file
├── sql/
│   └── 01_schema.sql            # Run in Supabase SQL editor
├── scripts/
│   ├── requirements.txt
│   ├── seed_teams.py            # One-time: populate 30 teams
│   └── pull_schedule.py         # Phase 0: daily schedule pull
├── frontend/                    # Phase 3+: Cloudflare Pages site
├── .github/workflows/
│   └── daily-pulls.yml          # GH Actions cron config
└── .env.example
```

## Notes
- **Supabase free tier pauses after 7 days inactivity.** GitHub Actions cron pings keep it alive.
- **DK scraping is for personal use only.** Don't republish odds.
- **All scheduled times in workflows are UTC.** Adjust for ET.
