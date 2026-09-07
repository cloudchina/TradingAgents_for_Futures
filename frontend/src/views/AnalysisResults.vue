<template>
  <div class="analysis-results">
    <!-- 分析进行中 -->
    <el-card v-if="store.isAnalysisRunning" class="progress-card">
      <template #header>
        <div class="card-header">
          <span>🚀 分析进行中</span>
          <el-tag type="warning">运行中</el-tag>
        </div>
      </template>

      <el-progress
        :percentage="progressPercent"
        :status="progressStatus"
        :stroke-width="20"
        text-inside
      />

      <div class="progress-info" v-if="progress">
        <p>当前品种: <strong>{{ progress.current_commodity || '准备中...' }}</strong></p>
        <p v-if="progress.current_module">当前模块: {{ moduleNames[progress.current_module] || progress.current_module }}</p>
        <p>进度: {{ progress.completed_commodities }}/{{ progress.total_commodities }}</p>
      </div>

      <el-table
        :data="commodityProgressList"
        size="small"
        style="margin-top: 16px"
      >
        <el-table-column prop="commodity" label="品种" width="100" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'" size="small">
              {{ row.status === 'completed' ? '完成' : row.status === 'failed' ? '失败' : '进行中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="direction" label="方向" width="100">
          <template #default="{ row }">
            <span v-if="row.direction" :class="row.direction === 'long' ? 'status-success' : 'status-danger'">
              {{ row.direction === 'long' ? '看多' : row.direction === 'short' ? '看空' : '中性' }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="confidence" label="信心度" width="100">
          <template #default="{ row }">
            {{ row.confidence ? (row.confidence * 100).toFixed(0) + '%' : '-' }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 分析结果 -->
    <template v-if="resultsList.length">
      <el-card v-for="item in resultsList" :key="item.commodity" class="result-card">
        <template #header>
          <div class="card-header">
            <span>📊 {{ item.commodity }} 分析结果</span>
            <div>
              <el-tag v-if="item.executive_decision?.final_decision"
                :type="decisionType(item.executive_decision.final_decision)" size="default">
                {{ decisionLabel(item.executive_decision.final_decision) }}
              </el-tag>
              <el-button size="small" @click="exportWord(item.commodity)" :loading="exporting === item.commodity">
                <el-icon><Download /></el-icon> Word
              </el-button>
            </div>
          </div>
        </template>

        <!-- 最终决策 -->
        <div v-if="item.executive_decision && Object.keys(item.executive_decision).length" class="decision-card">
          <div class="decision-title">🎯 最终决策</div>
          <div class="decision-value">{{ decisionLabel(item.executive_decision.final_decision) }}</div>
          <div class="decision-meta">
            <span>信心等级: {{ item.executive_decision.confidence_level || '未知' }}</span>
            <span>方向: {{ item.executive_decision.directional_view || '未知' }}</span>
            <span>方向信心: {{ ((item.executive_decision.directional_confidence || 0) * 100).toFixed(0) }}%</span>
          </div>
          <p v-if="item.executive_decision.reasoning" class="decision-reasoning">
            {{ item.executive_decision.reasoning }}
          </p>
        </div>

        <!-- 模块分析结果 -->
        <el-collapse v-model="item.activeModules" v-if="item.modules">
          <el-collapse-item
            v-for="(modResult, modKey) in item.modules"
            :key="modKey"
            :name="modKey"
          >
            <template #title>
              <div class="module-title">
                <el-tag :type="modResult.status === 'completed' ? 'success' : 'danger'" size="small">
                  {{ moduleNames[modKey] || modKey }}
                </el-tag>
                <span v-if="modResult.confidence_score" class="confidence">
                  信心度: {{ (modResult.confidence_score * 100).toFixed(0) }}%
                </span>
                <span v-if="modResult.directional_view" class="direction">
                  方向: {{ modResult.directional_view }}
                </span>
              </div>
            </template>

            <div class="module-detail">
              <p v-if="modResult.analysis_summary">{{ modResult.analysis_summary }}</p>
              <ul v-if="modResult.key_findings?.length">
                <li v-for="(finding, idx) in modResult.key_findings" :key="idx">{{ finding }}</li>
              </ul>
              <el-alert v-if="modResult.error" type="error" :title="modResult.error" :closable="false" />
            </div>
          </el-collapse-item>
        </el-collapse>

        <!-- 辩论结果 -->
        <div v-if="item.debate" class="debate-section">
          <h4>🎭 多空辩论</h4>
          <DebateHistory
            :history="item.debate.history || []"
            :rounds="item.debate.rounds || 0"
            :actual-rounds="item.debate.actual_rounds || 0"
            :terminated-reason="item.debate.terminated_reason || ''"
            :winner="item.debate.winner || 'split'"
          />
        </div>

        <!-- 交易员建议 -->
        <div v-if="item.trader" class="trader-section">
          <h4>💼 交易员建议</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="方向">{{ item.trader.direction === 'long' ? '做多' : item.trader.direction === 'short' ? '做空' : '观望' }}</el-descriptions-item>
            <el-descriptions-item label="信心度">{{ ((item.trader.confidence || 0) * 100).toFixed(0) }}%</el-descriptions-item>
            <el-descriptions-item label="仓位建议">{{ item.trader.position_size || '未知' }}</el-descriptions-item>
            <el-descriptions-item label="入场价">{{ item.trader.entry_price || '-' }}</el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 风控意见 -->
        <div v-if="item.risk_management" class="risk-section">
          <h4>🛡️ 风控意见</h4>
          <el-tag :type="item.risk_management.approved ? 'success' : 'danger'" size="default">
            {{ item.risk_management.approved ? '✅ 批准' : '❌ 否决' }}
          </el-tag>
          <span style="margin-left: 12px">风险等级: {{ item.risk_management.risk_level || '未知' }}</span>
          <span style="margin-left: 12px">最大仓位: {{ ((item.risk_management.max_position || 0) * 100).toFixed(0) }}%</span>
        </div>
      </el-card>
    </template>

    <!-- 无结果时的缓存列表 -->
    <el-card v-if="!store.isAnalysisRunning && !resultsList.length" class="cache-card">
      <template #header>
        <div class="card-header">
          <span>📦 本地缓存</span>
          <el-button size="small" @click="loadCacheList" :loading="loadingCache">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>

      <el-empty v-if="!store.cacheList.length" description="暂无缓存，请先执行分析" />

      <el-table v-else :data="store.cacheList" size="small">
        <el-table-column prop="commodity" label="品种" width="100" />
        <el-table-column prop="analysis_date" label="分析日期" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'completed' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="loadCachedResult(row)">加载</el-button>
            <el-popconfirm
              :title="`确认删除 ${row.commodity} ${row.analysis_date} 的缓存？`"
              width="220"
              @confirm="deleteCachedResult(row)"
            >
              <template #reference>
                <el-button size="small" type="danger" :loading="deletingCache === `${row.commodity}_${row.analysis_date}`">
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 空状态 -->
    <el-empty v-if="!store.isAnalysisRunning && !resultsList.length && !store.cacheList.length"
      description="请在分析配置页面设置参数并开始分析"
    >
      <el-button type="primary" @click="$router.push('/analysis')">去配置分析</el-button>
    </el-empty>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { analysisApi } from '@/api'
import { ElMessage } from 'element-plus'
import DebateHistory from '@/components/DebateHistory.vue'

const store = useAppStore()
const exporting = ref('')
const loadingCache = ref(false)
const deletingCache = ref('')
let pollTimer = null

const moduleNames = {
  inventory: '库存仓单分析',
  positioning: '持仓席位分析',
  term_structure: '期限结构分析',
  technical: '技术面分析',
  basis: '基差分析',
  news: '新闻分析',
}

const progress = computed(() => store.analysisStatus.progress)
const results = computed(() => store.analysisStatus.results || {})

const resultsList = computed(() => {
  return Object.entries(results.value).map(([commodity, data]) => ({
    commodity,
    ...data,
    activeModules: Object.keys(data.modules || {}).slice(0, 2),
  }))
})

const progressPercent = computed(() => {
  if (!progress.value) return 0
  const { completed_commodities, total_commodities } = progress.value
  if (!total_commodities) return 0
  return Math.round((completed_commodities / total_commodities) * 100)
})

const progressStatus = computed(() => {
  if (store.analysisStatus.status === 'completed') return 'success'
  if (store.analysisStatus.status === 'failed') return 'exception'
  return ''
})

const commodityProgressList = computed(() => {
  if (!progress.value) return []
  const list = []
  const commodities = store.analysisStatus.commodities || []
  for (const c of commodities) {
    const result = results.value[c]
    list.push({
      commodity: c,
      status: result ? 'completed' : (c === progress.value.current_commodity ? 'running' : 'pending'),
      direction: result?.executive_decision?.final_decision || result?.trader?.direction,
      confidence: result?.executive_decision?.directional_confidence || result?.trader?.confidence,
    })
  }
  return list
})

function decisionType(decision) {
  if (decision === 'long') return 'success'
  if (decision === 'short') return 'danger'
  return 'info'
}

function decisionLabel(decision) {
  const map = { long: '看多 📈', short: '看空 📉', neutral: '中性 ➡️', hold: '观望 ⏸️' }
  return map[decision] || decision || '未知'
}

async function exportWord(commodity) {
  exporting.value = commodity
  try {
    const blob = await analysisApi.generateWordReport({
      commodities: commodity,
      include_charts: false,
    })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `期货分析报告_${commodity}_${new Date().toISOString().slice(0, 10)}.docx`
    a.click()
    window.URL.revokeObjectURL(url)
    ElMessage.success('报告下载成功')
  } catch (e) {
    ElMessage.error('报告生成失败')
  } finally {
    exporting.value = ''
  }
}

async function loadCachedResult(meta) {
  try {
    const result = await analysisApi.loadCache(meta.commodity, meta.analysis_date)
    // 将结果合并到当前结果中
    store.analysisStatus.results = {
      ...store.analysisStatus.results,
      [meta.commodity]: result,
    }
    ElMessage.success(`${meta.commodity} 缓存已加载`)
  } catch (e) {
    ElMessage.error('缓存加载失败')
  }
}

// 🔧 修复：接线缓存删除按钮（此前 DELETE /analysis/cache 端点无任何 UI 入口）
async function deleteCachedResult(meta) {
  const key = `${meta.commodity}_${meta.analysis_date}`
  deletingCache.value = key
  try {
    await analysisApi.deleteCache(meta.commodity, meta.analysis_date)
    ElMessage.success(`${meta.commodity} ${meta.analysis_date} 缓存已删除`)
    await store.fetchCacheList()
  } catch (e) {
    ElMessage.error('缓存删除失败')
  } finally {
    deletingCache.value = ''
  }
}

async function loadCacheList() {
  loadingCache.value = true
  try {
    await store.fetchCacheList()
  } finally {
    loadingCache.value = false
  }
}

async function pollStatus() {
  await store.fetchAnalysisStatus()
  if (store.isAnalysisRunning) {
    pollTimer = setTimeout(pollStatus, 3000)
  }
}

onMounted(async () => {
  await store.fetchAnalysisStatus()
  await store.fetchCacheList()
  if (store.isAnalysisRunning) {
    pollTimer = setTimeout(pollStatus, 3000)
  }
})

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<style scoped>
.progress-card, .result-card, .cache-card { margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.progress-info { margin-top: 12px; }
.progress-info p { margin: 4px 0; color: #606266; }
.module-title { display: flex; align-items: center; gap: 12px; }
.confidence { color: #409eff; font-size: 13px; }
.direction { color: #606266; font-size: 13px; }
.module-detail { padding: 8px 0; }
.module-detail p { margin: 4px 0; color: #606266; }
.module-detail ul { padding-left: 20px; color: #606266; }
.debate-section, .trader-section, .risk-section { margin-top: 16px; padding-top: 12px; border-top: 1px solid #ebeef5; }
.debate-section h4, .trader-section h4, .risk-section h4 { margin-bottom: 12px; }
</style>
