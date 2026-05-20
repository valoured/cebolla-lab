<script setup>
/**
 * TermsView.vue — Cebolla Terms of Service.
 *
 * MIRRORS legal/terms-of-service.md. This template is the rendered
 * version of that source file. Any updates to language MUST be made
 * in both places (or this view rewritten to fetch the markdown at
 * build time).
 *
 * NOTE: This is a draft template that should be reviewed by an
 * attorney before publishing/commercializing.
 */
import { computed } from 'vue'

const lastUpdated = computed(() => {
  const d = new Date()
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
})
</script>

<template>
  <article class="max-w-3xl mx-auto px-6 sm:px-8 py-10 sm:py-14">
    <header class="mb-10">
      <div class="label-bracket text-signal-400 text-xs mb-3">[ M.08.terms ]</div>
      <h1 class="font-display text-3xl sm:text-4xl text-fg-50 tracking-tight">Terms of Service</h1>
      <p class="mt-3 text-fg-500 text-sm">Last updated: {{ lastUpdated }}</p>
    </header>

    <section class="space-y-6 text-fg-300 leading-relaxed text-[15px]">

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">1. Acceptance of Terms</h2>
        <p class="mb-2">
          By accessing or using cebolla.live (the "Platform", "Service"), you agree to be bound
          by these Terms of Service ("Terms"). If you do not agree to these Terms, do not use the
          Platform.
        </p>
        <p>
          You must be at least 18 years of age to use the Platform. You must be 21+ to engage in
          any sports wagering activity in most U.S. jurisdictions; verifying your eligibility for
          such activities is your responsibility, not ours.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">2. Description of Service</h2>
        <p class="mb-2">Cebolla provides:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Statistical projections for MLB players and games</li>
          <li>Historical batter-vs-pitcher matchup data</li>
          <li>Weather, lineup, and game-context information</li>
          <li>AI-generated parlay analysis (the "Cards" feature)</li>
          <li>Daily POD (Projection of the Day) analysis</li>
          <li>Other research tools and analytics</li>
        </ul>
        <p>
          <strong class="text-fg-100">THE PLATFORM IS A RESEARCH AND ANALYTICS TOOL.</strong>
          We do not accept wagers, process payments to sportsbooks, give specific betting advice,
          or guarantee any outcome.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">3. Not Betting Advice</h2>
        <p class="mb-2">
          All content provided on the Platform is for
          <strong class="text-fg-100">informational and entertainment purposes only</strong>.
          Nothing on the Platform constitutes:
        </p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Investment advice</li>
          <li>Financial advice</li>
          <li>Legal advice</li>
          <li>Tax advice</li>
          <li>Specific wagering recommendations</li>
        </ul>
        <p>You are solely responsible for any decisions you make using information from the Platform.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">4. Subscription and Payment</h2>
        <p class="mb-2">If you purchase a paid subscription:</p>
        <ul class="ml-4 space-y-1 list-disc">
          <li><strong class="text-fg-100">Billing:</strong> Subscriptions are billed in advance on a recurring basis (monthly, quarterly, semi-annually, or annually) until canceled.</li>
          <li><strong class="text-fg-100">Cancellation:</strong> You may cancel your subscription at any time through your account settings. Cancellation takes effect at the end of the current billing period.</li>
          <li><strong class="text-fg-100">Refunds:</strong> All sales are final. No refunds will be issued for any partial billing period. Exceptions may be made at our sole discretion in cases of billing error.</li>
          <li><strong class="text-fg-100">Price changes:</strong> We reserve the right to change subscription prices with at least 30 days' notice to existing subscribers.</li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">5. User Accounts</h2>
        <p class="mb-2">You are responsible for:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Maintaining the confidentiality of your account credentials</li>
          <li>All activity under your account</li>
          <li>Notifying us immediately of any unauthorized access</li>
        </ul>
        <p>We reserve the right to suspend or terminate accounts that violate these Terms.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">6. Prohibited Conduct</h2>
        <p class="mb-2">You may not:</p>
        <ul class="ml-4 space-y-1 list-disc">
          <li>Use the Platform for any illegal purpose</li>
          <li>Scrape, copy, or redistribute Platform content without authorization</li>
          <li>Reverse engineer Platform technology</li>
          <li>Share account access with others</li>
          <li>Use bots or automated systems to access the Platform</li>
          <li>Resell access to the Platform</li>
          <li>Use Platform data to operate a competing service</li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">7. Intellectual Property</h2>
        <p class="mb-2">
          The Platform, including its design, code, analytics models, projections, and branding
          ("Cebolla", logos, color schemes), is the intellectual property of Cebolla or its
          licensors.
        </p>
        <p class="mb-2">
          You may not reproduce, distribute, modify, or create derivative works without prior
          written authorization.
        </p>
        <p>
          Subscribers receive a limited, non-transferable license to access Platform content for
          personal use only during their subscription period.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">8. Third-Party Data and Services</h2>
        <p class="mb-2">
          The Platform aggregates data from publicly available sources, including but not limited
          to MLB statistical sources and sportsbook odds boards. We are not affiliated with,
          sponsored by, or endorsed by:
        </p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Major League Baseball or its affiliates</li>
          <li>DraftKings, FanDuel, or other sportsbooks</li>
          <li>Any sports league, team, or player</li>
          <li>Any other third-party data provider</li>
        </ul>
        <p>We make no warranty regarding the accuracy of third-party data.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">9. Disclaimer of Warranties</h2>
        <p class="mb-2 uppercase tracking-wide text-xs text-fg-500">
          The Platform is provided "as is" and "as available". We make no warranties, express or
          implied, including without limitation:
        </p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Accuracy of projections or analytics</li>
          <li>Accuracy of odds data</li>
          <li>Profitability of any wagering strategy</li>
          <li>Uninterrupted or error-free service</li>
          <li>Fitness for any particular purpose</li>
        </ul>
        <p class="uppercase tracking-wide text-xs text-fg-500">
          Past performance does not guarantee future results.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">10. Limitation of Liability</h2>
        <p class="mb-2">
          To the maximum extent permitted by law, Cebolla shall not be liable for:
        </p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Any wagering losses you incur</li>
          <li>Any indirect, incidental, consequential, or punitive damages</li>
          <li>Lost profits or lost data</li>
          <li>Service interruptions or technical issues</li>
          <li>Third-party actions or content</li>
        </ul>
        <p>
          Our total liability for any claim arising from these Terms or your use of the Platform
          shall not exceed the amount you paid to us in the 12 months preceding the claim, or $100,
          whichever is greater.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">11. Indemnification</h2>
        <p>
          You agree to indemnify and hold harmless Cebolla, its officers, employees, and agents
          from any claims, damages, or expenses arising from your use of the Platform or violation
          of these Terms.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">12. Governing Law and Disputes</h2>
        <p class="mb-2">
          These Terms are governed by the laws of the State of Florida, without regard to its
          conflict of laws principles.
        </p>
        <p>
          Any disputes arising from these Terms or your use of the Platform shall be resolved
          exclusively through binding arbitration in Hillsborough County, Florida, in accordance
          with the rules of the American Arbitration Association. You waive any right to
          participate in class action proceedings.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">13. Termination</h2>
        <p>
          We may suspend or terminate your access to the Platform at any time, with or without
          cause, with or without notice. Upon termination, your right to use the Platform ceases
          immediately.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">14. Modifications to Terms</h2>
        <p>
          We reserve the right to modify these Terms at any time. We will notify users of material
          changes by posting the updated Terms on the Platform with a revised "Last Updated"
          date. Continued use of the Platform after changes constitutes acceptance of the modified
          Terms.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">15. Responsible Gambling</h2>
        <p class="mb-2">If you or someone you know has a gambling problem:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>National Council on Problem Gambling: <a href="tel:18004262537" class="text-signal-400 hover:text-signal-300 underline">1-800-GAMBLER</a></li>
          <li>Website: <a href="https://www.ncpgambling.org" target="_blank" rel="noopener noreferrer" class="text-signal-400 hover:text-signal-300 underline">ncpgambling.org</a></li>
        </ul>
        <p>Never wager more than you can afford to lose. Sports betting is risky; most bettors lose money over time.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">16. Severability</h2>
        <p>
          If any provision of these Terms is found unenforceable, the remaining provisions remain
          in full force and effect.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">17. Contact</h2>
        <p>Questions about these Terms: <a href="mailto:support@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">support@cebolla.live</a></p>
      </div>

      <hr class="border-bg-200/30 my-8" />
      <p class="text-fg-500 text-xs italic">
        Cebolla is an independent sports analytics platform and is not affiliated with Major
        League Baseball, MLB Advanced Media, the MLB Players Association, or any sportsbook.
      </p>
    </section>
  </article>
</template>
