<script setup>
/**
 * EdgeStatusFilter.vue — multi-select pill row for edge_status.
 *
 * v-model is a Set of selected edge_status keys. Empty Set = show all.
 * longshot_unrated is intentionally absent here — it's governed by the
 * "Show longshots" toggle in WarningsFilter.vue.
 */
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Object, default: () => new Set() }, // Set<string>
})
const emit = defineEmits(['update:modelValue'])

const STATUSES = [
  { key: 'strong_back', label: 'Strong Back' },
  { key: 'lean_back',   label: 'Lean Back' },
  { key: 'flat',        label: 'Flat' },
  { key: 'lean_fade',   label: 'Lean Fade' },
  { key: 'strong_fade', label: 'Strong Fade' },
]

const selected = computed(() => props.modelValue)

function toggle(key) {
  const next = new Set(props.modelValue)
  next.has(key) ? next.delete(key) : next.add(key)
  emit('update:modelValue', next)
}

function clear() {
  emit('update:modelValue', new Set())
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-1">
    <button
      type="button"
      class="edge-pill"
      :class="selected.size === 0 ? 'active' : ''"
      @click="clear"
    >All</button>
    <button
      v-for="s in STATUSES"
      :key="s.key"
      type="button"
      class="edge-pill"
      :class="selected.has(s.key) ? 'active' : ''"
      @click="toggle(s.key)"
    >{{ s.label }}</button>
  </div>
</template>

<style scoped>
.edge-pill {
  font-family: 'IBM Plex Sans', sans-serif;
  font-size: 11px;
  letter-spacing: 0.04em;
  padding: 6px 10px;
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: rgb(155, 155, 168);
  background: transparent;
  border-radius: 2px;
  transition: all 0.15s;
}
.edge-pill:hover {
  color: rgb(232, 232, 238);
  border-color: rgba(255, 255, 255, 0.25);
}
.edge-pill.active {
  color: #FF2A2A;
  border-color: #FF2A2A;
  background: rgba(255, 42, 42, 0.08);
}
</style>
