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

// Force the burst animation to retrigger each time isPulsing flips true.
// Without this, CSS won't replay the keyframe if the class is already there.
const burstKey = ref(0)
watch(isPulsing, (v) => {
  if (v) burstKey.value++
})
</script>

<template>
  <header class="sticky top-0 z-50 bg-bg-0/95 backdrop-blur border-b border-bg-200">
    <div class="px-6 h-14 flex items-center gap-8">
      <!-- Brand: Red Onion -->
      <router-link to="/" class="flex items-center gap-3 group">
        <span
          class="text-2xl leading-none select-none transition-transform duration-500 group-hover:rotate-12 inline-block"
          style="filter: hue-rotate(-40deg) saturate(2.5) brightness(1.05) drop-shadow(0 0 6px rgba(255,42,42,0.55));"
        >🧅</span>
        <div class="flex flex-col leading-none">
          <span class="display-text text-base text-fg-700 tracking-tight">cebolla</span>
          <span class="label-bracket !text-[8px] mt-0.5 text-signal-400/80">lab v0.3</span>
        </div>
      </router-link>

      <!-- Nav tabs -->
      <nav class="flex items-center gap-1 ml-4">
        <router-link
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="px-4 py-2 text-sm transition relative group"
          :class="[
            isActive(item.name)
              ? 'text-fg-700'
              : 'text-fg-500 hover:text-fg-700'
          ]"
        >
          <span class="flex items-baseline gap-2">
            <span class="font-medium">{{ item.label }}</span>
            <span class="label-bracket !text-[8px] opacity-60">{{ item.code }}</span>
          </span>
          <span
            v-if="isActive(item.name)"
            class="absolute bottom-0 left-2 right-2 h-px bg-signal-400"
          ></span>
        </router-link>
      </nav>

      <!-- Right side -->
      <div class="ml-auto flex items-center gap-6">
        <!-- Live status: ambient pulse + burst on every realtime event -->
        <div class="hidden md:flex items-center gap-2">
          <span :key="burstKey" class="live-dot" :class="{ 'is-burst': isPulsing }"></span>
          <span class="label-caps">live</span>
        </div>
        <div class="flex items-baseline gap-2">
          <span class="label-caps">UTC</span>
          <span class="display-num text-xs text-fg-600">{{ now }}</span>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
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
