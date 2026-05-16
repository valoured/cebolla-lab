# 🧅 Cebolla Frontend — Phase 3

## Stack
- **Vue 3** (Composition API)
- **Vite** (dev + build)
- **Vue Router** (multi-view SPA)
- **@supabase/supabase-js** (direct DB reads, no backend)
- **Tailwind CSS** v3 (utility-first styling)
- **Chart.js + vue-chartjs** (line movement charts)
- **No state management library** — Pinia is overkill; composables suffice

## Aesthetic: "Lab Notebook"
- Deep warm-charcoal background `#0F0E0C`
- Onion-skin burnt-umber accent `#D97757`
- Complement: muted lab-teal `#5F9EA0`
- **Fonts:**
  - Display: **Fraunces** (Google, free) — serif, editorial
  - Body: **IBM Plex Sans** (Google, free) — distinctive, not Inter
  - Numbers: **JetBrains Mono** (Google, free) — tabular nums on
- Ruling lines instead of card containers (ledger aesthetic)
- Small-caps section labels
- Edge values: 4-step intensity scale (not binary red/green)

## Routes
| Path | Component | Purpose |
|---|---|---|
| `/` | `SlateView` | Today's MLB games, weather, HR factors |
| `/game/:gameId` | `HRReportView` | KrashBoard-style matchup grid |
| `/player/:playerId` | `PlayerView` | Batter deep-dive with arsenal stats |
| `/bets` | `BetTrackerView` | Bet log + ROI by edge bucket (Phase 5) |

## Side Panel Filtering
Collapsible left sidebar with:
- Date picker (default: today)
- Sport selector (MLB locked for now)
- Quick-filter chips: "Edge > +3%", "Coors only", "RHB only", etc.
- Bet log shortcut

## Data Flow
Vue components → Supabase JS client → read from public tables
- No backend, no API server
- Supabase RLS keeps writes locked, reads open for the views
- Realtime subscription on `odds_snapshots` so dashboard auto-refreshes when GH Actions writes new data

## Deploy
- Build: `npm run build` → outputs `dist/`
- Deploy: Cloudflare Pages, project: `cebolla-lab`, build command `npm run build`, output dir `dist`
- Domain: TBD (could be subdomain of osadia or fresh)

## File Layout
```
frontend/
├── index.html
├── package.json
├── vite.config.js
├── tailwind.config.js
├── postcss.config.js
├── src/
│   ├── main.js
│   ├── App.vue
│   ├── router.js
│   ├── supabase.js
│   ├── style.css
│   ├── composables/
│   │   ├── useSlate.js
│   │   ├── useGame.js
│   │   └── usePlayer.js
│   ├── components/
│   │   ├── SideNav.vue
│   │   ├── GameCard.vue
│   │   ├── HRReportTable.vue
│   │   ├── ArsenalGrid.vue
│   │   └── EdgeBadge.vue
│   └── views/
│       ├── SlateView.vue
│       ├── HRReportView.vue
│       ├── PlayerView.vue
│       └── BetTrackerView.vue
```
