<script setup>
/**
 * PrivacyView.vue — Cebolla Privacy Policy.
 *
 * MIRRORS legal/privacy-policy.md. GDPR/CCPA-scaffolded template
 * that should be reviewed by an attorney before launch, especially
 * if collecting EU/CA user data.
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
      <div class="label-bracket text-signal-400 text-xs mb-3">[ M.09.privacy ]</div>
      <h1 class="font-display text-3xl sm:text-4xl text-fg-50 tracking-tight">Privacy Policy</h1>
      <p class="mt-3 text-fg-500 text-sm">Last updated: {{ lastUpdated }}</p>
    </header>

    <section class="space-y-6 text-fg-300 leading-relaxed text-[15px]">

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">1. Introduction</h2>
        <p>
          Cebolla ("we", "our", "the Platform") is an MLB analytics and research platform that
          provides statistical projections and research tools for informational and entertainment
          purposes only. We respect your privacy. This Privacy Policy explains how we collect,
          use, and protect information when you use cebolla.live.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">2. Information We Collect</h2>

        <h3 class="font-semibold text-fg-200 mt-3 mb-1">Account Information (when you sign up):</h3>
        <ul class="ml-4 space-y-1 list-disc">
          <li>Email address</li>
          <li>Username</li>
          <li>Hashed password</li>
          <li>Subscription tier and billing status</li>
        </ul>

        <h3 class="font-semibold text-fg-200 mt-3 mb-1">Usage Data:</h3>
        <ul class="ml-4 space-y-1 list-disc">
          <li>Pages and features accessed</li>
          <li>Search queries within the Platform</li>
          <li>Time spent on Platform</li>
          <li>Device type and browser</li>
          <li>IP address (used for security and analytics; anonymized where possible)</li>
        </ul>

        <h3 class="font-semibold text-fg-200 mt-3 mb-1">Payment Information:</h3>
        <ul class="ml-4 space-y-1 list-disc">
          <li>Processed exclusively through our third-party payment processor (e.g., Stripe).</li>
          <li>We do not store full credit card numbers on our servers. We retain only the last 4 digits and card brand for receipt purposes.</li>
        </ul>

        <h3 class="font-semibold text-fg-200 mt-3 mb-1">We do <span class="text-edge-cold-1">NOT</span> collect:</h3>
        <ul class="ml-4 space-y-1 list-disc">
          <li>Information about your sports betting activity outside Cebolla</li>
          <li>Your real-money wagering history</li>
          <li>Your sportsbook account information</li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">3. How We Use Information</h2>
        <p class="mb-2">We use information to:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>Provide and maintain the Platform</li>
          <li>Process subscriptions and payments</li>
          <li>Personalize content (e.g., dashboard preferences)</li>
          <li>Send important account notifications (security, billing)</li>
          <li>Improve our analytics models and Platform features</li>
          <li>Respond to support requests</li>
          <li>Detect and prevent fraud</li>
        </ul>
        <p>We do <strong class="text-fg-100">not</strong> sell personal information to third parties.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">4. Cookies and Tracking</h2>
        <p class="mb-2">We use cookies for:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li><strong class="text-fg-200">Essential:</strong> Authentication, session management</li>
          <li><strong class="text-fg-200">Functional:</strong> Remember your preferences (e.g., theme, default sport)</li>
          <li><strong class="text-fg-200">Analytics:</strong> Understand Platform usage in aggregate</li>
        </ul>
        <p>You may disable non-essential cookies through your browser settings. Disabling essential cookies may break Platform functionality.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">5. Data Sharing</h2>
        <p class="mb-2">We share information only with:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li><strong class="text-fg-200">Service Providers:</strong> Cloud hosting (Cloudflare), database (Supabase), payment processing (Stripe), analytics platforms — all bound by contractual data protection obligations.</li>
          <li><strong class="text-fg-200">Legal Requirements:</strong> When required by law (subpoena, court order, law enforcement request with proper legal basis).</li>
          <li><strong class="text-fg-200">Business Transfers:</strong> In the event of merger, acquisition, or sale of assets, your information may be transferred subject to this Policy.</li>
        </ul>
        <p class="mb-2">We do <strong class="text-fg-100">not</strong> share your information with:</p>
        <ul class="ml-4 space-y-1 list-disc">
          <li>Sportsbooks or gambling operators</li>
          <li>Marketing partners</li>
          <li>Data brokers</li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">6. Data Retention</h2>
        <ul class="ml-4 space-y-1 list-disc">
          <li><strong class="text-fg-200">Account data:</strong> Retained while your account is active and for up to 90 days after deletion for compliance and dispute resolution.</li>
          <li><strong class="text-fg-200">Payment records:</strong> Retained for 7 years for tax and financial compliance.</li>
          <li><strong class="text-fg-200">Usage logs:</strong> Aggregated and anonymized after 12 months.</li>
        </ul>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">7. Security</h2>
        <p class="mb-2">We implement industry-standard security measures including:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li>HTTPS/TLS encryption for all data in transit</li>
          <li>Password hashing using industry-standard algorithms</li>
          <li>Restricted access to production systems</li>
          <li>Regular security reviews</li>
        </ul>
        <p>No system is 100% secure. You are responsible for maintaining the confidentiality of your account credentials.</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">8. Your Rights</h2>
        <p class="mb-2">Depending on your jurisdiction, you may have the right to:</p>
        <ul class="ml-4 space-y-1 list-disc mb-2">
          <li><strong class="text-fg-200">Access:</strong> Request a copy of your personal data</li>
          <li><strong class="text-fg-200">Correction:</strong> Update inaccurate information</li>
          <li><strong class="text-fg-200">Deletion:</strong> Request deletion of your account and data</li>
          <li><strong class="text-fg-200">Portability:</strong> Export your data in a structured format</li>
          <li><strong class="text-fg-200">Opt-out:</strong> Unsubscribe from non-essential emails</li>
          <li><strong class="text-fg-200">Restriction:</strong> Restrict certain processing activities</li>
        </ul>
        <p class="mb-2">To exercise these rights, contact <a href="mailto:privacy@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">privacy@cebolla.live</a>.</p>
        <p class="mb-2">For California residents: We comply with CCPA. You may also request information about categories of personal data we have collected.</p>
        <p>For EU/UK residents: We comply with GDPR. Our legal basis for processing is performance of contract (subscription) and legitimate interests (Platform improvement, fraud prevention).</p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">9. Children's Privacy</h2>
        <p>
          The Platform is not intended for users under 18. Users engaging in any wagering-related
          activity must be 21+ in most U.S. jurisdictions. We do not knowingly collect personal
          information from individuals under 18. If we discover such collection, we will delete
          the information.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">10. International Users</h2>
        <p>
          The Platform is operated from the United States. By using the Platform, you consent to
          your information being transferred to and processed in the United States.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">11. Third-Party Links</h2>
        <p>
          The Platform may contain links to third-party websites (e.g., sportsbooks, news
          sources). We are not responsible for the privacy practices of these sites. Review their
          privacy policies before sharing information.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">12. Changes to This Policy</h2>
        <p>
          We may update this Privacy Policy from time to time. Material changes will be notified
          via Platform announcement or email. Continued use of the Platform after changes
          constitutes acceptance.
        </p>
      </div>

      <div>
        <h2 class="font-display text-lg text-fg-100 mb-2">13. Contact</h2>
        <ul class="ml-1 space-y-1">
          <li>Privacy questions or requests: <a href="mailto:privacy@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">privacy@cebolla.live</a></li>
          <li>General support: <a href="mailto:support@cebolla.live" class="text-signal-400 hover:text-signal-300 underline">support@cebolla.live</a></li>
        </ul>
      </div>

      <hr class="border-bg-200/30 my-8" />
      <p class="text-fg-500 text-xs italic">
        Cebolla — MLB Analytics &amp; Research Platform
      </p>
    </section>
  </article>
</template>
