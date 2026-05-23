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
 * The popup is rendered via <Teleport to="body"> with position: fixed so
 * it escapes any parent that uses overflow / sticky / transform (which all
 * create clipping or stacking contexts that would otherwise truncate the
 * popup — notably the .overflow-x-auto wrapper around BatterTable).
 *
 * Closes on click-outside, Escape, scroll, or window resize.
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
const popupPosition = ref('top')  // resolved position: 'top' | 'bottom'

// Pixel coordinates (viewport-relative, for position: fixed)
const popupLeft = ref(0)       // left edge of popup in viewport px
const popupTop = ref(0)        // top edge of popup in viewport px
const arrowOffsetX = ref(0)    // px from popup's left edge to where the arrow points

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

// Compute viewport-relative coordinates for the popup based on the button's
// current rect. Called after open (once popup is mounted and measurable)
// and is cheap enough to also call on resize if we ever wanted reposition.
function positionPopup() {
  if (!buttonRef.value || !popupRef.value) return

  const margin = 8
  const gap = 8  // space between button and popup
  const buttonRect = buttonRef.value.getBoundingClientRect()
  const popupRect = popupRef.value.getBoundingClientRect()
  const viewportW = window.innerWidth
  const viewportH = window.innerHeight

  // Vertical placement
  let placement
  if (props.position !== 'auto') {
    placement = props.position
  } else {
    placement = buttonRect.top < viewportH / 2 ? 'bottom' : 'top'
  }
  popupPosition.value = placement

  // Top coordinate
  if (placement === 'top') {
    popupTop.value = buttonRect.top - popupRect.height - gap
  } else {
    popupTop.value = buttonRect.bottom + gap
  }

  // Horizontal: center on button, then clamp to viewport
  const buttonCenterX = buttonRect.left + buttonRect.width / 2
  let left = buttonCenterX - popupRect.width / 2

  if (left < margin) {
    left = margin
  } else if (left + popupRect.width > viewportW - margin) {
    left = viewportW - margin - popupRect.width
  }
  popupLeft.value = left

  // Arrow: pointing at the button's center, expressed as offset from
  // popup's left edge. Clamped so the arrow doesn't escape the popup.
  let arrow = buttonCenterX - left
  const arrowMin = 12
  const arrowMax = popupRect.width - 12
  if (arrow < arrowMin) arrow = arrowMin
  if (arrow > arrowMax) arrow = arrowMax
  arrowOffsetX.value = arrow
}

function open() {
  isOpen.value = true
  nextTick(() => {
    positionPopup()
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

// Close on scroll/resize. Repositioning while open would be possible but
// adds edge cases (popup following you across the page is jarring on
// fast scrolls). Closing is simpler and matches typical tooltip UX.
function handleScroll() {
  if (isOpen.value) close()
}
function handleResize() {
  if (isOpen.value) close()
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('keydown', handleEscape)
  // `capture: true` catches scroll events from any scrollable ancestor
  // (the table's overflow-x-auto, etc), not just window scroll.
  window.addEventListener('scroll', handleScroll, { capture: true, passive: true })
  window.addEventListener('resize', handleResize)
})
onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('keydown', handleEscape)
  window.removeEventListener('scroll', handleScroll, { capture: true })
  window.removeEventListener('resize', handleResize)
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

    <!--
      Teleport popup to <body> so it escapes any overflow/transform/sticky
      stacking context. Positioned with position: fixed in viewport coords.
    -->
    <Teleport to="body">
      <transition name="tt-fade">
        <div
          v-if="isOpen"
          ref="popupRef"
          class="tt-popup"
          :class="popupPosition === 'top' ? 'tt-popup-top' : 'tt-popup-bottom'"
          :style="{
            left: popupLeft + 'px',
            top: popupTop + 'px',
            '--tt-arrow-x': arrowOffsetX + 'px',
          }"
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
    </Teleport>
  </span>
</template>

<!--
  IMPORTANT: popup styles are NOT scoped because the popup is teleported
  to <body> — scoped style attributes would not match the moved element.
  Icon styles stay scoped because the icon stays inside the component.
-->
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
  position: relative;  /* anchor for the tap-area pseudo */
}
/* Invisible tap-area expander for touch devices. The icon itself stays
   small (14-18px) for visual density, but the tap region grows to ~30px
   so thumbs can hit it reliably. Doesn't affect layout or hover states
   on desktop — pointer-events only matters during actual taps. */
.info-icon::before {
  content: '';
  position: absolute;
  inset: -8px;
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
</style>

<style>
/* Unscoped — popup is teleported to <body>, so scoped attrs wouldn't match.
   Class names are tt-* and scoped enough by convention to avoid collisions. */
.tt-popup {
  position: fixed;
  min-width: 220px;
  max-width: 280px;
  background: rgba(8, 8, 10, 0.97);
  border: 1px solid rgba(255, 42, 42, 0.30);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6),
              0 0 0 1px rgba(255, 42, 42, 0.06);
  padding: 10px 12px;
  z-index: 9999;
  text-align: left;
  pointer-events: auto;
  backdrop-filter: blur(8px);
}

/* Mobile: cap popup width with a small viewport margin */
@media (max-width: 480px) {
  .tt-popup {
    min-width: 200px;
    max-width: 92vw;
  }
}

/* Arrow — uses --tt-arrow-x to point at the trigger button.
   The arrow's horizontal position is set by JS in pixel offset from the
   popup's left edge, so it survives the viewport-clamp logic. */
.tt-popup::after {
  content: '';
  position: absolute;
  left: var(--tt-arrow-x, 50%);
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

.tt-popup .tt-label {
  display: flex;
  align-items: baseline;
  margin-bottom: 5px;
}
.tt-popup .tt-description {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.75);
  line-height: 1.45;
  margin: 0 0 6px 0;
}
.tt-popup .tt-guide {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 6px;
  margin-top: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
}
.tt-popup .tt-guide-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 1px 0;
}
.tt-popup .tt-guide-label {
  color: rgba(255, 255, 255, 0.55);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 9px;
}
.tt-popup .tt-guide-value {
  color: rgba(255, 255, 255, 0.85);
}
.tt-popup .tone-elite  { color: rgba(255, 42, 42, 1); }
.tt-popup .tone-good   { color: rgba(255, 100, 100, 0.9); }
.tt-popup .tone-avg    { color: rgba(255, 255, 255, 0.6); }
.tt-popup .tone-poor   { color: rgba(95, 165, 255, 0.85); }

.tt-popup .tt-note {
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
.tt-popup.tt-popup-top.tt-fade-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}
.tt-popup.tt-popup-bottom.tt-fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.tt-fade-leave-to {
  opacity: 0;
}
</style>
