<script setup>
/**
 * DisclaimerView.vue — full legal disclaimer page.
 *
 * Real source of truth for Cebolla's "we are research not advice"
 * positioning. Mirrors legal/disclaimer.txt long form. All headings
 * + numbering match the master file so legal counsel can reference
 * sections without ambiguity.
 */
import { computed } from 'vue'

// Display "Last updated" as the build date of this component.
// In production replace with a hardcoded effective date when published.
const lastUpdated = computed(() => {
  const d = new Date()
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
})
</script>

<template>
  <article class="max-w-3xl mx-auto px-6 sm:px-8 py-10 sm:py-14">
    <header class="mb-10">
      <div class="label-bracket text-signal-400 text-xs mb-3">[ M.07.disclaimer ]</div>
      <h1 class="font-display text-3xl sm:text-4xl text-fg-50 tracking-tight">Disclaimer</h1>
      <p class="mt-3 text-fg-500 text-sm">Last updated: {{ lastUpdated }}</p>
    </header>

    <section class="space-y-6 text-fg-300 leading-relaxed text-[15px]">
      <p class="text-fg-200 border-l-2 border-signal-400 pl-4 italic">
        Cebolla ("we", "our", "the Platform") is a sports analytics research platform that
        provides statistical projections, historical matchup data, weather information, lineup
        information, and AI-generated parlay analysis.
        <strong class="text-fg-100 not-italic">ALL CONTENT IS FOR INFORMATIONAL AND
        ENTERTAINMENT PURPOSES ONLY.</strong>
      </p>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">1. Not Financial or Betting Advice</h2>
        <p>
          Nothing on this Platform constitutes financial, investment, legal, or betting advice.
          We do not provide recommendations to place specific wagers. Users are solely responsible
          for any wagering decisions they make.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">2. No Guarantees</h2>
        <p>
          Past performance does not guarantee future results. Our statistical projections are
          estimates based on historical data and proprietary models. Actual outcomes may differ
          significantly from projections. We make no representations regarding the accuracy of any
          projection, analysis, or other information.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">3. Sports Betting Is Risky</h2>
        <p>
          Sports betting involves substantial risk of financial loss. Most bettors lose money
          over time. You should only wager money you can afford to lose. We are not responsible
          for any losses incurred by users.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">4. Legal Jurisdiction</h2>
        <p>
          Sports betting is regulated differently in each U.S. state and country. It is the
          user's sole responsibility to determine whether sports betting is legal in their
          jurisdiction. We do not facilitate, accept, or process any wagers.
          <strong class="text-fg-100">We are not a sportsbook.</strong>
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">5. Age Restriction</h2>
        <p>
          Sports betting is restricted to individuals 21+ in most U.S. jurisdictions. Users must
          verify they meet all age and eligibility requirements in their jurisdiction before
          engaging in any wagering activity with third parties.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">6. Problem Gambling</h2>
        <p class="mb-2">If you or someone you know has a gambling problem, please seek help:</p>
        <ul class="ml-4 space-y-1 list-disc">
          <li>National Council on Problem Gambling: <a href="tel:18004262537" class="text-signal-400 hover:text-signal-300 underline">1-800-GAMBLER</a></li>
          <li>Or visit <a href="https://www.ncpgambling.org" target="_blank" rel="noopener noreferrer" class="text-signal-400 hover:text-signal-300 underline">ncpgambling.org</a></li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">7. No Affiliation</h2>
        <p>
          Cebolla is independent and not affiliated with, endorsed by, or sponsored by Major
          League Baseball, MLB Advanced Media, the MLB Players Association, DraftKings, FanDuel,
          or any other sportsbook, sports league, team, player, or governing body.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">8. Data Sources</h2>
        <p>
          We aggregate publicly available sports data including statistical information from
          publicly accessible MLB sources and publicly available sportsbook odds. We make no
          claim of ownership over underlying sports data, official statistics, or odds data.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">9. Intellectual Property</h2>
        <p>
          The Cebolla platform, including its analytics, projections, proprietary models, design,
          and branding, is the intellectual property of Cebolla. Reproduction or redistribution
          of platform content without authorization is prohibited.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">10. Changes to Disclaimer</h2>
        <p>
          We reserve the right to update this disclaimer at any time. Continued use of the
          Platform constitutes acceptance of any updates.
        </p>
      </div>

      <hr class="border-bg-200/30 my-8" />
      <p class="text-fg-500 text-xs">
        Questions? Contact <a href="mailto:support@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">support@cebolla.live</a>
      </p>
    </section>
  </article>
</template>
