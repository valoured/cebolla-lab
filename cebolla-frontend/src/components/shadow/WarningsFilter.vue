<script setup>
/**
 * WarningsFilter.vue — warning-flag narrowing toggles + a Show-longshots switch.
 *
 * Each flag pill, when active, NARROWS the table to rows that carry that
 * warning (warnings[key] === true). Multiple active flags = AND.
 *
 * The "Show longshots" toggle is separate: OFF by default (lock #5) hides
 * edge_status === 'longshot_unrated'.
 */
const props = defineProps({
  filters: { type: Object, required: true },        // {weather_fallback, did_not_play, longshot, per_game_high, is_fallback}
  showLongshots: { type: Boolean, default: false },
})
const emit = defineEmits(['update:filters', 'update:showLongshots'])

const FLAGS = [
  { key: 'weather_fallback', label: 'Wx Fallback' },
  { key: 'did_not_play',     label: 'DNP' },
  { key: 'longshot',         label: 'Longshot' },
  { key: 'per_game_high',    label: 'High P%' },
  { key: 'is_fallback',      label: 'Pitcher Fallback' },
]

function toggleFlag(key) {
  emit('update:filters', { ...props.filters, [key]: !props.filters[key] })
}
function toggleLongshots() {
  emit('update:showLongshots', !props.showLongshots)
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-1">
    <span class="label-bracket text-fg-500 mr-1">warnings</span>
    <button
      v-for="f in FLAGS"
      :key="f.key"
      type="button"
      class="warn-pill"
      :class="filters[f.key] ? 'active' : ''"
      @click="toggleFlag(f.key)"
    >{{ f.label }}</button>

    <span class="mx-2 h-4 w-px bg-bg-200"></span>

    <button
      type="button"
      class="warn-pill longshot-toggle"
      :class="showLongshots ? 'active' : ''"
      @click="toggleLongshots"
    >{{ showLongshots ? 'Longshots: shown' : 'Longshots: hidden' }}</button>
  </div>
</template>

<style scoped>
.warn-pill {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 5px 9px;
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: rgb(155, 155, 168);
  background: transparent;
  border-radius: 2px;
  transition: all 0.15s;
}
.warn-pill:hover {
  color: rgb(232, 232, 238);
  border-color: rgba(255, 255, 255, 0.25);
}
.warn-pill.active {
  color: #FF2A2A;
  border-color: #FF2A2A;
  background: rgba(255, 42, 42, 0.08);
}
.longshot-toggle.active {
  /* shown state uses lab-teal complement to read as "expanded view", not a filter */
  color: #5F9EA0;
  border-color: #5F9EA0;
  background: rgba(95, 158, 160, 0.10);
}
</style>
