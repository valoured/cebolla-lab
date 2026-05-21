// router.js — Cebolla routes
//
// All view files lazy-loaded. Title set per-route for SEO + browser tab.
// Legal routes (/about, /terms, /privacy, /disclaimer) are required for
// the footer links to resolve cleanly and avoid 404s.
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  // ── Core MLB analytics routes ──
  {
    path: '/',
    name: 'slate',
    component: () => import('./views/SlateView.vue'),
    meta: { title: 'Slate · Cebolla' },
  },
  {
    path: '/pod',
    name: 'pod',
    component: () => import('./views/PODView.vue'),
    meta: { title: 'Projection of the Day · Cebolla' },
  },
  {
    path: '/cards',
    name: 'cards',
    component: () => import('./views/CardsView.vue'),
    meta: { title: 'Daily Parlay Analysis · Cebolla' },
  },
  {
    path: '/matchups',
    name: 'matchups',
    component: () => import('./views/MatchupsView.vue'),
    meta: { title: 'Matchups · Cebolla' },
  },
  {
    path: '/stats',
    name: 'stats',
    component: () => import('./views/StatsView.vue'),
    meta: { title: 'Stats &amp; Studies · Cebolla' },
  },
  {
    path: '/methodology',
    name: 'methodology',
    component: () => import('./views/MethodologyView.vue'),
    meta: { title: 'Methodology · Cebolla' },
  },
  {
    path: '/trends',
    name: 'trends',
    component: () => import('./views/TrendsView.vue'),
    meta: { title: 'Trends · Cebolla' },
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
    meta: { title: 'Player Report · Cebolla' },
  },
  {
    path: '/bets',
    name: 'bets',
    component: () => import('./views/BetTrackerView.vue'),
    meta: { title: 'Activity Log · Cebolla' },
  },

  // ── Legal / informational routes ──
  // These MUST exist or footer links will 404. Each renders a static
  // page mirroring its master markdown in /legal at the repo root.
  {
    path: '/about',
    name: 'about',
    component: () => import('./views/AboutView.vue'),
    meta: { title: 'About · Cebolla' },
  },
  {
    path: '/terms',
    name: 'terms',
    component: () => import('./views/TermsView.vue'),
    meta: { title: 'Terms of Service · Cebolla' },
  },
  {
    path: '/privacy',
    name: 'privacy',
    component: () => import('./views/PrivacyView.vue'),
    meta: { title: 'Privacy Policy · Cebolla' },
  },
  {
    path: '/disclaimer',
    name: 'disclaimer',
    component: () => import('./views/DisclaimerView.vue'),
    meta: { title: 'Disclaimer · Cebolla' },
  },

  // ── Fallback ──
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

// Set the document title on each route navigation
router.afterEach((to) => {
  if (to.meta?.title) document.title = to.meta.title
})

export default router
