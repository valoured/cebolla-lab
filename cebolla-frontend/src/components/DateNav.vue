<script setup>
/**
 * DateNav.vue
 *
 * Horizontal date selector for the slate page. Renders one pill per
 * available slate date and lets the user override the auto-picked target.
 *
 * Props:
 *   - dates: string[]           // 'YYYY-MM-DD' list from useSlate.availableDates
 *   - activeDate: string|null   // currently-loaded date from useSlate.activeDate
 *   - targetDate: string|null   // user override from useSlate.targetDate (null = auto)
 *
 * Emits:
 *   - update:targetDate (dateStr | null)   // pass null to return to auto-pick
 */

import { computed } from 'vue'

const props = defineProps({
  dates:      { type: Array,  default: () => [] },
  activeDate: { type: String, default: null },
  targetDate: { type: String, default: null },
})

const emit = defineEmits(['update:targetDate'])

function todayStr() {
  // Local date — toISOString() returns UTC and flips a day ahead in ET evenings.
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// Parse 'YYYY-MM-DD' as LOCAL date (avoid TZ shift from new Date('2026-05-19'))
function parseLocalDate(s) {
  if (!s) return null
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

const today = todayStr()

const pills = computed(() => {
  return (props.dates || []).map((dateStr) => {
    const d = parseLocalDate(dateStr)
    const isToday    = dateStr === today
    const isActive   = dateStr === props.activeDate

    // Day-of-week relative label: TODAY / TMRW / WED
    let topLabel
    const diff = Math.round((parseLocalDate(dateStr) - parseLocalDate(today)) / 86400000)
    if (diff === 0) topLabel = 'TODAY'
    else if (diff === 1) topLabel = 'TMRW'
    else topLabel = d.toLocaleDateString('en-US', { weekday: 'short' }).toUpperCase()

    const bottomLabel = d.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric' })

    return {
      dateStr,
      topLabel,
      bottomLabel,
      isToday,
      isActive,
    }
  })
})

function selectDate(dateStr) {
  if (dateStr === props.activeDate) return
  emit('update:targetDate', dateStr)
}
</script>

<template>
  <div class="date-nav">
    <div class="flex items-center gap-2 mb-2">
      <span class="label-bracket text-fg-500">slate dates</span>
    </div>

    <!-- Scrollable rail for many dates; on narrow screens this slides horizontally -->
    <div class="date-rail">
      <button
        v-for="pill in pills"
        :key="pill.dateStr"
        type="button"
        @click="selectDate(pill.dateStr)"
        class="date-pill"
        :class="{
          'date-pill--active': pill.isActive,
        }"
      >
        <span class="date-pill__top">
          {{ pill.topLabel }}
        </span>
        <span class="date-pill__bottom">{{ pill.bottomLabel }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.date-nav {
  /* Block-level container so it integrates into the header flow */
}

.date-rail {
  display: flex;
  align-items: stretch;
  gap: 0.5rem;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;          /* Firefox */
  -ms-overflow-style: none;       /* IE/Edge legacy */
  padding-bottom: 2px;            /* room for focus rings */
}
.date-rail::-webkit-scrollbar {
  display: none;                  /* Webkit */
}

.date-pill {
  position: relative;
  flex: 0 0 auto;
  min-width: 64px;
  padding: 6px 12px 7px;
  border: 1px solid var(--bg-200, #1c1c20);
  background: var(--bg-50, #0c0c0e);
  color: var(--fg-500, #8a8a92);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  text-align: center;
  line-height: 1.15;
  transition: border-color 120ms ease, background-color 120ms ease, color 120ms ease;
  cursor: pointer;
}
.date-pill:hover {
  border-color: var(--bg-300, #26262c);
  color: var(--fg-700, #c0c0c8);
}
.date-pill:focus-visible {
  outline: none;
  border-color: #FF2A2A;
  box-shadow: 0 0 0 1px rgba(255, 42, 42, 0.35);
}

.date-pill__top {
  display: block;
  font-size: 9px;
  letter-spacing: 0.12em;
  opacity: 0.85;
  margin-bottom: 2px;
}
.date-pill__bottom {
  display: block;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.date-pill--active {
  border-color: #FF2A2A;
  background: rgba(255, 42, 42, 0.10);
  color: #FF6B6B;
}
.date-pill--active .date-pill__top {
  opacity: 1;
}

@media (max-width: 640px) {
  .date-pill {
    min-width: 58px;
    padding: 5px 10px 6px;
  }
  .date-pill__bottom { font-size: 12px; }
}
</style>
