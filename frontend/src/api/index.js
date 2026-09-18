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
  // 🔧 修复(C1)：数据更新已任务化——提交后立即返回 task_id，通过 getDataTask 轮询
  updateData: (moduleKey, params = {}) =>
    api.post(`/data/update/${moduleKey}`, null, { params }),
  listDataTasks: () => api.get('/data/tasks'),
  getDataTask: (taskId) => api.get(`/data/tasks/${taskId}`),
  // 【三评 G】全品种批量更新：按品种并发 + 断点续传 + 失败重试（同样是任务化）
  bulkUpdate: (params = {}) => api.post('/data/bulk-update', null, { params }),
}

// ====== LLM 配置 API（运行时热更新，保存后无需重启） ======
export const llmApi = {
  getConfig: () => api.get('/llm/config'),
  saveConfig: (data) => api.put('/llm/config', data),
  test: (data) => api.post('/llm/test', data),
  listModels: (data) => api.post('/llm/models', data),
  reset: () => api.post('/llm/reset'),
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

// ====== 记忆体系 API（阶段0/2/4/5） ======
export const memoryApi = {
  health: () => api.get('/memory/health'),
  init: () => api.post('/memory/init'),
  // 数据可用性（阶段0）
  getAvailability: () => api.get('/memory/availability'),
  refreshAvailability: () => api.post('/memory/availability/refresh'),
  // 复盘回填 / 巩固 / 统计（阶段4）
  backfill: (symbols) =>
    api.post('/memory/backfill', null, { params: symbols ? { symbols } : {} }),
  consolidate: (symbol) =>
    api.post('/memory/consolidate', null, { params: symbol ? { symbol } : {} }),
  getStats: (symbol) => api.get('/memory/stats', { params: symbol ? { symbol } : {} }),
  // 品种记忆详情（阶段2+5）
  getSymbol: (symbol) => api.get(`/memory/symbols/${symbol}`),
  getRelations: () => api.get('/memory/relations'),
  refreshRelations: (symbols) =>
    api.post('/memory/relations/refresh', null, { params: symbols ? { symbols } : {} }),
  // 人工记忆
  addNote: (data) => api.post('/memory/note', data),
  listNotes: (symbol) => api.get('/memory/notes', { params: symbol ? { symbol } : {} }),
  deleteNote: (id) => api.delete(`/memory/notes/${id}`),
  // 语义记忆人工覆盖（决策3）
  reviewSemantic: (id, action, reviewer = 'human') =>
    api.post(`/memory/semantics/${id}/review`, { action, reviewer }),
}

// ====== 定时分析 API ======
// 🔧 清理(P2)：移除无调用封装 getStatus（后端亦无 /scheduled/status 路由）
export const scheduledApi = {
  getConfig: () => api.get('/scheduled/config'),
  start: (config) => api.post('/scheduled/start', config),
  stop: () => api.post('/scheduled/stop'),
}

export default api
