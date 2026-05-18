<script setup>
/**
 * InfoTooltip.vue
 *
 * A small "?" icon button that opens a glossary popup on click (mobile)
 * or hover (desktop). Used throughout Cebolla to explain Statcast/betting
 * terms without cluttering the UI.
 *
 * Usage:
 *   <InfoTooltip term="barrel_pct" />
 *   <InfoTooltip term="edge" />
 *
 * Or with custom content:
 *   <InfoTooltip>
 *     <template #content>
 *       <div>Custom HTML here</div>
 *     </template>
 *   </InfoTooltip>
 *
 * Auto-positions above the icon. Closes on click-outside.
 */

import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { glossary } from '../utils/glossary.js'

const props = defineProps({
  term:      { type: String,  default: null },
  position:  { type: String,  default: 'auto' }, // 'top' | 'bottom' | 'auto'
  size:      { type: String,  default: 'sm' },   // 'sm' | 'md' (icon size)
})

const isOpen = ref(false)
const popupRef = ref(null)
const buttonRef = ref(null)
const popupPosition = ref('top') // resolved position

// Touch-device detection. On touch devices, mouseenter/mouseleave events
// fire alongside click events from a single tap, causing the popup to
// open-close-open in a "twitch" loop. We disable hover handlers entirely
// on touch and rely purely on tap-to-toggle + tap-outside-to-close.
const isTouchDevice = ref(false)
onMounted(() => {
  isTouchDevice.value =
    'ontouchstart' in window ||
    (navigator.maxTouchPoints && navigator.maxTouchPoints > 0)
})

const entry = computed(() => {
  if (!props.term) return null
  return glossary[props.term] || null
})

function open() {
  isOpen.value = true
  nextTick(() => {
    if (props.position !== 'auto') {
      popupPosition.value = props.position
      return
    }
    // Auto-position: if button is in top half of viewport, put popup below;
    // otherwise above. Prevents tooltips from being cut off at top of screen.
    if (buttonRef.value) {
      const rect = buttonRef.value.getBoundingClientRect()
      const viewportHeight = window.innerHeight
      popupPosition.value = rect.top < viewportHeight / 2 ? 'bottom' : 'top'
    }
  })
}

function close() {
  isOpen.value = false
}

function toggle() {
  isOpen.value ? close() : open()
}

// Hover handlers — desktop only, disabled on touch devices
function handleHoverOpen() {
  if (isTouchDevice.value) return
  open()
}
function handleHoverClose() {
  if (isTouchDevice.value) return
  close()
}

function handleClickOutside(e) {
  if (!isOpen.value) return
  if (popupRef.value?.contains(e.target)) return
  if (buttonRef.value?.contains(e.target)) return
  close()
}

function handleEscape(e) {
  if (e.key === 'Escape' && isOpen.value) close()
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('keydown', handleEscape)
})
onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('keydown', handleEscape)
})
</script>

<template>
  <span class="relative inline-flex items-baseline" :class="{ 'tt-open': isOpen }">
    <button
      ref="buttonRef"
      type="button"
      @click.stop="toggle"
      @mouseenter="handleHoverOpen"
      @mouseleave="handleHoverClose"
      class="info-icon"
      :class="size === 'md' ? 'info-icon-md' : 'info-icon-sm'"
      :aria-label="entry?.label ? `What is ${entry.label}?` : 'More info'"
    >?</button>

    <transition name="tt-fade">
      <div
        v-if="isOpen"
        ref="popupRef"
        class="tt-popup"
        :class="popupPosition === 'top' ? 'tt-popup-top' : 'tt-popup-bottom'"
        @click.stop
        @mouseenter="handleHoverOpen"
        @mouseleave="handleHoverClose"
      >
        <slot name="content">
          <template v-if="entry">
            <div class="tt-label">
              <span class="label-bracket text-signal-400">{{ entry.label }}</span>
              <span v-if="entry.unit" class="label-caps !text-[8px] ml-2 opacity-60">{{ entry.unit }}</span>
            </div>
            <p class="tt-description">{{ entry.description }}</p>
            <div v-if="entry.guide" class="tt-guide">
              <div v-for="(item, i) in entry.guide" :key="i" class="tt-guide-row">
                <span class="tt-guide-label" :class="item.tone">{{ item.label }}</span>
                <span class="tt-guide-value">{{ item.value }}</span>
              </div>
            </div>
            <div v-if="entry.note" class="tt-note">{{ entry.note }}</div>
          </template>
          <template v-else>
            <div class="text-fg-500 text-xs">No info available.</div>
          </template>
        </slot>
      </div>
    </transition>
  </span>
</template>

<style scoped>
.info-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.03);
  color: rgba(255, 255, 255, 0.5);
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  user-select: none;
}
.info-icon-sm {
  width: 14px;
  height: 14px;
  font-size: 9px;
  margin-left: 4px;
}
.info-icon-md {
  width: 18px;
  height: 18px;
  font-size: 10px;
  margin-left: 5px;
}
.info-icon:hover,
.tt-open .info-icon {
  background: rgba(255, 42, 42, 0.12);
  border-color: rgba(255, 42, 42, 0.4);
  color: rgba(255, 42, 42, 0.95);
}

.tt-popup {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  min-width: 220px;
  max-width: 280px;
  background: rgba(8, 8, 10, 0.97);
  border: 1px solid rgba(255, 42, 42, 0.30);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6),
              0 0 0 1px rgba(255, 42, 42, 0.06);
  padding: 10px 12px;
  z-index: 60;
  text-align: left;
  pointer-events: auto;
  backdrop-filter: blur(8px);
}
.tt-popup-top {
  bottom: calc(100% + 8px);
}
.tt-popup-bottom {
  top: calc(100% + 8px);
}

/* Mobile: keep popup on-screen by capping at viewport width with margin */
@media (max-width: 480px) {
  .tt-popup {
    min-width: 200px;
    max-width: 92vw;
  }
}

/* Arrow */
.tt-popup::after {
  content: '';
  position: absolute;
  left: 50%;
  transform: translateX(-50%) rotate(45deg);
  width: 8px;
  height: 8px;
  background: rgba(8, 8, 10, 0.97);
  border: 1px solid rgba(255, 42, 42, 0.30);
}
.tt-popup-top::after {
  bottom: -5px;
  border-top: none;
  border-left: none;
}
.tt-popup-bottom::after {
  top: -5px;
  border-bottom: none;
  border-right: none;
}

.tt-label {
  display: flex;
  align-items: baseline;
  margin-bottom: 5px;
}
.tt-description {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.75);
  line-height: 1.45;
  margin: 0 0 6px 0;
}
.tt-guide {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 6px;
  margin-top: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
}
.tt-guide-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 1px 0;
}
.tt-guide-label {
  color: rgba(255, 255, 255, 0.55);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 9px;
}
.tt-guide-value {
  color: rgba(255, 255, 255, 0.85);
}
.tone-elite  { color: rgba(255, 42, 42, 1); }
.tone-good   { color: rgba(255, 100, 100, 0.9); }
.tone-avg    { color: rgba(255, 255, 255, 0.6); }
.tone-poor   { color: rgba(95, 165, 255, 0.85); }

.tt-note {
  font-size: 9px;
  color: rgba(255, 255, 255, 0.45);
  font-style: italic;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  line-height: 1.4;
}

.tt-fade-enter-active,
.tt-fade-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.tt-fade-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(-4px);
}
.tt-popup-bottom.tt-fade-enter-from {
  transform: translateX(-50%) translateY(4px);
}
.tt-fade-leave-to {
  opacity: 0;
}
</style>
