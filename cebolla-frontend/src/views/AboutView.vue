<script setup>
/**
 * AboutView.vue — Cebolla's "what we are / what we aren't" page.
 *
 * Real legal positioning page. Establishes Cebolla as a research and
 * analytics platform, NOT a sportsbook or tipster service. The
 * language here mirrors the master legal stack (Terms, Privacy,
 * Disclaimer) and reinforces the "informational and entertainment
 * purposes only" framing required for safe commercialization.
 *
 * Edits to language here should be reflected in:
 *  - legal/about-page.md (source of truth)
 *  - legal/terms-of-service.md (section 2 mirrors this scope)
 *  - legal/disclaimer.txt (statement of independence)
 */
</script>

<template>
  <article class="max-w-3xl mx-auto px-6 sm:px-8 py-10 sm:py-14">
    <header class="mb-10">
      <div class="label-bracket text-signal-400 text-xs mb-3">[ M.06.about ]</div>
      <h1 class="font-display text-3xl sm:text-4xl text-fg-50 tracking-tight">About Cebolla</h1>
      <p class="mt-3 text-fg-500 text-sm leading-relaxed">
        MLB Analytics &amp; Research Platform
      </p>
    </header>

    <section class="space-y-6 text-fg-300 leading-relaxed text-[15px]">
      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">What is Cebolla?</h2>
        <p class="mb-3">
          Cebolla is an <strong class="text-fg-100">MLB analytics and research platform</strong>.
          We aggregate publicly available baseball data, generate statistical projections using
          proprietary models, and present research tools to help users better understand player
          performance, matchup context, and market dynamics.
        </p>
        <p class="mb-3">
          <strong class="text-signal-400">All content provided on the Platform is for
          informational and entertainment purposes only.</strong> Nothing on Cebolla constitutes
          financial advice, investment advice, or specific betting recommendations.
        </p>
        <p>
          <strong class="text-fg-100">We are not a sportsbook.</strong> We do not accept wagers,
          process payments to gambling operators, or provide specific betting advice.
        </p>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">What we provide</h2>
        <ul class="space-y-2 ml-1">
          <li class="flex gap-3"><span class="text-signal-400">→</span><span><strong class="text-fg-100">Statistical projections</strong> for MLB players and games</span></li>
          <li class="flex gap-3"><span class="text-signal-400">→</span><span><strong class="text-fg-100">Historical batter-vs-pitcher matchup data</strong> built from publicly available records</span></li>
          <li class="flex gap-3"><span class="text-signal-400">→</span><span><strong class="text-fg-100">Weather analysis</strong> for outdoor stadiums</span></li>
          <li class="flex gap-3"><span class="text-signal-400">→</span><span><strong class="text-fg-100">Lineup tracking</strong> as projected lineups are released</span></li>
          <li class="flex gap-3"><span class="text-signal-400">→</span><span><strong class="text-fg-100">AI-generated parlay analysis</strong> ("Cebolla Cards") — research showing how mathematically structured parlay combinations score against current market lines</span></li>
          <li class="flex gap-3"><span class="text-signal-400">→</span><span><strong class="text-fg-100">Daily POD (Projection of the Day)</strong> — a single high-confidence player projection identified by our model</span></li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">What we don't provide</h2>
        <ul class="space-y-2 ml-1">
          <li class="flex gap-3"><span class="text-edge-cold-1">✗</span><span>Specific betting advice or "lock picks"</span></li>
          <li class="flex gap-3"><span class="text-edge-cold-1">✗</span><span>Guaranteed wins or hot streaks</span></li>
          <li class="flex gap-3"><span class="text-edge-cold-1">✗</span><span>Real-money wagering</span></li>
          <li class="flex gap-3"><span class="text-edge-cold-1">✗</span><span>Payment processing to sportsbooks</span></li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">How we use our data</h2>
        <p class="mb-3">
          We rely on publicly available data sources including:
        </p>
        <ul class="space-y-1 ml-4 list-disc">
          <li>MLB's public Stats API for game and player statistics</li>
          <li>Public weather data</li>
          <li>Publicly displayed sportsbook odds</li>
        </ul>
        <p class="mt-3">
          We are an <strong class="text-fg-100">independent research platform</strong> and have no
          affiliation, endorsement, or sponsorship from MLB, MLB Advanced Media, the MLB Players
          Association, DraftKings, FanDuel, or any other sportsbook, team, or league.
        </p>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">Methodology</h2>
        <p class="mb-3">Our projections combine:</p>
        <ul class="space-y-1 ml-4 list-disc">
          <li>Recent player performance (rolling windows of last 15, 50, and 100 games)</li>
          <li>Statcast-derived metrics (barrel%, hard-hit%, xSLG)</li>
          <li>Park factors and environmental conditions</li>
          <li>Matchup data (batter-vs-pitcher history, platoon splits)</li>
          <li>Pitcher arsenal analysis (pitch type, velocity, location)</li>
        </ul>
        <p class="mt-3">
          We weight these factors using statistical models. Projections are
          <strong class="text-fg-100">estimates, not predictions</strong>. Past performance does
          not guarantee future results.
        </p>
        <p class="mt-3">
          For complete methodology details, see our
          <router-link to="/methodology" class="text-signal-400 hover:text-signal-300 underline">methodology page</router-link>.
        </p>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">For research, not for advice</h2>
        <p class="mb-3">
          Sports betting involves substantial risk. Most bettors lose money over time.
        </p>
        <p class="mb-3">
          If you choose to use information from Cebolla in your own analysis, you do so at your
          own risk. We do not recommend specific wagers and make no guarantees about any outcome.
        </p>
        <p class="text-fg-500 text-sm">
          If you or someone you know has a gambling problem:
          <strong class="text-fg-300">National Council on Problem Gambling:</strong>
          <a href="tel:18004262537" class="text-signal-400 hover:text-signal-300 underline">1-800-GAMBLER</a>
        </p>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">Eligibility</h2>
        <p>
          Cebolla is intended for users <strong class="text-fg-100">18 and older</strong>.
          Users engaging in any real-money wagering with third-party sportsbooks must be
          <strong class="text-fg-100">21+ in most U.S. jurisdictions</strong> and must verify
          that wagering is legal in their location.
        </p>
      </div>

      <div>
        <h2 class="font-display text-xl text-fg-100 mb-3">Contact</h2>
        <ul class="space-y-1 ml-1">
          <li>General: <a href="mailto:support@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">support@cebolla.live</a></li>
          <li>Privacy: <a href="mailto:privacy@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">privacy@cebolla.live</a></li>
        </ul>
      </div>

      <hr class="border-bg-200/30 my-8" />
      <p class="text-fg-500 text-xs italic">
        Cebolla — MLB Analytics &amp; Research Platform · cebolla.live
      </p>
    </section>
  </article>
</template>
