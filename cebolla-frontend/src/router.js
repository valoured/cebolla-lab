import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'slate', component: () => import('./views/SlateView.vue') },
  { path: '/game/:gameId', name: 'hr-report', component: () => import('./views/HRReportView.vue'), props: true },
  { path: '/player/:playerId', name: 'player', component: () => import('./views/PlayerView.vue'), props: true },
  { path: '/bets', name: 'bets', component: () => import('./views/BetTrackerView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
