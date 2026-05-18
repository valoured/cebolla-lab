// composables/useRealtimePulse.js
// Tiny shared store: subscriptions emit a "ping" when data arrives;
// TopNav listens and briefly pulses its live indicator.

import { ref } from 'vue'

const lastPingAt = ref(0)
const isPulsing = ref(false)
let pulseTimer = null

export function ping() {
  lastPingAt.value = Date.now()
  isPulsing.value = true
  if (pulseTimer) clearTimeout(pulseTimer)
  pulseTimer = setTimeout(() => {
    isPulsing.value = false
  }, 1500)
}

export function useRealtimePulse() {
  return { lastPingAt, isPulsing, ping }
}
