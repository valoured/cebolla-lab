<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRealtimePulse } from '../composables/useRealtimePulse.js'

const route = useRoute()
const { isPulsing } = useRealtimePulse()

const navItems = [
  { name: 'slate',        label: 'Slate',        code: 'M.01' },
  { name: 'bets',         label: 'Bet Log',      code: 'M.02' },
  { name: 'methodology',  label: 'Methodology',  code: 'M.03' },
]

function isActive(name) {
  if (name === 'slate' && (route.name === 'slate' || route.name === 'hr-report' || route.name === 'player')) {
    return true
  }
  return route.name === name
}

const now = computed(() => {
  const d = new Date()
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
})

const burstKey = ref(0)
watch(isPulsing, (v) => {
  if (v) burstKey.value++
})
</script>

<template>
  <header class="sticky top-0 z-50 bg-bg-0/95 backdrop-blur border-b border-bg-200">
    <!-- px-3 mobile / px-6 desktop. Gap collapses on mobile. -->
    <div class="px-3 sm:px-6 h-16 sm:h-20 flex items-center gap-3 sm:gap-8">
      <!-- Brand: wordmark only (icon removed — now lives as the realtime indicator). -->
      <router-link to="/" class="flex items-center group shrink-0">
        <img
          src="/cebolla-wordmark.png"
          alt="Cebolla · Every Layer Reveals an Edge"
          class="cebolla-wordmark-nav"
        />
      </router-link>

      <!-- Nav tabs -->
      <nav class="flex items-center gap-0 sm:gap-1 sm:ml-4">
        <router-link
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="px-2.5 sm:px-4 py-2 text-sm transition relative group"
          :class="[
            isActive(item.name)
              ? 'text-fg-700'
              : 'text-fg-500 hover:text-fg-700'
          ]"
        >
          <span class="flex items-baseline gap-1.5 sm:gap-2">
            <span class="font-medium">{{ item.label }}</span>
            <!-- Hide the M.0X code on mobile to save space -->
            <span class="hidden sm:inline label-bracket !text-[8px] opacity-60">{{ item.code }}</span>
          </span>
          <span
            v-if="isActive(item.name)"
            class="absolute bottom-0 left-2 right-2 h-px bg-signal-400"
          ></span>
        </router-link>
      </nav>

      <!-- Right side -->
      <div class="ml-auto flex items-center gap-3 sm:gap-6">
        <!-- Live status: pulsing onion icon as realtime indicator -->
        <div class="hidden md:flex items-center gap-2">
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
        <div class="flex items-baseline gap-1.5 sm:gap-2">
          <span class="label-caps">UTC</span>
          <span class="display-num text-xs text-fg-600">{{ now }}</span>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.cebolla-wordmark-nav {
  /* Full wordmark is 1024x254 (~4:1). At 56px tall ≈ 226px wide.
     Tagline portion renders at ~12-14px which is comfortably legible. */
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

/* Live realtime indicator: tiny glowing onion that pulses ambient,
   then bursts brighter when Supabase realtime fires (data arriving). */
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
</style>
