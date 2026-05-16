# 🧅 Cebolla Frontend

Vue 3 + Vite + Tailwind dashboard for Cebolla Lab.

## Local dev

```bash
npm install
cp .env.example .env  # fill in VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
npm run dev
```

Open http://localhost:5173

## Build

```bash
npm run build
```

Outputs to `dist/`.

## Deploy to Cloudflare Pages

1. Push the `frontend/` directory contents to a GitHub repo (or push as a subfolder of cebolla-lab and configure Pages to point to it)
2. Cloudflare Pages → Create project → Connect to GitHub
3. Build command: `npm run build`
4. Build output directory: `dist`
5. Environment variables (add in Pages settings):
   - `VITE_SUPABASE_URL`
   - `VITE_SUPABASE_ANON_KEY`

## Routes
- `/` — Slate (today's games)
- `/game/:gameId` — HR Report matchup grid (Phase 3.5)
- `/player/:playerId` — Batter deep dive (Phase 3.5)
- `/bets` — Bet log (Phase 5)

## Design system

See `tailwind.config.js`. Key tokens:
- Background: `bg-ink-0` (warm charcoal `#0F0E0C`)
- Primary text: `text-ink-700`
- Accent (umber): `text-umber-300` (`#D97757`)
- Complement (teal): `text-teal-500` (`#5F9EA0`)
- Display font: `font-display` (Fraunces)
- Body: `font-sans` (IBM Plex Sans)
- Numbers: `font-mono` (JetBrains Mono, tabular)

Custom utilities in `style.css`:
- `.label-caps` — small-caps lab labels
- `.display-num` — tabular monospace numbers
- `.display-text` — Fraunces serif
- `.edge-pill` + `.edge-{back-strong,back-mild,flat,fade-mild,fade-strong}`
- `.data-table` — dense table styling
