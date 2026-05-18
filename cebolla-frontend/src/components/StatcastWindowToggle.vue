<script setup>
/**
 * StatcastWindowToggle.vue
 *
 * Compact pill row for switching Statcast windows: Season / L30 / L14 / L7.
 * Phase 7. Used in BatterTable and pitcher matchup section.
 *
 * Emits 'update:modelValue' with the new window string.
 */

defineProps({
  modelValue: { type: String, default: 'l14' },
})
defineEmits(['update:modelValue'])

const WINDOWS = [
  { key: 'season', label: 'Season' },
  { key: 'l30',    label: 'L30' },
  { key: 'l14',    label: 'L14' },
  { key: 'l7',     label: 'L7' },
]
</script>

<template>
  <div class="inline-flex items-center gap-0 border border-bg-200 bg-bg-50">
    <button
      v-for="w in WINDOWS"
      :key="w.key"
      type="button"
      @click="$emit('update:modelValue', w.key)"
      class="px-2.5 py-1 text-[10px] font-mono uppercase tracking-wide2 transition-all"
      :class="modelValue === w.key
        ? 'bg-signal-400/15 text-signal-400 font-semibold'
        : 'text-fg-500 hover:text-fg-700 hover:bg-bg-100'"
    >
      {{ w.label }}
    </button>
  </div>
</template>
