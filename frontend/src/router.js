import { createRouter, createWebHistory } from 'vue-router'
import Home from './pages/Home.vue'
import Expects from './pages/Expects.vue'
import Receives from './pages/Receives.vue'
import Confirms from './pages/Confirms.vue'
import Reconcile from './pages/Reconcile.vue'
import Settings from './pages/Settings.vue'
import IngestEvents from './pages/IngestEvents.vue'
import Quarantine from './pages/Quarantine.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/expects', component: Expects },
    { path: '/receives', component: Receives },
    { path: '/confirms', component: Confirms },
    { path: '/reconcile', component: Reconcile },
    { path: '/ingest-events', component: IngestEvents },
    { path: '/quarantine', component: Quarantine },
    { path: '/settings', component: Settings },
  ],
})
