<script setup>
/**
 * MethodologyView.vue — How Cebolla Lab computes its numbers.
 *
 * Public-facing reference page. Sectioned with a sticky side-nav on desktop;
 * collapses to an accordion on mobile.
 *
 * Source-name gatekept by design: we describe categories of data (raw
 * pitch-level events, traditional pitching totals, weather feeds, posted
 * odds) without naming the specific APIs or libraries that power them.
 *
 * Formulas live in expandable <details> blocks under each stat — plain
 * English first, math behind the disclosure if you want it.
 */

import { ref, onMounted, onUnmounted, nextTick } from 'vue'

// ── Side nav state ──────────────────────────────────────────────
const sections = [
  { id: 'overview',      label: 'What this is',          code: 'M.03.a' },
  { id: 'data',          label: 'Data philosophy',       code: 'M.03.b' },
  { id: 'windows',       label: 'Rolling windows',       code: 'M.03.c' },
  { id: 'combined-heat', label: 'Combined Heat',         code: 'M.03.d' },
  { id: 'statcast',      label: 'Statcast stats',        code: 'M.03.e' },
  { id: 'pitcher-side',  label: 'Pitcher-allowed stats', code: 'M.03.f' },
  { id: 'park-factors',  label: 'HR park factors',       code: 'M.03.g' },
  { id: 'lineups',       label: 'Lineups',               code: 'M.03.h' },
  { id: 'edge',          label: 'Edge',                  code: 'M.03.i' },
  { id: 'contact-score', label: 'Contact score',         code: 'M.03.j' },
  { id: 'combined-sort', label: 'Default sort & POD',    code: 'M.03.k' },
  { id: 'hrr-pod',       label: 'H+R+RBI POD',           code: 'M.03.l' },
  { id: 'pitch-types',   label: 'Pitch-type breakdown',  code: 'M.03.m' },
  { id: 'principles',    label: 'What we don\u2019t do', code: 'M.03.n' },
  { id: 'freshness',     label: 'Updates & freshness',   code: 'M.03.o' },
]

const activeSection = ref('overview')

// Lookup helper so the inline M.03.x codes in each section header are
// driven by the sections array (single source of truth). Without this,
// the codes are duplicated between the array and the template's
// <span class="label-bracket"> tags — which is what caused the
// combined-heat / statcast code collision originally (both said M.03.d).
function codeFor(id) {
  const s = sections.find(x => x.id === id)
  return s ? s.code : ''
}

// IntersectionObserver wires the side nav to scroll position so the active
// pill follows you as you read.
let observer = null
onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      // Find the entry closest to the top of viewport that's intersecting
      const visible = entries
        .filter(e => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible.length) {
        activeSection.value = visible[0].target.id
      }
    },
    {
      // Trigger when section top crosses 25% into viewport — feels natural
      rootMargin: '-15% 0px -65% 0px',
      threshold: 0,
    }
  )

  nextTick(() => {
    for (const s of sections) {
      const el = document.getElementById(s.id)
      if (el) observer.observe(el)
    }
  })
})
onUnmounted(() => {
  if (observer) observer.disconnect()
})

function scrollTo(id) {
  const el = document.getElementById(id)
  if (!el) return
  // Account for sticky header height (~64-80px) with a manual offset
  const top = el.getBoundingClientRect().top + window.scrollY - 90
  window.scrollTo({ top, behavior: 'smooth' })
  activeSection.value = id
}
</script>

<template>
  <div class="min-h-screen">

    <!-- ── Header strip ─────────────────────────────────────── -->
    <header class="px-4 sm:px-6 pt-6 sm:pt-8 pb-5 border-b border-bg-200">
      <div class="flex items-center gap-3 mb-2">
        <span class="label-bracket text-signal-400">methodology · M.03</span>
      </div>
      <h1 class="display-text text-2xl sm:text-3xl text-fg-800 tracking-tight leading-none">
        How we compute every number on this site
      </h1>
      <p class="text-fg-500 text-sm mt-3 max-w-2xl leading-relaxed">
        Cebolla Lab exists to surface real edge in MLB betting markets.
        This page documents how every stat, color, and projection is built —
        so you can trust what you're looking at.
      </p>
    </header>

    <!-- ── Body: 2-column on desktop, single column on mobile ── -->
    <div class="px-4 sm:px-6 py-6 grid grid-cols-1 lg:grid-cols-[180px_1fr] gap-6 lg:gap-10 max-w-5xl mx-auto">

      <!-- ── Side nav (sticky, desktop only) ─────────────────── -->
      <aside class="hidden lg:block">
        <div class="sticky top-24 space-y-0.5">
          <div class="label-caps !text-[8px] mb-3 text-fg-500">contents</div>
          <button
            v-for="s in sections"
            :key="s.id"
            @click="scrollTo(s.id)"
            type="button"
            class="block w-full text-left px-2 py-1.5 text-[12px] transition border-l-2"
            :class="activeSection === s.id
              ? 'border-signal-400 text-signal-200 bg-signal-400/5'
              : 'border-transparent text-fg-500 hover:text-fg-700 hover:border-bg-300'"
          >
            {{ s.label }}
          </button>
        </div>
      </aside>

      <!-- ── Mobile: section jump (horizontal scroll pills) ─── -->
      <nav class="lg:hidden -mx-4 px-4 overflow-x-auto h-scroll">
        <div class="flex gap-1.5 pb-3">
          <button
            v-for="s in sections"
            :key="s.id"
            @click="scrollTo(s.id)"
            type="button"
            class="shrink-0 px-3 py-1.5 text-[11px] border transition whitespace-nowrap min-h-[36px] inline-flex items-center"
            :class="activeSection === s.id
              ? 'border-signal-400 text-signal-200 bg-signal-400/10'
              : 'border-bg-200 text-fg-500'"
          >
            {{ s.label }}
          </button>
        </div>
      </nav>

      <!-- ── Content ─────────────────────────────────────────── -->
      <article class="space-y-10 max-w-2xl">

        <!-- 1. OVERVIEW -->
        <section id="overview" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">What this is</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('overview') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Cebolla Lab is a personal research tool for MLB betting. It pulls raw
            pitch-level data and traditional pitching totals from authoritative
            public sources, computes everything fresh, and presents the numbers
            in a layout designed for one thing: building a card before the
            slate locks.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed">
            Every stat shown on this site is computed by us. Nothing is scraped
            from another betting site, repackaged, or relabeled. If you see a
            number, we did the math.
          </p>
        </section>

        <!-- 2. DATA PHILOSOPHY -->
        <section id="data" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Data philosophy</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('data') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            We use four kinds of data, all public:
          </p>
          <ul class="text-fg-600 text-sm leading-relaxed space-y-2 list-none pl-0">
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="label-caps text-signal-200">pitch-level events</span>
              — every individual pitch thrown in MLB games, with launch speed,
              launch angle, pitch type, location, and outcome. This is the raw
              material for every Statcast metric on the site.
            </li>
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="label-caps text-signal-200">traditional pitching totals</span>
              — season-level ERA, FIP, WHIP, K/9, walk rate. Used to anchor
              pitcher profiles alongside the Statcast view.
            </li>
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="label-caps text-signal-200">venue and weather</span>
              — stadium IDs, dome status, game-time wind, temperature, and
              precipitation. Feeds park-factor and conditions overlays.
            </li>
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="label-caps text-signal-200">posted odds</span>
              — current player props and game lines from a major sportsbook,
              refreshed throughout the day. Used solely to compute edge vs.
              our projections.
            </li>
          </ul>
          <p class="text-fg-500 text-xs italic mt-4">
            We don't pull from other handicapping or "rating" sites. The whole
            point is to compute our own grade, not relay someone else's.
          </p>
        </section>

        <!-- 3. ROLLING WINDOWS -->
        <section id="windows" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Rolling windows</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('windows') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Every player has four versions of their stats on the site:
          </p>
          <div class="grid grid-cols-2 gap-2 mb-4">
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps text-signal-400">Season</div>
              <div class="text-fg-600 text-xs mt-1 leading-snug">
                Full year. Largest sample, slowest to react.
              </div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps text-signal-400">L30</div>
              <div class="text-fg-600 text-xs mt-1 leading-snug">
                Last 30 days. Catches multi-week trends.
              </div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps text-signal-400">L14</div>
              <div class="text-fg-600 text-xs mt-1 leading-snug">
                Last 14 days. The sweet-spot window — recent enough to reflect form, big enough to mean something.
              </div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps text-signal-400">L7</div>
              <div class="text-fg-600 text-xs mt-1 leading-snug">
                Last 7 days. Hottest signal, smallest sample. Use with care.
              </div>
            </div>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed">
            For any rolling window we require a minimum sample (at least 5 plate
            appearances) before we'll report a row. Below that, the numbers are
            noise, not signal — we'd rather show nothing than mislead.
          </p>
        </section>

        <!-- 3.5 COMBINED HEAT (the flagship trend metric) -->
        <section id="combined-heat" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Combined Heat</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('combined-heat') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Combined Heat is Cebolla's multi-signal trend score. It answers
            a question single-metric trends can't: <em>is this hitter trending
            up across multiple independent signals at once, or is it just one
            lucky stat carrying the read?</em>
          </p>

          <div class="bg-bg-50 border border-bg-200 px-4 py-3 mb-3">
            <div class="label-caps text-signal-400 mb-2">The math</div>
            <ol class="text-fg-600 text-sm leading-relaxed list-decimal pl-5 space-y-1">
              <li>
                For each base metric — <span class="font-mono text-fg-700">HR/PA</span>,
                <span class="font-mono text-fg-700">H/PA</span>,
                <span class="font-mono text-fg-700">Barrel%</span>,
                <span class="font-mono text-fg-700">ISO</span> —
                compute the trend score:
                <span class="font-mono text-fg-700">(L14 − season) / season</span>
              </li>
              <li>
                Clamp each trend to <span class="font-mono text-fg-700">[−75%, +200%]</span>
                so a single outlier metric can't dominate.
              </li>
              <li>
                Take the geometric mean of <span class="font-mono text-fg-700">(1 + clamped)</span>
                across all valid metrics, then subtract 1.
              </li>
              <li>
                Require at least 3 of 4 base metrics to produce a result.
                Sparse data → no Combined score, rather than a misleading one.
              </li>
            </ol>
          </div>

          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            <strong class="text-fg-700">Why geometric mean and not arithmetic.</strong>
            Arithmetic mean rewards spikes — a player at +200% on HR and 0% on
            everything else would look just as hot as one at +50% across all
            four. Geometric mean punishes disagreement: the +200% / 0% / 0% / 0%
            player lands around +30% combined, while the +50% / +50% / +50% / +50%
            player lands at exactly +50%. Multi-signal agreement wins.
          </p>

          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            <strong class="text-fg-700">Why the clamps.</strong>
            A player with a tiny sample can post a +500% trend on a single
            metric — usually noise, not signal. Capping per-metric inputs at
            +200% before the geometric mean keeps one outlier from skewing
            the combined read. The −75% floor protects against multiplying
            by near-zero (a player at −100% on one metric would otherwise
            zero out the entire combined score).
          </p>

          <div class="bg-bg-50 border border-bg-200 px-4 py-3 mb-3">
            <div class="label-caps text-signal-400 mb-2">Tier thresholds</div>
            <p class="text-fg-600 text-xs leading-relaxed mb-2">
              Combined Heat uses the same tier system as individual trend
              metrics, so a +50% combined hits the same BLAZING threshold as
              a +50% on HR/PA — but it means something far stronger because
              multiple signals agree.
            </p>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-1.5 text-[10px] font-mono">
              <div class="px-2 py-1 border border-signal-400/60 text-signal-300">≥ +50% BLAZING</div>
              <div class="px-2 py-1 border border-signal-400/40 text-signal-300">+25 to +50 HOT</div>
              <div class="px-2 py-1 border border-amber-400/40 text-amber-300">+10 to +25 WARM</div>
              <div class="px-2 py-1 border border-bg-300 text-fg-500">±10% FLAT</div>
              <div class="px-2 py-1 border border-blue-400/30 text-blue-300">−10 to −25 COOL</div>
              <div class="px-2 py-1 border border-blue-400/45 text-blue-300">−25 to −50 COLD</div>
              <div class="px-2 py-1 border border-blue-400/60 text-blue-300">≤ −50% FROZEN</div>
              <div class="px-2 py-1 border border-bg-300 text-fg-500 italic">NULL = no data</div>
            </div>
          </div>

          <p class="text-fg-600 text-sm leading-relaxed">
            <strong class="text-fg-700">Why this matters for bet selection.</strong>
            A single hot metric can be sample noise — a player goes
            3-for-7 on barrel% over 14 days and looks like a power surge that
            was really 4 lucky swings. Combined Heat is harder to fake:
            the noise on each metric is roughly independent, so seeing
            agreement across four metrics is meaningful even when any single
            metric on its own would be uncertain.
          </p>
        </section>

        <!-- 4. STATCAST STATS -->
        <section id="statcast" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Statcast stats</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('statcast') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-4">
            Every Statcast number on the site is recomputed from raw pitch-level
            data. We don't store cached rates from external feeds. The thresholds
            below are the bands we use to color stats from elite (red) to poor
            (blue).
          </p>

          <div class="space-y-4">
            <!-- Barrel% -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">Barrel %</span>
                <span class="label-caps !text-[8px]">elite ≥ 14% · poor ≤ 4%</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                The percentage of a batter's batted balls that meet MLB's
                official barrel definition — the perfect combination of exit
                velocity and launch angle that almost always results in
                extra-base contact.
              </p>
              <details class="mt-2 text-fg-500 text-xs">
                <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
                <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px]">
                  Barrel% = (barreled batted balls / total batted balls) × 100
                  <br><br>
                  A batted ball is barreled when launch speed and launch angle
                  fall within MLB's defined zone — roughly 98+ mph at 26-30°,
                  with the qualifying angle band widening as exit velocity climbs.
                </div>
              </details>
            </div>

            <!-- Hard-Hit% -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">Hard-Hit %</span>
                <span class="label-caps !text-[8px]">elite ≥ 50% · poor ≤ 32%</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                The percentage of batted balls hit at 95 mph or harder.
                A simpler, more stable companion to barrel rate — it tells
                you who's squaring the ball up consistently, even when
                they're not finding the perfect launch angle.
              </p>
              <details class="mt-2 text-fg-500 text-xs">
                <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
                <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px]">
                  Hard-Hit% = (batted balls with exit velocity ≥ 95 mph / total batted balls) × 100
                </div>
              </details>
            </div>

            <!-- xBA -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">xBA</span>
                <span class="label-caps !text-[8px]">elite ≥ .290 · poor ≤ .220</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                Expected batting average. Strips luck out of the equation by
                asking: given how hard and at what angle the batter is hitting
                the ball, what should their average be?
              </p>
              <details class="mt-2 text-fg-500 text-xs">
                <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
                <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px]">
                  Average of the expected-BA value assigned to each batted
                  ball based on its exit velocity and launch angle. Strikeouts
                  count as zero. Walks are excluded.
                </div>
              </details>
            </div>

            <!-- xSLG -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">xSLG</span>
                <span class="label-caps !text-[8px]">elite ≥ .500 · poor ≤ .360</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                Expected slugging. Same approach as xBA, but weighted by total
                bases. This is the headline xStat for HR projections — high
                xSLG with low actual SLG often means a hitter is about to break
                out.
              </p>
            </div>

            <!-- xwOBA -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">xwOBA</span>
                <span class="label-caps !text-[8px]">composite</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                Expected weighted on-base average. The single best
                catch-all hitter metric. Combines contact quality
                (xBA + xSLG components) with walks. If you can only look at
                one number to compare two hitters, look at this one.
              </p>
            </div>

            <!-- Sweet Spot% -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">Sweet Spot %</span>
                <span class="label-caps !text-[8px]">elite ≥ 38%</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                The percentage of batted balls hit at the ideal launch angle
                range (8° to 32°). Sweet-spot contact is where extra-base hits
                and home runs live. Hard contact on the ground is wasted; this
                metric isolates productive launch angles.
              </p>
            </div>

            <!-- Exit Velocity -->
            <div class="bg-bg-50 border border-bg-200 px-4 py-3">
              <div class="flex items-baseline justify-between mb-1.5">
                <span class="label-bracket text-signal-400">Exit Velocity</span>
                <span class="label-caps !text-[8px]">avg + max</span>
              </div>
              <p class="text-fg-600 text-sm leading-relaxed">
                Raw speed of the ball off the bat in mph. We show both the
                average across a window and the max — a high max with a modest
                average flags a hitter with elite top-end power who isn't
                consistently squaring it up.
              </p>
            </div>
          </div>
        </section>

        <!-- 5. PITCHER-ALLOWED -->
        <section id="pitcher-side" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Pitcher-allowed stats</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('pitcher-side') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            For pitchers, we compute the same Statcast metrics — but
            from the contact they <em>allow</em>. A pitcher's barrel%
            allowed is the percentage of opposing batted balls that
            meet the barrel definition.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            The interpretation flips. For batters, high barrel% is elite.
            For pitchers, <span class="text-signal-400">low</span> barrel%
            allowed is elite — they're suppressing hard contact. The site's
            color scale flips accordingly: on the pitcher view, red means the
            pitcher is shutting hitters down, blue means he's getting hit hard.
          </p>
          <div class="bg-bg-50 border border-bg-200 px-4 py-3 text-fg-600 text-xs leading-relaxed">
            <span class="label-caps text-signal-400 mb-1.5 block">elite pitcher bands</span>
            Barrel% allowed ≤ 5% · Hard-Hit% allowed ≤ 34% · xBA allowed ≤ .220 · xSLG allowed ≤ .350
          </div>
        </section>

        <!-- 6. PARK FACTORS -->
        <section id="park-factors" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">HR park factors</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('park-factors') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Some stadiums boost home runs, others suppress them. We track
            three park factors per stadium — overall, and split by left-handed
            and right-handed batter perspective — because Coors plays
            differently for a lefty pulling to right field than a righty
            pulling to left.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Values are indexed where 1.00 means league-average. A factor of 1.15
            means roughly 15% more home runs than league average for that batter
            handedness at that venue, accounting for stadium dimensions and
            elevation.
          </p>
          <div class="grid grid-cols-3 gap-2">
            <div class="bg-bg-50 border border-signal-400/30 px-3 py-2 text-center">
              <div class="label-caps text-signal-400">Hot park</div>
              <div class="display-num text-xs text-fg-600 mt-1">≥ 1.05</div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2 text-center">
              <div class="label-caps">Neutral</div>
              <div class="display-num text-xs text-fg-600 mt-1">0.95 – 1.05</div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2 text-center">
              <div class="label-caps">Cold park</div>
              <div class="display-num text-xs text-fg-600 mt-1">≤ 0.95</div>
            </div>
          </div>
        </section>

        <!-- 7. LINEUPS -->
        <section id="lineups" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Lineups</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('lineups') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Lineups come in three flavors on the site, color-coded:
          </p>
          <div class="space-y-2 mb-3">
            <div class="flex items-baseline gap-2">
              <span class="!text-[8px] px-1.5 py-0.5 rounded-sm text-signal-400 bg-signal-400/10 label-caps">confirmed</span>
              <span class="text-fg-600 text-sm leading-relaxed">Official starting lineup posted by the team.</span>
            </div>
            <div class="flex items-baseline gap-2">
              <span class="!text-[8px] px-1.5 py-0.5 rounded-sm text-amber-300 bg-amber-500/10 label-caps">projected · last lineup</span>
              <span class="text-fg-600 text-sm leading-relaxed">Today's lineup isn't out yet. We're showing the team's last known starting nine as a placeholder.</span>
            </div>
            <div class="flex items-baseline gap-2">
              <span class="!text-[8px] px-1.5 py-0.5 rounded-sm text-fg-500 bg-bg-200/60 label-caps">projected</span>
              <span class="text-fg-600 text-sm leading-relaxed">A generic projection when we don't have a recent confirmed lineup to fall back on.</span>
            </div>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed">
            Use confirmed lineups when they're available. The fallback exists
            so you can start building your card earlier in the day, but always
            re-check before placing a bet.
          </p>
        </section>

        <!-- 8. EDGE -->
        <section id="edge" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Edge</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('edge') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Edge is the headline number on the HR Report. It compares
            our projected HR probability for a batter against the implied
            probability of his posted HR odds. A positive edge means the
            market is offering more value than our model thinks the bet
            is worth.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            The projection combines a hitter's rolling-window contact
            quality (Brl%, HH%, xSLG) with the opposing pitcher's allowed
            contact profile, then adjusts for handedness, park, and weather.
            The market price is converted to an implied probability the
            standard way.
          </p>
          <details class="mt-2 text-fg-500 text-xs">
            <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
            <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px] leading-relaxed">
              edge = projected_hr_prob − implied_market_prob
              <br><br>
              projected_hr_prob = base_rate
              <br>&nbsp;&nbsp;× pitcher_adj
              <br>&nbsp;&nbsp;× park_adj_by_hand
              <br>&nbsp;&nbsp;× weather_adj
              <br>&nbsp;&nbsp;× recent_form_factor
              <br><br>
              implied_market_prob = 1 / decimal_odds
              <br>(after de-vigging the book's posted lines)
            </div>
          </details>
          <p class="text-fg-500 text-xs italic mt-4">
            Edge is a number, not a guarantee. It tells you the market is
            offering value relative to our model. Discipline still matters.
          </p>
        </section>

        <!-- 9. CONTACT SCORE -->
        <section id="contact-score" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Contact score</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('contact-score') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            The Contact column on the HR Report is a single 0–100 number that
            captures how good a batter's recent contact quality is, in
            absolute terms, compared to all qualified MLB batters this season.
            A 90 means elite (top 10%). A 30 means weak. The math is honest:
            we don't grade on a curve, we don't reward streakiness without
            substance.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            It blends three Statcast metrics by percentile rank, weighted by
            how predictive each is for HR outcomes: <span class="text-fg-700">Barrel%</span>
            (40%), <span class="text-fg-700">Hard-Hit%</span> (30%), and
            <span class="text-fg-700">xSLG</span> (30%). The L14 score uses
            the last 14 days of plate appearances; the trend arrow (▲/▼)
            compares L14 to the batter's full-season score, flagging hot and
            cold streaks.
          </p>
          <details class="mt-2 text-fg-500 text-xs">
            <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
            <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px] leading-relaxed">
              pool = all MLB batters this season
              <br>&nbsp;&nbsp;&nbsp;with L14 PA &gt;= 20, vs_hand = 'A'
              <br><br>
              brl_pct  = percentile_rank(batter.barrel_pct,   pool)
              <br>hh_pct   = percentile_rank(batter.hard_hit_pct, pool)
              <br>xslg_pct = percentile_rank(batter.xslg,         pool)
              <br><br>
              contact_score = 0.40 · brl_pct
              <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ 0.30 · hh_pct
              <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ 0.30 · xslg_pct
              <br><br>
              trend = score(L14) − score(season)
              <br>(arrow shown only when |trend| &gt;= 5)
            </div>
          </details>
          <p class="text-fg-600 text-sm leading-relaxed mt-4 mb-2">
            <span class="text-fg-700">Tier breakdown:</span>
          </p>
          <ul class="text-fg-600 text-sm leading-relaxed space-y-1 list-none pl-0">
            <li class="border-l-2 border-signal-400/60 pl-3"><span class="text-signal-400 font-medium">90+</span> &nbsp; Elite — top 10% of MLB</li>
            <li class="border-l-2 border-signal-400/30 pl-3"><span class="text-signal-200">75–89</span> &nbsp; Strong</li>
            <li class="border-l-2 border-bg-300 pl-3"><span class="text-fg-700">50–74</span> &nbsp; Average to above average</li>
            <li class="border-l-2 border-edge-cold-2/40 pl-3"><span class="text-edge-cold-2">30–49</span> &nbsp; Below average</li>
            <li class="border-l-2 border-edge-cold-1/40 pl-3"><span class="text-edge-cold-1">&lt; 30</span> &nbsp; Poor contact this stretch</li>
          </ul>
          <p class="text-fg-500 text-xs italic mt-4">
            Minimum 20 L14 PA to qualify. Under that, rate stats are random
            noise. The score is L14-anchored — toggling the Statcast window
            to L7/L30/Season shows "L14 only" since the trend logic depends
            on the L14 sample.
          </p>
        </section>

        <!-- 10. COMBINED SORT / POD -->
        <section id="combined-sort" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Default sort & POD</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('combined-sort') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            By default, the HR Report ranks batters by a multiplicative blend
            of <span class="text-signal-400">Edge × Contact</span>. The idea
            is to surface bets where BOTH signals agree: strong market value
            AND strong recent contact. A batter with +8% edge but a 20 contact
            score isn't a great pick — either the market knows something we
            don't, or the contact is too weak to expect the projection to
            hold up. A batter with +4% edge and 80 contact is the more
            durable play.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Both factors are normalized to a 0–100 scale before multiplying so
            neither one dominates. Edge is clamped at ±10% (anything more
            extreme rounds to the edge of the scale).
          </p>
          <details class="mt-2 text-fg-500 text-xs">
            <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
            <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px] leading-relaxed">
              edge_norm    = clamp(edge·100, −10, +10)
              <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;mapped to 0–100
              <br>contact_norm = clamp(contact_score, 0, 100)
              <br><br>
              combined = edge_norm × contact_norm
              <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;(range 0 – 10,000)
              <br><br>
              missing edge or contact → neutral 50
            </div>
          </details>
          <p class="text-fg-600 text-sm leading-relaxed mt-4 mb-3">
            <span class="text-fg-700">Play of the Day (POD):</span>
            The daily public-scoreboard pick is the highest-combined-score HR
            prop in the slate, gated by <span class="text-fg-700">projected_prob ≥ 20%</span>
            so we don't pick wild longshots. The pick locks at ~10:30 AM ET
            after the morning projections run, and settles automatically
            after games end. The combined score AND contact score at lock
            time are snapshot into the POD record, so the public scoreboard
            shows why each pick was made.
          </p>
          <p class="text-fg-500 text-xs italic mt-4">
            You can override the default sort by clicking any column header.
            The default returns when you click the # column (lineup order
            reset) or reload the page.
          </p>
        </section>

        <!-- 10b. HRR POD (SHIPPED) -->
        <section id="hrr-pod" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">H + R + RBI POD</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('hrr-pod') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Alongside the HR POD, Cebolla picks a second daily play for the
            <span class="text-fg-700">Hits + Runs + RBIs</span> market — a DraftKings
            prop where the line is posted at 1.5, 2.5, or 3.5 for each batter.
            Same daily lock window (~10:30 AM ET), same combined edge × contact ranking,
            just a different stat to clear.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            <span class="text-fg-700">Why a separate POD?</span> The H+R+RBI market
            and the HR market measure different things. A leadoff contact hitter who rarely
            homers can still produce 2+ hits and a run regularly. An elite power hitter who
            walks a lot might struggle to clear an HRR line. Picking one POD per market
            captures both kinds of value without forcing them into the same ranking.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            <span class="text-fg-700">Projection approach (v0.3).</span> For each batter,
            expected plate appearances are derived from lineup position (leadoff ~4.6 PA,
            #9 ~3.6 PA). Per-PA event rates for hits, runs, and RBIs come from a
            shrinkage estimator that blends the batter's L14 rate with the league average,
            weighted by sample size — this prevents small-sample noise from inflating
            projections on hot hitters with few recent PAs.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            The three per-PA rates are summed (with an overlap correction since a HR
            counts as 1H + 1R + 1RBI from one event) to get a single λ representing
            expected HRR events per PA. Multiplied by expected PAs, that gives λ for
            the full game. A Poisson tail probability over λ produces the line-clearing
            chance for each of 1.5 / 2.5 / 3.5.
          </p>
          <details class="mt-2 text-fg-500 text-xs">
            <summary class="cursor-pointer hover:text-fg-700 transition">show formula</summary>
            <div class="mt-2 pl-3 border-l border-bg-300 font-mono text-[11px] leading-relaxed">
              E[PA]&nbsp;=&nbsp;lookup_by_batting_order(order)
              <br><br>
              # Shrinkage estimator (k = prior strength, 150 PA)
              <br>shrink(batter_rate, league_rate, batter_PA, k)
              <br>&nbsp;&nbsp;=&nbsp;(batter_rate · batter_PA + league_rate · k) / (batter_PA + k)
              <br><br>
              p(H per PA)&nbsp;&nbsp;&nbsp;=&nbsp;shrink(batter.hits/PA,&nbsp;league_H/PA,&nbsp;PA,&nbsp;k=150)
              <br>p(R per PA)&nbsp;&nbsp;&nbsp;=&nbsp;shrink(batter.runs/PA,&nbsp;league_R/PA,&nbsp;PA,&nbsp;k=150)
              <br>p(RBI per PA)&nbsp;=&nbsp;shrink(batter.rbi/PA,&nbsp;league_RBI/PA,&nbsp;PA,&nbsp;k=150)
              <br><br>
              λ(per PA)&nbsp;=&nbsp;p(H) + p(R) + p(RBI) − HR_overlap
              <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;(HR_overlap ≈ 0.06 — one HR contributes to all 3 stats)
              <br>λ(per PA)&nbsp;=&nbsp;clamp(λ, 0, 0.75)
              <br><br>
              λ(per game)&nbsp;=&nbsp;λ(per PA)&nbsp;·&nbsp;E[PA]
              <br>P(HRR ≥ X+1)&nbsp;=&nbsp;Poisson_tail(X+1, λ_game)
            </div>
          </details>
          <p class="text-fg-600 text-sm leading-relaxed mt-4 mb-3">
            <span class="text-fg-700">Line selection.</span> For each batter, the picker
            evaluates all three lines (1.5 / 2.5 / 3.5) and chooses the one with the
            highest edge above its probability floor. Floors are calibrated to the
            actual distribution of projections per line — roughly 40% for the 1.5 line,
            20% for 2.5, and 7% for 3.5. The batter with the highest combined-score
            (edge × L14 contact percentile) becomes that day's HRR POD.
          </p>
          <p class="text-fg-500 text-xs italic mt-4">
            The v0.3 Poisson model assumes independence between PAs and uses each batter's
            own shrunk L14 rates without adjusting for opposing pitcher or surrounding
            lineup quality. Expected calibration: ±5 percentage points on the most common
            (1.5) line. Once the model has ~30 settled picks, back-test data will
            inform tuning.
          </p>
        </section>

        <!-- 9. PITCH-TYPE BREAKDOWN -->
        <section id="pitch-types" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Pitch-type breakdown</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('pitch-types') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            On Player Deep Dive, the pitch-type table shows how a hitter
            performs against each pitch he sees. You can toggle the window
            — Season, L30, L14, L7 — to see how his pitch-specific contact
            is trending.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            If your chosen window has too few pitches to be meaningful, we
            don't show garbage. We fall back automatically to the next-larger
            window that has data. The page will tell you when this is happening.
          </p>
          <p class="text-fg-600 text-sm leading-relaxed">
            Fallback order: L7 → L14 → L30 → Season. Minimum 5 plate appearances
            per pitch type to qualify.
          </p>
        </section>

        <!-- 10. PRINCIPLES -->
        <section id="principles" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">What we don't do</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('principles') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            A short list of what's off the table, by design:
          </p>
          <ul class="text-fg-600 text-sm leading-relaxed space-y-2 list-none pl-0">
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="text-fg-700">No moneyline parlays.</span>
              Stringing favorites together is how books print money. We
              don't surface that combination as a recommended structure.
            </li>
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="text-fg-700">No same-game parlays.</span>
              SGP correlation models are deliberately mispriced in the
              book's favor. We pass.
            </li>
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="text-fg-700">No scraping competitor ratings.</span>
              Every grade on this site is computed from raw inputs. We're
              not relaying other people's homework.
            </li>
            <li class="border-l-2 border-signal-400/40 pl-3">
              <span class="text-fg-700">No fake confidence.</span>
              If a sample is too small, we say so. If a lineup is projected,
              we tag it. The site is honest about what it knows and
              doesn't know.
            </li>
          </ul>
        </section>

        <!-- 11. FRESHNESS -->
        <section id="freshness" class="scroll-mt-24">
          <div class="flex items-baseline gap-3 mb-3">
            <h2 class="display-text text-xl text-fg-800">Updates & freshness</h2>
            <span class="label-bracket !text-[8px] text-fg-500">{{ codeFor('freshness') }}</span>
          </div>
          <p class="text-fg-600 text-sm leading-relaxed mb-3">
            Different data refreshes on different schedules:
          </p>
          <div class="space-y-2">
            <div class="flex items-baseline gap-3">
              <span class="label-caps text-signal-400 w-28 shrink-0">Statcast</span>
              <span class="text-fg-600 text-sm">Daily deep recompute every morning across all rolling windows.</span>
            </div>
            <div class="flex items-baseline gap-3">
              <span class="label-caps text-signal-400 w-28 shrink-0">Schedule</span>
              <span class="text-fg-600 text-sm">Twice daily — morning sync plus mid-day refresh.</span>
            </div>
            <div class="flex items-baseline gap-3">
              <span class="label-caps text-signal-400 w-28 shrink-0">Lineups</span>
              <span class="text-fg-600 text-sm">Pulled at morning lock; refreshed throughout the day as teams post confirmed lineups.</span>
            </div>
            <div class="flex items-baseline gap-3">
              <span class="label-caps text-signal-400 w-28 shrink-0">Odds</span>
              <span class="text-fg-600 text-sm">Captured at morning lock, then refreshed hourly through the last game of the day.</span>
            </div>
            <div class="flex items-baseline gap-3">
              <span class="label-caps text-signal-400 w-28 shrink-0">Live scores</span>
              <span class="text-fg-600 text-sm">Pulled every 5 minutes during peak game hours, every 15 minutes off-peak; pushed to the UI in real-time.</span>
            </div>
            <div class="flex items-baseline gap-3">
              <span class="label-caps text-signal-400 w-28 shrink-0">Weather</span>
              <span class="text-fg-600 text-sm">Refreshed alongside the schedule and again pre-first-pitch.</span>
            </div>
          </div>
          <p class="text-fg-500 text-xs italic mt-4">
            The pulsing onion in the top nav indicates a live data event. When
            it bursts, something just changed — a score, a lineup, an odd.
          </p>
        </section>

        <!-- Closing -->
        <section class="pt-6 pb-12 border-t border-bg-200 mt-12">
          <p class="text-fg-500 text-xs italic">
            Every layer reveals an edge. If you've made it this far, you
            already know what to do.
          </p>
        </section>

      </article>
    </div>
  </div>
</template>

<style scoped>
/* scroll-mt-24 is a Tailwind utility — included via main.css already.
   This file uses it for jump-link offset under the sticky nav. */
</style>
