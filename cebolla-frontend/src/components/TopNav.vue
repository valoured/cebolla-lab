<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRealtimePulse } from '../composables/useRealtimePulse.js'

const route = useRoute()
const { isPulsing } = useRealtimePulse()

const navItems = [
  { name: 'slate',  label: 'Slate',    code: 'M.01' },
  { name: 'bets',   label: 'Bet Log',  code: 'M.02' },
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
    <div class="px-3 sm:px-6 h-14 flex items-center gap-3 sm:gap-8">
      <!-- Brand: icon + wordmark. Wordmark hides below sm to save mobile space. -->
      <router-link to="/" class="flex items-center gap-2 sm:gap-3 group shrink-0">
        <img
          src="/cebolla-icon-64-transparent.png"
          alt="Cebolla"
          class="cebolla-logo-nav"
          width="28"
          height="28"
        />
        <img
          src="/cebolla-wordmark.png"
          alt="Cebolla · Every Layer Reveals an Edge"
          class="cebolla-wordmark-nav hidden sm:block"
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
        <!-- Live status: dot+label on md+, just dot on mobile -->
        <div class="hidden md:flex items-center gap-2">
          <span :key="burstKey" class="live-dot" :class="{ 'is-burst': isPulsing }"></span>
          <span class="label-caps">live</span>
        </div>
        <div class="flex md:hidden items-center">
          <span :key="`m-${burstKey}`" class="live-dot" :class="{ 'is-burst': isPulsing }"></span>
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
.cebolla-logo-nav {
  width: 28px;
  height: 28px;
  filter: drop-shadow(0 0 4px rgba(255, 42, 42, 0.25));
  transition: filter 0.3s ease, transform 0.3s ease;
  user-select: none;
  -webkit-user-drag: none;
}
.group:hover .cebolla-logo-nav {
  filter: drop-shadow(0 0 8px rgba(255, 42, 42, 0.7))
          drop-shadow(0 0 16px rgba(255, 42, 42, 0.25));
  transform: scale(1.06);
}

.cebolla-wordmark-nav {
  /* Wordmark image is 4:1 (1024x254). At ~30px tall = ~120px wide. */
  height: 30px;
  width: auto;
  filter: drop-shadow(0 0 4px rgba(255, 42, 42, 0.20));
  transition: filter 0.3s ease;
  user-select: none;
  -webkit-user-drag: none;
}
.group:hover .cebolla-wordmark-nav {
  filter: drop-shadow(0 0 8px rgba(255, 42, 42, 0.55));
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(255, 42, 42, 0.4);
  animation: ambient-pulse 3s ease-in-out infinite;
}

.live-dot.is-burst {
  animation: data-burst 1.5s ease-out;
  background: rgba(255, 42, 42, 1);
}

@keyframes ambient-pulse {
  0%, 100% {
    background: rgba(255, 42, 42, 0.35);
    box-shadow: 0 0 0 0 rgba(255, 42, 42, 0);
  }
  50% {
    background: rgba(255, 42, 42, 0.75);
    box-shadow: 0 0 6px 1px rgba(255, 42, 42, 0.3);
  }
}

@keyframes data-burst {
  0% {
    transform: scale(0.7);
    box-shadow: 0 0 0 0 rgba(255, 42, 42, 0.9);
    background: rgba(255, 42, 42, 1);
  }
  40% {
    transform: scale(1.35);
    box-shadow: 0 0 0 7px rgba(255, 42, 42, 0.35),
                0 0 22px rgba(255, 42, 42, 0.85);
    background: rgba(255, 80, 80, 1);
  }
  100% {
    transform: scale(1);
    box-shadow: 0 0 6px 1px rgba(255, 42, 42, 0.3);
    background: rgba(255, 42, 42, 0.6);
  }
}
</style>
