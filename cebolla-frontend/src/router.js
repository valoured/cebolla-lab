// router.js — Cebolla Lab routes
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'slate',
    component: () => import('./views/SlateView.vue'),
    meta: { title: 'Slate · Cebolla Lab' },
  },
  {
    path: '/game/:gameId',
    name: 'hr-report',
    component: () => import('./views/HRReportView.vue'),
    props: true,
    meta: { title: 'HR Report · Cebolla Lab' },
  },
  {
    path: '/player/:playerId',
    name: 'player',
    component: () => import('./views/PlayerView.vue'),
    props: true,
    meta: { title: 'Player · Cebolla Lab' },
  },
  {
    path: '/bets',
    name: 'bets',
    component: () => import('./views/BetTrackerView.vue'),
    meta: { title: 'Bet Log · Cebolla Lab' },
  },
  // Fallback → slate
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

// Set the document title on each route
router.afterEach((to) => {
  if (to.meta?.title) document.title = to.meta.title
})

export default router
