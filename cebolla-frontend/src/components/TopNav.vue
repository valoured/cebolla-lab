<script setup>
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useRealtimePulse } from '../composables/useRealtimePulse.js'

const route = useRoute()
const { isPulsing } = useRealtimePulse()

// Primary nav items — order matters (left to right).
// The 'methodology' tab lives in a secondary hidden-on-mobile bucket so
// the top bar stays scannable on small screens.
const navItems = [
  { name: 'slate',       label: 'Slate',       code: 'M.01' },
  { name: 'pod',         label: 'POD',         code: 'M.02' },
  { name: 'cards',       label: 'Cards',       code: 'M.03' },
  { name: 'matchups',    label: 'Matchups',    code: 'M.06' },
  { name: 'trends',      label: 'Trends',      code: 'M.07' },
  { name: 'stats',       label: 'Stats',       code: 'M.04' },
  { name: 'methodology', label: 'Methodology', code: 'M.05' },
]

// Sport selector — MLB is live today. NFL is scaffolded so users know it's
// coming. Click on a 'coming soon' sport does nothing yet.
const sports = [
  { key: 'mlb', label: 'MLB', live: true },
  { key: 'nfl', label: 'NFL', live: false },
]
const activeSport = ref('mlb')

function selectSport(s) {
  if (!s.live) return    // disabled — coming soon
  activeSport.value = s.key
}

// Every deep-dive page that surfaces slate content (game detail, player,
// team) keeps the 'Slate' tab highlighted so the user can always trace
// back to the page that brought them there.
const SLATE_DESCENDANTS = ['slate', 'hr-report', 'player', 'team']
function isActive(name) {
  if (name === 'slate' && SLATE_DESCENDANTS.includes(route.name)) return true
  return route.name === name
}

// Clock ticks every 30s. Without a reactive dep, `now` would evaluate once
// at setup and freeze. Recomputing every second is wasteful for an HH:MM
// display; 30s is plenty since the lowest displayed unit is minutes.
const clockTick = ref(0)
let clockTimer = null
onMounted(() => {
  clockTimer = setInterval(() => clockTick.value++, 30_000)
  document.addEventListener('keydown', onMenuKeydown)
})
onUnmounted(() => {
  if (clockTimer) clearInterval(clockTimer)
  document.removeEventListener('keydown', onMenuKeydown)
})

const now = computed(() => {
  clockTick.value  // reactive dep
  const d = new Date()
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
})

const burstKey = ref(0)
watch(isPulsing, (v) => {
  if (v) burstKey.value++
})

// Trigger the global SearchModal. SearchModal exposes a window-level open
// handle so we don't need a Vue event bus or prop drilling for this.
function openSearch() {
  if (typeof window !== 'undefined' && typeof window.__openCebollaSearch === 'function') {
    window.__openCebollaSearch()
  }
}

// ── Mobile nav menu (below lg breakpoint) ──────────────────────────
// Below lg the horizontal nav is hidden, so this hamburger-driven panel
// exposes every section. Focus moves into the panel on open and returns to
// the toggle on close, so keyboard / screen-reader users aren't stranded at
// the top of the page when the menu dismisses.
const mobileMenuOpen = ref(false)
const hamburgerRef = ref(null)
const mobilePanelRef = ref(null)

function openMobileMenu() {
  mobileMenuOpen.value = true
  nextTick(() => {
    mobilePanelRef.value?.querySelector('a')?.focus()
  })
}
function closeMobileMenu() {
  if (!mobileMenuOpen.value) return
  mobileMenuOpen.value = false
  nextTick(() => {
    hamburgerRef.value?.focus()
  })
}
function toggleMobileMenu() {
  if (mobileMenuOpen.value) closeMobileMenu()
  else openMobileMenu()
}
function onMenuKeydown(e) {
  if (e.key === 'Escape' && mobileMenuOpen.value) closeMobileMenu()
}
</script>

<template>
  <header class="sticky top-0 z-50 bg-bg-0/95 backdrop-blur border-b border-bg-200 topnav-safe">
    <div class="px-3 sm:px-6 h-16 sm:h-20 flex items-center gap-3 sm:gap-6">
      <!-- Brand -->
      <router-link to="/" class="flex items-center group shrink-0">
        <img
          src="/cebolla-wordmark.png"
          alt="Cebolla · Every Layer Reveals an Edge"
          class="cebolla-wordmark-nav"
        />
      </router-link>

      <!-- Nav tabs -->
      <nav class="hidden lg:flex items-center gap-0 sm:gap-1 sm:ml-2 overflow-x-auto scrollbar-none h-scroll self-stretch">
        <router-link
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="px-2 sm:px-3 py-2 text-sm transition relative group shrink-0 flex items-center self-stretch"
          :class="[
            isActive(item.name)
              ? 'text-fg-700'
              : 'text-fg-500 hover:text-fg-700'
          ]"
        >
          <span class="flex items-baseline gap-1 sm:gap-1.5">
            <span class="font-medium">{{ item.label }}</span>
            <span class="hidden md:inline label-bracket !text-[8px] opacity-60">{{ item.code }}</span>
          </span>
          <span
            v-if="isActive(item.name)"
            class="absolute bottom-0 left-2 right-2 h-px bg-signal-400"
          ></span>
        </router-link>
      </nav>

      <!-- Right side: search + sport selector + live indicator + clock -->
      <div class="ml-auto flex items-center gap-2 sm:gap-4 shrink-0">
        <!-- Search button — clickable affordance for users who don't know
             the Ctrl/Cmd+K or `/` hotkeys. Triggers the global SearchModal. -->
        <button
          type="button"
          @click="openSearch"
          class="search-trigger"
          aria-label="Search players and teams"
          title="Search players and teams (Ctrl+K)"
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7"></circle>
            <path d="m20 20-3.5-3.5"></path>
          </svg>
          <span class="hidden sm:inline search-trigger__label">find</span>
          <kbd class="hidden md:inline-flex search-trigger__kbd">⌘K</kbd>
        </button>

        <!-- Sport selector hidden below lg: only MLB is live;
             restore to hamburger panel when NFL ships -->
        <div class="hidden lg:flex items-center gap-1">
          <button
            v-for="s in sports"
            :key="s.key"
            type="button"
            @click="selectSport(s)"
            class="sport-pill"
            :class="{
              'sport-pill--active': s.live && activeSport === s.key,
              'sport-pill--soon':   !s.live,
            }"
            :title="s.live ? `${s.label} (active)` : `${s.label} — coming soon`"
            :aria-disabled="!s.live"
          >
            <span>{{ s.label }}</span>
            <span v-if="!s.live" class="sport-pill__soon-dot" aria-hidden="true"></span>
          </button>
        </div>

        <!-- Live realtime indicator -->
        <div class="hidden md:flex items-center gap-1.5">
          <img
            :key="burstKey"
            src="/cebolla-icon-64-transparent.png"
            alt=""
            class="live-onion"
            :class="{ 'is-burst': isPulsing }"
          />
          <span class="label-caps">live</span>
        </div>
        <div class="flex md:hidden items-center">
          <img
            :key="`m-${burstKey}`"
            src="/cebolla-icon-64-transparent.png"
            alt=""
            class="live-onion"
            :class="{ 'is-burst': isPulsing }"
          />
        </div>

        <!-- UTC clock — hidden on small mobile to save room -->
        <div class="hidden sm:flex items-baseline gap-1.5">
          <span class="label-caps">UTC</span>
          <span class="display-num text-xs text-fg-600">{{ now }}</span>
        </div>

        <!-- Mobile menu toggle — below lg the horizontal nav is hidden, so this
             hamburger opens a panel listing every section. -->
        <button
          ref="hamburgerRef"
          type="button"
          @click="toggleMobileMenu"
          class="lg:hidden mobile-menu-btn"
          :aria-expanded="mobileMenuOpen"
          aria-controls="mobile-nav-panel"
          :aria-label="mobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'"
        >
          <svg v-if="!mobileMenuOpen" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18M3 12h18M3 18h18"></path>
          </svg>
          <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18"></path>
          </svg>
        </button>
      </div>
    </div>

    <!-- Mobile nav panel (below lg). Backdrop fades, panel slides down under
         the header bar. Closes on item click, backdrop click, or Escape. -->
    <transition name="mobile-panel">
      <div v-if="mobileMenuOpen" class="lg:hidden">
        <div class="mobile-backdrop" @click="closeMobileMenu" aria-hidden="true"></div>
        <nav
          id="mobile-nav-panel"
          ref="mobilePanelRef"
          class="mobile-panel"
          aria-label="Primary"
        >
          <router-link
            v-for="item in navItems"
            :key="item.name"
            :to="{ name: item.name }"
            @click="closeMobileMenu"
            class="mobile-panel__item"
            :class="isActive(item.name) ? 'mobile-panel__item--active' : ''"
          >
            <span class="font-medium">{{ item.label }}</span>
            <span class="label-bracket !text-[8px] opacity-60">{{ item.code }}</span>
          </router-link>
        </nav>
      </div>
    </transition>
  </header>
</template>

<style scoped>
/* Respect iPhone notch + dynamic island safe areas. With viewport-fit=cover
   set in the HTML viewport meta, content can extend into the unsafe top
   area — without this, nav items would clip behind the notch on iPhone X+
   in portrait mode. Side insets handle landscape orientation (notch + home
   indicator on the sides). */
.topnav-safe {
  padding-top: env(safe-area-inset-top);
  padding-left: env(safe-area-inset-left);
  padding-right: env(safe-area-inset-right);
}

.cebolla-wordmark-nav {
  height: 44px;
  width: auto;
  filter: drop-shadow(0 0 4px rgba(255, 42, 42, 0.20));
  transition: filter 0.3s ease;
  user-select: none;
  -webkit-user-drag: none;
}
@media (min-width: 640px) {
  .cebolla-wordmark-nav {
    height: 56px;
  }
}
.group:hover .cebolla-wordmark-nav {
  filter: drop-shadow(0 0 8px rgba(255, 42, 42, 0.55));
}

/* Hide overflow scrollbar on the nav row for clean overflow on narrow screens */
.scrollbar-none {
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.scrollbar-none::-webkit-scrollbar { display: none; }

/* Global search trigger — matches the sport-pill aesthetic but with a
   distinct outline so it reads as an action rather than a state pill. */
.search-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  min-height: 36px;
  border: 1px solid var(--bg-200, #1c1c20);
  background: transparent;
  color: var(--fg-500, #8a8a92);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  cursor: pointer;
  transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
  border-radius: 2px;
}
.search-trigger:hover {
  border-color: rgba(255, 42, 42, 0.45);
  color: var(--fg-700, #c0c0c8);
  background: rgba(255, 42, 42, 0.04);
}
.search-trigger:focus-visible {
  outline: none;
  border-color: #FF2A2A;
  box-shadow: 0 0 0 1px rgba(255, 42, 42, 0.35);
}
.search-trigger__label {
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: lowercase;
}
.search-trigger__kbd {
  font-size: 9px;
  letter-spacing: 0.04em;
  padding: 1px 4px;
  border: 1px solid var(--bg-200, #1c1c20);
  border-radius: 2px;
  color: var(--fg-500, #8a8a92);
  background: rgba(255, 255, 255, 0.02);
  align-items: center;
}

/* Sport selector pills */
.sport-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  padding: 6px 8px;
  min-height: 32px;
  border: 1px solid var(--bg-200, #1c1c20);
  color: var(--fg-500, #8a8a92);
  background: transparent;
  cursor: pointer;
  transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
}
.sport-pill:hover:not(.sport-pill--soon) {
  border-color: rgba(255, 42, 42, 0.40);
  color: var(--fg-700, #c0c0c8);
}
.sport-pill--active {
  border-color: #FF2A2A;
  background: rgba(255, 42, 42, 0.10);
  color: #FF6B6B;
}
.sport-pill--soon {
  opacity: 0.55;
  cursor: not-allowed;
}
.sport-pill__soon-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: rgba(255, 200, 80, 0.65);
  display: inline-block;
}

/* Live realtime indicator: tiny glowing onion that pulses ambient,
   then bursts brighter when Supabase realtime fires. */
.live-onion {
  width: 18px;
  height: 18px;
  object-fit: contain;
  user-select: none;
  -webkit-user-drag: none;
  animation: onion-ambient 3s ease-in-out infinite;
  filter: drop-shadow(0 0 3px rgba(255, 42, 42, 0.35));
}
.live-onion.is-burst {
  animation: onion-burst 1.5s ease-out;
}
@keyframes onion-ambient {
  0%, 100% {
    transform: scale(0.95);
    filter: drop-shadow(0 0 2px rgba(255, 42, 42, 0.30));
  }
  50% {
    transform: scale(1.05);
    filter: drop-shadow(0 0 6px rgba(255, 42, 42, 0.70));
  }
}
@keyframes onion-burst {
  0% {
    transform: scale(0.85);
    filter: drop-shadow(0 0 3px rgba(255, 42, 42, 0.6));
  }
  40% {
    transform: scale(1.30);
    filter: drop-shadow(0 0 8px rgba(255, 42, 42, 1))
            drop-shadow(0 0 18px rgba(255, 42, 42, 0.55));
  }
  100% {
    transform: scale(1);
    filter: drop-shadow(0 0 4px rgba(255, 42, 42, 0.35));
  }
}

/* ── Mobile nav (below lg) ─────────────────────────────────────────── */
/* Hamburger toggle — mirrors the search-trigger outline aesthetic. */
.mobile-menu-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--bg-200, #1c1c20);
  background: transparent;
  color: var(--fg-500, #8a8a92);
  border-radius: 2px;
  cursor: pointer;
  transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
}
.mobile-menu-btn:hover {
  border-color: rgba(255, 42, 42, 0.45);
  color: var(--fg-700, #c0c0c8);
  background: rgba(255, 42, 42, 0.04);
}
.mobile-menu-btn:focus-visible {
  outline: none;
  border-color: #FF2A2A;
  box-shadow: 0 0 0 1px rgba(255, 42, 42, 0.35);
}

/* Full-viewport backdrop catches outside clicks. Sits below the panel but
   above page content (header owns the stacking context at z-50). */
.mobile-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 40;
}

/* Panel drops down directly under the header bar. */
.mobile-panel {
  position: absolute;
  left: 0;
  right: 0;
  top: 100%;
  z-index: 50;
  display: flex;
  flex-direction: column;
  background: var(--bg-0, #08080A);
  border-top: 1px solid var(--bg-200, #1c1c20);
  border-bottom: 1px solid var(--bg-200, #1c1c20);
  padding: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
.mobile-panel__item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  font-size: 14px;
  color: var(--fg-500, #8a8a92);
  border-left: 2px solid transparent;
  transition: color 120ms ease, background-color 120ms ease, border-color 120ms ease;
}
.mobile-panel__item:hover {
  color: var(--fg-700, #c0c0c8);
  background: rgba(255, 255, 255, 0.02);
}
.mobile-panel__item--active {
  color: var(--fg-700, #c0c0c8);
  border-left-color: #FF2A2A;
  background: rgba(255, 42, 42, 0.06);
}
.mobile-panel__item:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 1px rgba(255, 42, 42, 0.35);
}

/* Backdrop fades; panel slides down. */
.mobile-panel-enter-active,
.mobile-panel-leave-active {
  transition: opacity 150ms ease;
}
.mobile-panel-enter-active .mobile-panel,
.mobile-panel-leave-active .mobile-panel {
  transition: transform 150ms ease;
}
.mobile-panel-enter-from,
.mobile-panel-leave-to {
  opacity: 0;
}
.mobile-panel-enter-from .mobile-panel,
.mobile-panel-leave-to .mobile-panel {
  transform: translateY(-8px);
}
</style>
