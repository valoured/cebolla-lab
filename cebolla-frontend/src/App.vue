<script setup>
import TopNav from './components/TopNav.vue'
import SearchModal from './components/SearchModal.vue'
</script>

<template>
  <div class="min-h-screen flex flex-col">
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
    <footer class="border-t border-bg-200/30 bg-bg-50/60 px-4 sm:px-6 py-6 mt-auto relative z-10">
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
</style>