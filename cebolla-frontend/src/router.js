// router.js — Cebolla Lab routes
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'slate',
    component: () => import('./views/SlateView.vue'),
    meta: { title: 'Slate · Cebolla' },
  },
  {
    path: '/game/:gameId',
    name: 'hr-report',
    component: () => import('./views/HRReportView.vue'),
    props: true,
    meta: { title: 'Game Report · Cebolla' },
  },
  {
    path: '/player/:playerId',
    name: 'player',
    component: () => import('./views/PlayerView.vue'),
    props: true,
    meta: { title: 'Player · Cebolla' },
  },
  {
    path: '/pod',
    name: 'pod',
    component: () => import('./views/PODView.vue'),
    meta: { title: 'Play of the Day · Cebolla' },
  },
  {
    path: '/cards',
    name: 'cards',
    component: () => import('./views/CardsView.vue'),
    meta: { title: 'Cebolla Cards · Cebolla' },
  },
  {
    path: '/stats',
    name: 'stats',
    component: () => import('./views/StatsView.vue'),
    meta: { title: 'Stats & Studies · Cebolla' },
  },
  {
    path: '/methodology',
    name: 'methodology',
    component: () => import('./views/MethodologyView.vue'),
    meta: { title: 'Methodology · Cebolla' },
  },
  {
    path: '/bets',
    name: 'bets',
    component: () => import('./views/BetTrackerView.vue'),
    meta: { title: 'Bet Log · Cebolla' },
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
