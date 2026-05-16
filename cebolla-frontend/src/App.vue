<script setup>
import TopNav from './components/TopNav.vue'
</script>

<template>
  <div class="min-h-screen flex flex-col">
    <TopNav />
    <main class="flex-1 min-w-0 relative z-10">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<style>
.fade-enter-active, .fade-leave-active {
  transition: opacity 200ms ease;
}
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>