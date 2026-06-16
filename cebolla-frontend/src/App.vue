<script setup>
import { ref } from 'vue'
import TopNav from './components/TopNav.vue'
import SearchModal from './components/SearchModal.vue'

// ─── STOP-THE-BLEED NOTICE (rebuild stop-gap) ──────────────────────────────
// Sitewide "model under reconstruction" banner shown on every page while the
// picker model is rebuilt. Dismissible per browser SESSION (sessionStorage,
// not localStorage) so it reappears each new session until the v2 rebuild
// ships and this block + the banner markup are removed.
const REBUILD_BANNER_KEY = 'cebolla_rebuild_banner_dismissed'
const rebuildBannerDismissed = ref(
  sessionStorage.getItem(REBUILD_BANNER_KEY) === '1'
)
function dismissRebuildBanner() {
  rebuildBannerDismissed.value = true
  try { sessionStorage.setItem(REBUILD_BANNER_KEY, '1') } catch (e) { /* storage blocked — fine, just won't persist */ }
}
</script>

<template>
  <div class="min-h-screen flex flex-col">
    <!-- ─── STOP-THE-BLEED NOTICE ──────────────────────────────────────── -->
    <!-- Above everything (incl. TopNav). High-visibility yellow/red, but    -->
    <!-- dismissible per session. Hex literals per aesthetic-lock lesson 7   -->
    <!-- (Tailwind tokens flaky for critical UI).                            -->
    <div
      v-if="!rebuildBannerDismissed"
      role="alert"
      class="relative z-50 w-full"
      style="background:#FFD400;color:#111111;border-bottom:2px solid #FF2A2A;"
    >
      <div class="max-w-6xl mx-auto px-4 sm:px-6 py-2.5 flex items-start gap-3">
        <span class="text-base leading-none mt-0.5" aria-hidden="true">⚠️</span>
        <p class="flex-1 text-[12px] sm:text-[13px] font-semibold leading-snug">
          Model under reconstruction.
          Picks shown for tracking only — do not bet real money.
          Returning with improved predictions soon.
        </p>
        <button
          type="button"
          @click="dismissRebuildBanner"
          aria-label="Dismiss notice"
          class="shrink-0 text-[18px] leading-none font-bold px-1 hover:opacity-60 transition"
          style="color:#111111;"
        >×</button>
      </div>
    </div>

    <TopNav />
    <main class="flex-1 min-w-0 relative z-10">
      <router-view v-slot="{ Component, route }">
        <transition name="fade" mode="out-in">
          <component :is="Component" :key="route.fullPath" />
        </transition>
      </router-view>
    </main>

    <!-- Global player+team search modal. Mounted once at root so the
         Ctrl/Cmd+K and `/` hotkeys work from anywhere in the app. -->
    <SearchModal />

    <!-- ─── GLOBAL LEGAL FOOTER ────────────────────────────────── -->
    <!-- Sitewide disclaimer per Cebolla legal positioning: research      -->
    <!-- platform, not betting advice. Visible on every page.             -->
    <footer class="border-t border-bg-200/30 bg-bg-50/60 px-4 sm:px-6 py-6 mt-auto relative z-10 footer-safe">
      <div class="max-w-6xl mx-auto space-y-4">
        <!-- Primary disclaimer block -->
        <div class="text-[10px] sm:text-[11px] text-fg-500 leading-relaxed">
          <p class="mb-2">
            <span class="font-display text-fg-700">CEBOLLA</span> provides sports analytics, projections, and research tools
            <span class="font-semibold text-fg-600">for informational and entertainment purposes only</span>.
            Nothing on this platform constitutes financial advice or betting advice.
          </p>
          <p class="mb-2">
            Sports betting involves substantial risk and may not be legal in your jurisdiction.
            Never wager more than you can afford to lose. Past projections do not guarantee future results.
            Must be 21+ to participate in legal sports wagering where permitted.
          </p>
          <p>
            If you or someone you know has a gambling problem, call
            <a href="tel:18004262537" class="text-signal-400 hover:text-signal-300 underline">1-800-GAMBLER</a>
            or visit
            <a href="https://www.ncpgambling.org" target="_blank" rel="noopener noreferrer" class="text-signal-400 hover:text-signal-300 underline">ncpgambling.org</a>.
          </p>
        </div>

        <!-- Affiliation disclaimer -->
        <div class="text-[10px] text-fg-500/80 italic leading-relaxed">
          Cebolla is an independent research platform and is not affiliated with, endorsed by, or sponsored by
          Major League Baseball, MLB Advanced Media, the MLB Players Association, DraftKings, FanDuel, or any other
          sportsbook, sports league, team, or governing body.
        </div>

        <!-- Footer nav links -->
        <div class="flex flex-wrap gap-x-4 gap-y-2 pt-2 text-[10px] uppercase tracking-wider text-fg-500/80">
          <router-link to="/about" class="hover:text-signal-400 transition">About</router-link>
          <span class="text-fg-300">·</span>
          <router-link to="/methodology" class="hover:text-signal-400 transition">Methodology</router-link>
          <span class="text-fg-300">·</span>
          <router-link to="/terms" class="hover:text-signal-400 transition">Terms</router-link>
          <span class="text-fg-300">·</span>
          <router-link to="/privacy" class="hover:text-signal-400 transition">Privacy</router-link>
          <span class="text-fg-300">·</span>
          <router-link to="/disclaimer" class="hover:text-signal-400 transition">Disclaimer</router-link>
          <span class="text-fg-300">·</span>
          <a href="mailto:support@cebolla.live" class="hover:text-signal-400 transition">Contact</a>
        </div>

        <div class="text-[9px] text-fg-500/60 pt-1">
          © {{ new Date().getFullYear() }} Cebolla. All rights reserved.
        </div>
      </div>
    </footer>
  </div>
</template>

<style>
.fade-enter-active, .fade-leave-active {
  transition: opacity 200ms ease;
}
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* Respect iPhone home-indicator safe area on the footer, plus side
   insets for landscape. The notch top-inset is handled separately on
   the TopNav (which is sticky and renders at the top of viewport). */
.footer-safe {
  padding-bottom: calc(1.5rem + env(safe-area-inset-bottom));
  padding-left: calc(1rem + env(safe-area-inset-left));
  padding-right: calc(1rem + env(safe-area-inset-right));
}
@media (min-width: 640px) {
  .footer-safe {
    padding-left: calc(1.5rem + env(safe-area-inset-left));
    padding-right: calc(1.5rem + env(safe-area-inset-right));
  }
}
</style>