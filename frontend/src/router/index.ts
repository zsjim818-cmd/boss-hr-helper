import { createRouter, createWebHistory } from 'vue-router'
import JobPolicyView from '@/views/JobPolicyView.vue'
import ScannerView from '@/views/ScannerView.vue'
import ReviewBoardView from '@/views/ReviewBoardView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/review' },
    { path: '/policy', name: 'JobPolicy', component: JobPolicyView, meta: { title: '岗位策略编译与发布' } },
    { path: '/scanner', name: 'Scanner', component: ScannerView, meta: { title: '两级漏斗采集控制台' } },
    { path: '/review', name: 'ReviewBoard', component: ReviewBoardView, meta: { title: 'HR 候选人核验看板' } }
  ]
})

export default router