<template>
  <div class="analysis-config">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- 手动分析 -->
      <el-tab-pane label="📝 手动分析" name="manual">
        <el-form :model="form" label-width="120px" style="max-width: 800px">
          <el-divider content-position="left">📊 基础配置</el-divider>

          <el-form-item label="分析模块">
            <el-checkbox-group v-model="form.modules">
              <el-checkbox-button
                v-for="m in moduleOptions"
                :key="m.value"
                :value="m.value"
                :label="m.value"
              >{{ m.label }}</el-checkbox-button>
            </el-checkbox-group>
          </el-form-item>

          <el-form-item label="分析品种">
            <el-input
              v-model="form.commodityInput"
              placeholder="输入品种代码，多个用空格分隔，如：AU RB I"
              style="width: 400px"
            />
            <div v-if="commodityStatuses.length" style="margin-top: 8px">
              <div v-for="cs in commodityStatuses" :key="cs.commodity" class="commodity-status">
                <strong>{{ cs.commodity }}</strong>
                <el-tag
                  v-for="(has, key) in cs.status"
                  :key="key"
                  :type="has ? 'success' : 'danger'"
                  size="small"
                  style="margin-left: 4px"
                >
                  {{ moduleShortNames[key] }}
                </el-tag>
              </div>
            </div>
          </el-form-item>

          <el-form-item label="分析日期">
            <el-date-picker
              v-model="form.analysisDate"
              type="date"
              value-format="YYYY-MM-DD"
              :disabled-date="disableFuture"
            />
          </el-form-item>

          <el-divider content-position="left">⚙️ 高级配置</el-divider>

          <el-form-item label="分析模式">
            <el-radio-group v-model="form.analysisMode">
              <el-radio value="analyst_only">仅分析师团队</el-radio>
              <el-radio value="complete_flow">完整流程（分析师+辩论+交易员+风控+决策）</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item v-if="form.analysisMode === 'complete_flow'" label="辩论轮数">
            <el-slider v-model="form.debateRounds" :min="1" :max="5" show-input style="width: 300px" />
          </el-form-item>

          <el-form-item label="AI模型">
            <el-select v-model="form.aiModel" style="width: 200px">
              <el-option label="Qwen Plus (推荐)" value="qwen-plus" />
              <el-option label="Qwen Max (最强)" value="qwen-max" />
              <el-option label="Qwen Turbo (快速)" value="qwen-turbo" />
            </el-select>
          </el-form-item>

          <el-form-item label="强制重新分析">
            <el-switch v-model="form.forceRefresh" />
            <span style="margin-left: 8px; color: #909399; font-size: 12px">忽略本地缓存</span>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" size="large" @click="submitAnalysis" :loading="submitting">
              <el-icon><Promotion /></el-icon> 开始分析
            </el-button>
            <el-button @click="resetForm">重置</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 自动定时分析 -->
      <el-tab-pane label="🤖 自动定时分析" name="scheduled">
        <el-form :model="scheduledForm" label-width="140px" style="max-width: 700px">
          <el-form-item label="启用定时分析">
            <el-switch v-model="scheduledForm.enabled" />
          </el-form-item>

          <el-form-item label="分析模块">
            <el-checkbox-group v-model="scheduledForm.analysis_modules">
              <el-checkbox-button
                v-for="m in moduleOptions"
                :key="m.value"
                :value="m.value"
              >{{ m.label }}</el-checkbox-button>
            </el-checkbox-group>
          </el-form-item>

          <el-form-item label="分析品种">
            <el-input
              v-model="scheduledForm.commodityInput"
              placeholder="如：AU RB I"
              style="width: 300px"
            />
          </el-form-item>

          <el-form-item label="定时时间">
            <el-select v-model="scheduledForm.schedule_time" style="width: 150px">
              <el-option v-for="h in 24" :key="h-1" :label="`${String(h-1).padStart(2,'0')}:00`" :value="`${String(h-1).padStart(2,'0')}:00`" />
            </el-select>
          </el-form-item>

          <el-form-item label="分析模式">
            <el-radio-group v-model="scheduledForm.analysis_mode">
              <el-radio value="analyst_only">仅分析师</el-radio>
              <el-radio value="complete_flow">完整流程</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="辩论轮数">
            <el-slider v-model="scheduledForm.debate_rounds" :min="1" :max="5" show-input style="width: 300px" />
          </el-form-item>

          <el-form-item label="AI模型">
            <el-select v-model="scheduledForm.ai_model" style="width: 200px">
              <el-option label="Qwen Plus" value="qwen-plus" />
              <el-option label="Qwen Max" value="qwen-max" />
              <el-option label="Qwen Turbo" value="qwen-turbo" />
            </el-select>
          </el-form-item>

          <el-divider content-position="left">📡 附加选项（无人值守，后台自动执行）</el-divider>

          <el-form-item label="分析前更新数据">
            <el-switch v-model="scheduledForm.update_data_before_analysis" />
            <span style="margin-left: 8px; color: #909399; font-size: 12px">到点先自动更新本地数据再分析</span>
          </el-form-item>
          <el-form-item label="自动发送邮件">
            <el-switch v-model="scheduledForm.auto_email" />
            <span style="margin-left: 8px; color: #909399; font-size: 12px">需配置 SMTP_* 环境变量</span>
          </el-form-item>
          <el-form-item label="自动保存Word">
            <el-switch v-model="scheduledForm.auto_word" />
            <span style="margin-left: 8px; color: #909399; font-size: 12px">保存到 results/auto_reports 目录</span>
          </el-form-item>
          <el-form-item label="独立后台运行">
            <span style="color: #909399; font-size: 12px">
              无人值守场景可使用 <code>backend/run_scheduled_task.py</code> 交由 cron / Windows 任务计划程序定时调用，无需前端页面常驻。
            </span>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" @click="startScheduled" :loading="schedLoading">
              {{ scheduledForm.enabled ? '更新定时配置' : '确认开启定时分析' }}
            </el-button>
            <el-button type="danger" @click="stopScheduled" :disabled="!schedRunning">
              停止定时分析
            </el-button>
          </el-form-item>

          <el-form-item v-if="schedRunning">
            <el-alert type="success" :closable="false">
              🟢 定时分析运行中 — 每天 {{ scheduledForm.schedule_time }}
            </el-alert>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { dataApi } from '@/api'
import { ElMessage } from 'element-plus'

const router = useRouter()
const store = useAppStore()
const activeTab = ref('manual')
const submitting = ref(false)
const schedLoading = ref(false)
const schedRunning = ref(false)
const commodityStatuses = ref([])

const moduleOptions = [
  { value: 'inventory', label: '库存仓单' },
  { value: 'positioning', label: '持仓席位' },
  { value: 'term_structure', label: '期限结构' },
  { value: 'technical', label: '技术面' },
  { value: 'basis', label: '基差' },
  { value: 'news', label: '新闻' },
]

const moduleShortNames = {
  technical: '技术',
  // 🔧 修复：数据状态检查接口(check_commodity_data)返回的是数据模块目录 key
  // “technical_analysis”（技术面指标数据），而非分析模块 key “technical”，
  // 此前缺少该映射导致状态标签显示为空白。
  technical_analysis: '技术',
  basis: '基差',
  inventory: '库存',
  positioning: '持仓',
  term_structure: '期限',
  receipt: '仓单',
  news: '新闻',
}

const form = reactive({
  modules: ['inventory', 'positioning', 'term_structure', 'technical', 'basis', 'news'],
  commodityInput: 'AU',
  analysisDate: new Date(Date.now() - 86400000).toISOString().split('T')[0],
  analysisMode: 'complete_flow',
  debateRounds: 3,
  aiModel: 'qwen-plus',
  forceRefresh: false,
})

const scheduledForm = reactive({
  enabled: false,
  schedule_time: '20:00',
  commodityInput: 'AU',
  analysis_modules: ['inventory', 'positioning', 'term_structure', 'technical', 'basis', 'news'],
  analysis_mode: 'complete_flow',
  debate_rounds: 3,
  ai_model: 'qwen-plus',
  auto_email: false,
  auto_word: false,
  update_data_before_analysis: true,
})

function disableFuture(date) {
  return date.getTime() > Date.now()
}

const parsedCommodities = computed(() => {
  const input = form.commodityInput.trim()
  if (!input) return []
  return input.split(/[\s,]+/).map(c => c.trim().toUpperCase()).filter(Boolean)
})

async function checkCommodityData() {
  commodityStatuses.value = []
  for (const c of parsedCommodities.value) {
    try {
      const status = await dataApi.checkCommodity(c)
      commodityStatuses.value.push({ commodity: c, status })
    } catch (e) {
      // skip
    }
  }
}

import { watch } from 'vue'
watch(() => form.commodityInput, () => {
  if (parsedCommodities.value.length) checkCommodityData()
})

async function submitAnalysis() {
  const commodities = parsedCommodities.value
  if (!commodities.length) {
    ElMessage.warning('请输入至少一个品种')
    return
  }
  if (!form.modules.length) {
    ElMessage.warning('请选择至少一个分析模块')
    return
  }

  submitting.value = true
  try {
    await store.submitAnalysis({
      commodities,
      analysis_date: form.analysisDate,
      modules: form.modules,
      analysis_mode: form.analysisMode,
      ai_model: form.aiModel,
      debate_rounds: form.analysisMode === 'complete_flow' ? form.debateRounds : 0,
      force_refresh: form.forceRefresh,
    })
    ElMessage.success('分析任务已提交！')
    router.push('/results')
  } catch (e) {
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

function resetForm() {
  form.modules = ['inventory', 'positioning', 'term_structure', 'technical', 'basis', 'news']
  form.commodityInput = 'AU'
  form.analysisMode = 'complete_flow'
  form.debateRounds = 3
  form.aiModel = 'qwen-plus'
  form.forceRefresh = false
}

async function startScheduled() {
  const commodities = scheduledForm.commodityInput.trim().split(/[\s,]+/).map(c => c.trim().toUpperCase()).filter(Boolean)
  if (!commodities.length) {
    ElMessage.warning('请输入至少一个品种')
    return
  }
  schedLoading.value = true
  try {
    await store.startScheduled({
      ...scheduledForm,
      commodities,
      enabled: true,
    })
    schedRunning.value = true
    ElMessage.success(`定时分析已开启：每天 ${scheduledForm.schedule_time}`)
  } catch (e) {
    ElMessage.error('开启失败')
  } finally {
    schedLoading.value = false
  }
}

async function stopScheduled() {
  try {
    await store.stopScheduled()
    schedRunning.value = false
    ElMessage.success('定时分析已停止')
  } catch (e) {
    ElMessage.error('停止失败')
  }
}

onMounted(async () => {
  await store.fetchScheduledConfig()
  const cfg = store.scheduledConfig
  if (cfg.commodities?.length) {
    scheduledForm.enabled = cfg.enabled ?? false
    scheduledForm.commodityInput = cfg.commodities.join(' ')
    scheduledForm.schedule_time = cfg.schedule_time || '20:00'
    scheduledForm.analysis_modules = cfg.analysis_modules || scheduledForm.analysis_modules
    scheduledForm.analysis_mode = cfg.analysis_mode || 'complete_flow'
    scheduledForm.debate_rounds = cfg.debate_rounds ?? 3
    scheduledForm.ai_model = cfg.ai_model || 'qwen-plus'
    scheduledForm.auto_email = cfg.auto_email ?? false
    scheduledForm.auto_word = cfg.auto_word ?? false
    // 🔧 修复：回填自动更新数据开关，此前该字段被忽略导致展示与后端配置不一致
    scheduledForm.update_data_before_analysis = cfg.update_data_before_analysis ?? true
    schedRunning.value = cfg.enabled || false
  }
})
</script>

<style scoped>
.commodity-status {
  padding: 8px;
  margin-bottom: 4px;
  background: #f5f7fa;
  border-radius: 4px;
}
</style>
