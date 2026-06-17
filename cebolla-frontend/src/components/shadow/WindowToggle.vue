<script setup>
/**
 * WindowToggle.vue — Shadow Lab calibration window selector.
 *
 * Pill row: Today / L7 / L30 / All. Mirrors StatcastWindowToggle.vue.
 * Emits 'update:modelValue' with the new window key; ShadowLab routes that
 * to useShadowLab.setCalibWindow (re-fetches the calibration set only).
 */

defineProps({
  modelValue: { type: String, default: 'today' },
})
defineEmits(['update:modelValue'])

const WINDOWS = [
  { key: 'today', label: 'Today' },
  { key: 'l7',    label: 'L7' },
  { key: 'l30',   label: 'L30' },
  { key: 'all',   label: 'All' },
]
</script>

<template>
  <div class="inline-flex items-center gap-0 border border-bg-200 bg-bg-50">
    <button
      v-for="w in WINDOWS"
      :key="w.key"
      type="button"
      @click="$emit('update:modelValue', w.key)"
      class="window-toggle-btn px-2.5 py-1 text-[10px] font-mono uppercase tracking-wide2 transition-all"
      :class="modelValue === w.key
        ? 'bg-signal-400/15 text-signal-400 font-semibold'
        : 'text-fg-500 hover:text-fg-700 hover:bg-bg-100'"
    >
      {{ w.label }}
    </button>
  </div>
</template>

<style scoped>
.window-toggle-btn {
  min-height: 36px;
  min-width: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
</style>
