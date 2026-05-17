<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { refreshSlate } from '../utils/githubDispatch.js'

const route = useRoute()

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

// ── Manual refresh ──
const refreshState = ref('idle')   // 'idle' | 'firing' | 'pulling' | 'done' | 'error'
const refreshError = ref(null)

async function handleRefresh() {
  if (refreshState.value !== 'idle' && refreshState.value !== 'done' && refreshState.value !== 'error') return
  refreshState.value = 'firing'
  refreshError.value = null

  const result = await refreshSlate()
  if (!result.ok) {
    refreshState.value = 'error'
    refreshError.value = result.error || 'dispatch failed'
    setTimeout(() => { refreshState.value = 'idle' }, 5000)
    return
  }

  // Workflows are now running on GH. Give them ~45s to write fresh data,
  // then auto-reload the page so the user sees the new state.
  refreshState.value = 'pulling'
  setTimeout(() => {
    refreshState.value = 'done'
    setTimeout(() => window.location.reload(), 800)
  }, 45000)
}

const refreshLabel = computed(() => {
  switch (refreshState.value) {
    case 'firing':  return 'sending…'
    case 'pulling': return 'pulling…'
    case 'done':    return 'refreshed ✓'
    case 'error':   return 'failed'
    default:        return 'refresh'
  }
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
        <div class="hidden md:flex items-center gap-2">
          <span class="w-1.5 h-1.5 rounded-full bg-signal-400 animate-pulse"></span>
          <span class="label-caps">live</span>
        </div>

        <!-- Refresh data button -->
        <button
          @click="handleRefresh"
          :disabled="refreshState !== 'idle' && refreshState !== 'done' && refreshState !== 'error'"
          class="refresh-btn"
          :class="`state-${refreshState}`"
          :title="refreshError || 'Trigger lineups + odds + projections pull'"
        >
          <span class="refresh-dot"></span>
          <span class="label-caps">{{ refreshLabel }}</span>
        </button>

        <div class="flex items-baseline gap-2">
          <span class="label-caps">UTC</span>
          <span class="display-num text-xs text-fg-600">{{ now }}</span>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 2px;
  color: var(--color-fg-500, #888);
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  cursor: pointer;
  transition: all 0.15s;
}
.refresh-btn:hover:not(:disabled) {
  border-color: rgba(255, 42, 42, 0.4);
  color: rgba(255, 42, 42, 0.85);
}
.refresh-btn:disabled {
  cursor: progress;
  opacity: 0.7;
}

.refresh-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.3);
  transition: background 0.15s;
}
.refresh-btn:hover:not(:disabled) .refresh-dot {
  background: rgba(255, 42, 42, 0.85);
}

.state-firing .refresh-dot,
.state-pulling .refresh-dot {
  background: rgba(255, 42, 42, 0.85);
  animation: pulse-dot 1s ease-in-out infinite;
}
.state-firing,
.state-pulling {
  border-color: rgba(255, 42, 42, 0.4) !important;
  color: rgba(255, 42, 42, 0.85) !important;
}
.state-done .refresh-dot {
  background: rgba(95, 158, 160, 1);
}
.state-done {
  border-color: rgba(95, 158, 160, 0.5) !important;
  color: rgba(95, 158, 160, 1) !important;
}
.state-error .refresh-dot {
  background: rgba(255, 42, 42, 1);
}
.state-error {
  border-color: rgba(255, 42, 42, 0.6) !important;
  color: rgba(255, 42, 42, 1) !important;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 0.5; transform: scale(0.85); }
  50%      { opacity: 1;   transform: scale(1.2);  }
}
</style>
