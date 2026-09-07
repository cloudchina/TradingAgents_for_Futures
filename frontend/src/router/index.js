import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/data',
  },
  {
    path: '/data',
    name: 'DataManagement',
    component: () => import('@/views/DataManagement.vue'),
    meta: { title: '数据管理' },
  },
  {
    path: '/update',
    name: 'DataUpdate',
    component: () => import('@/views/DataUpdate.vue'),
    meta: { title: '数据更新' },
  },
  {
    path: '/analysis',
    name: 'AnalysisConfig',
    component: () => import('@/views/AnalysisConfig.vue'),
    meta: { title: '分析配置' },
  },
  {
    path: '/results',
    name: 'AnalysisResults',
    component: () => import('@/views/AnalysisResults.vue'),
    meta: { title: '分析结果' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  document.title = `${to.meta.title || '首页'} - 期货Trading Agents`
  next()
})

export default router
