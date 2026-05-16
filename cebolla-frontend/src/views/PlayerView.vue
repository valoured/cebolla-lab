<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <div class="min-h-screen px-6 py-10">
    <div class="label-bracket text-signal-400 mb-3">module · M.01.c</div>
    <h1 class="display-text text-4xl text-fg-700 mb-2">Player Deep Dive</h1>
    <p class="font-mono text-xs text-fg-500 mb-6">player.{{ route.params.playerId }}</p>
    <p class="text-fg-500 text-sm max-w-xl">
      Pitch arsenal matchup, rolling Barrel% / EV / HardHit% trends, pitch log.
    </p>
    <router-link to="/"
      class="inline-block mt-6 label-caps hover:text-signal-400 transition">
      ← back to slate
    </router-link>
  </div>
</template>
