import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
api.interceptors.request.use(
  (config) => config,
  (error) => Promise.reject(error)
)

// 响应拦截器
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const message = error.response?.data?.detail || error.message || '请求失败'
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

// ====== 系统 API ======
// 🔧 清理(P2)：移除无调用封装 getCommodities / health
export const systemApi = {
  getStatus: () => api.get('/system/status'),
}

// ====== 数据管理 API ======
// 🔧 清理(P2)：移除无调用封装 getDominantContracts（主力合约信息已含于 /data/commodities 行内）
export const dataApi = {
  getStatus: () => api.get('/data/status'),
  getCommodities: () => api.get('/data/commodities'),
  refreshDominantContracts: () => api.post('/data/dominant-contracts/refresh'),
  getVarieties: (moduleKey) => api.get(`/data/varieties/${moduleKey}`),
  checkCommodity: (commodity) => api.get(`/data/commodity/${commodity}`),
  updateData: (moduleKey, params = {}) =>
    api.post(`/data/update/${moduleKey}`, null, { params }),
}

// ====== 分析 API ======
// 🔧 清理(P2)：移除无调用封装 getProgress / getResult（结果页统一走 status + cache/list）
export const analysisApi = {
  submit: (data) => api.post('/analysis/submit', data),
  getStatus: () => api.get('/analysis/status'),
  listCache: () => api.get('/analysis/cache/list'),
  loadCache: (commodity, date) => api.post(`/analysis/cache/load/${commodity}/${date}`),
  deleteCache: (commodity, date) => api.delete(`/analysis/cache/${commodity}/${date}`),
  generateWordReport: (params) =>
    api.post('/analysis/word-report', null, {
      params,
      responseType: 'blob',
    }),
}

// ====== 定时分析 API ======
// 🔧 清理(P2)：移除无调用封装 getStatus（后端亦无 /scheduled/status 路由）
export const scheduledApi = {
  getConfig: () => api.get('/scheduled/config'),
  start: (config) => api.post('/scheduled/start', config),
  stop: () => api.post('/scheduled/stop'),
}

export default api
