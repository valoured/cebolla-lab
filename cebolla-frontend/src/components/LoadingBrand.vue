<script setup>
/**
 * LoadingBrand.vue — Brand loading indicator
 *
 * Cebolla wordmark that animates from grayscale-dim to full-color-glow on a
 * slow loop. Loading text appears below.
 *
 * Used anywhere we're waiting for data:
 *   - Slate loading
 *   - Player Deep Dive loading
 *   - HR Report loading
 *
 * Props:
 *   text         — loading text shown beneath wordmark (default: "Peeling layers…")
 *   size         — 'sm' | 'md' | 'lg'  controls wordmark width (default 'md')
 *
 * Usage:
 *   <LoadingBrand text="Loading player…" />
 *   <LoadingBrand text="Loading matchup…" size="lg" />
 */
defineProps({
  text: {
    type: String,
    default: 'Peeling layers…',
  },
  size: {
    type: String,
    default: 'md',  // 'sm' | 'md' | 'lg'
    validator: v => ['sm', 'md', 'lg'].includes(v),
  },
})
</script>

<template>
  <div class="text-center py-20" :class="`size-${size}`">
    <img
      src="/cebolla-wordmark.png"
      alt="Cebolla"
      class="brand-loading-wordmark mx-auto mb-5"
    />
    <div class="inline-flex items-center gap-3 text-fg-500">
      <span class="w-2 h-2 bg-signal-400 brand-loading-dot"></span>
      <span class="display-text text-lg italic">{{ text }}</span>
    </div>
  </div>
</template>

<style scoped>
.brand-loading-wordmark {
  display: block;
  width: clamp(220px, 50%, 360px);
  height: auto;
  /* Start dim and gray, then animate to full color */
  animation: brand-power-up 2.8s ease-in-out infinite;
  user-select: none;
  -webkit-user-drag: none;
}

.size-sm .brand-loading-wordmark { width: clamp(160px, 35%, 240px); }
.size-md .brand-loading-wordmark { width: clamp(220px, 50%, 360px); }
.size-lg .brand-loading-wordmark { width: clamp(280px, 60%, 440px); }

/* The dot has its own pulse, slightly faster, so it feels alive
   even when the wordmark is in its dim phase */
.brand-loading-dot {
  animation: dot-blink 1.2s ease-in-out infinite;
}

@keyframes brand-power-up {
  0%, 100% {
    /* Dim, desaturated state */
    filter: grayscale(0.95) brightness(0.45)
            drop-shadow(0 0 0 rgba(255, 42, 42, 0));
    opacity: 0.65;
    transform: scale(0.99);
  }
  50% {
    /* Full color, glowing state */
    filter: grayscale(0) brightness(1)
            drop-shadow(0 0 16px rgba(255, 42, 42, 0.55));
    opacity: 1;
    transform: scale(1.01);
  }
}

@keyframes dot-blink {
  0%, 100% {
    opacity: 0.35;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
    box-shadow: 0 0 6px rgba(255, 42, 42, 0.7);
  }
}
</style>
