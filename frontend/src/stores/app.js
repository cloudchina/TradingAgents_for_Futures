import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { systemApi, dataApi, analysisApi, scheduledApi, llmApi } from '@/api'

export const useAppStore = defineStore('app', () => {
  // 系统状态
  const systemStatus = ref({
    bailian_api_configured: false,
    serper_api_configured: false,
    docx_available: false,
    cache_count: 0,
    scheduler_running: false,
  })

  // 数据状态
  const dataStatus = ref({
    modules: {},
    summary: { total_modules: 0, success_modules: 0, common_commodities: [] },
  })

  // 分析状态
  const analysisStatus = ref({
    has_active_task: false,
    status: 'idle',
    progress: null,
    results: {},
    queue_length: 0,
  })

  // 缓存列表
  const cacheList = ref([])

  // LLM 运行时配置（前端「LLM 配置」页面维护，保存后立即生效）
  const llmConfig = ref({
    base_url: '',
    api_key: '',
    api_key_masked: '',
    model: '',
    configured: false,
    source: 'env',
    config_path: '',
    updated_at: '',
  })

  // 定时分析配置
  const scheduledConfig = ref({
    enabled: false,
    schedule_time: '20:00',
    commodities: [],
    analysis_modules: ['inventory', 'positioning', 'term_structure', 'technical', 'basis', 'news'],
    analysis_mode: 'complete_flow',
    debate_rounds: 3,
    ai_model: 'qwen-plus',
    auto_email: false,
    auto_word: false,
    update_data_before_analysis: true,
  })

  // 加载状态
  const loading = ref(false)

  // 计算属性
  const availableCommodities = computed(() => dataStatus.value.summary.common_commodities || [])
  // 全局默认模型：LLM 配置页未设置时兜底 qwen-plus
  const defaultModel = computed(() => llmConfig.value.model || 'qwen-plus')
  const isAnalysisRunning = computed(() => analysisStatus.value.has_active_task && analysisStatus.value.status === 'running')

  // Actions
  async function fetchSystemStatus() {
    try {
      systemStatus.value = await systemApi.getStatus()
    } catch (e) {
      console.error('获取系统状态失败', e)
    }
  }

  async function fetchDataStatus() {
    loading.value = true
    try {
      dataStatus.value = await dataApi.getStatus()
    } catch (e) {
      console.error('获取数据状态失败', e)
    } finally {
      loading.value = false
    }
  }

  async function fetchAnalysisStatus() {
    try {
      analysisStatus.value = await analysisApi.getStatus()
    } catch (e) {
      console.error('获取分析状态失败', e)
    }
  }

  async function fetchCacheList() {
    try {
      cacheList.value = await analysisApi.listCache()
    } catch (e) {
      console.error('获取缓存列表失败', e)
    }
  }

  // LLM 配置（读写 + 连通性测试）
  async function fetchLlmConfig() {
    try {
      llmConfig.value = await llmApi.getConfig()
      return llmConfig.value
    } catch (e) {
      console.error('获取 LLM 配置失败', e)
      throw e
    }
  }

  async function saveLlmConfig(config) {
    const res = await llmApi.saveConfig(config)
    if (res?.config) llmConfig.value = res.config
    await fetchSystemStatus() // 右上角/系统状态里的 LLM 可用状态随之更新
    return res
  }

  async function testLlmConfig(config) {
    return await llmApi.test(config)
  }

  async function fetchLlmModels(config) {
    return await llmApi.listModels(config)
  }

  async function resetLlmConfig() {
    const res = await llmApi.reset()
    if (res?.config) llmConfig.value = res.config
    await fetchSystemStatus()
    return res
  }

  async function fetchScheduledConfig() {
    try {
      scheduledConfig.value = await scheduledApi.getConfig()
    } catch (e) {
      console.error('获取定时配置失败', e)
    }
  }

  async function updateData(moduleKey, params = {}) {
    loading.value = true
    try {
      const result = await dataApi.updateData(moduleKey, params)
      return result
    } finally {
      loading.value = false
    }
  }

  async function submitAnalysis(config) {
    try {
      const task = await analysisApi.submit(config)
      await fetchAnalysisStatus()
      return task
    } catch (e) {
      throw e
    }
  }

  async function startScheduled(config) {
    try {
      await scheduledApi.start(config)
      await fetchScheduledConfig()
    } catch (e) {
      throw e
    }
  }

  async function stopScheduled() {
    try {
      await scheduledApi.stop()
      await fetchScheduledConfig()
    } catch (e) {
      throw e
    }
  }

  return {
    systemStatus,
    dataStatus,
    analysisStatus,
    cacheList,
    llmConfig,
    scheduledConfig,
    loading,
    availableCommodities,
    isAnalysisRunning,
    defaultModel,
    fetchSystemStatus,
    fetchDataStatus,
    fetchAnalysisStatus,
    fetchCacheList,
    fetchLlmConfig,
    saveLlmConfig,
    testLlmConfig,
    fetchLlmModels,
    resetLlmConfig,
    fetchScheduledConfig,
    updateData,
    submitAnalysis,
    startScheduled,
    stopScheduled,
  }
})
