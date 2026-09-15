<template>
  <div class="data-update">
    <!-- 品种概览 -->
    <el-card class="overview-card">
      <template #header>
        <div class="card-header">
          <span>📋 品种列表</span>
          <div>
            <el-button size="small" @click="loadCommodities" :loading="loadingCommodities">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
            <el-button
              size="small"
              type="warning"
              @click="refreshContracts"
              :loading="refreshingContracts"
              :disabled="taskRunning && !refreshingContracts"
            >
              <el-icon><Link /></el-icon> 更新主力合约
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="commodities" size="small" stripe max-height="400" v-loading="loadingCommodities">
        <el-table-column prop="symbol" label="代码" width="80" fixed />
        <el-table-column prop="name" label="品种" width="100" />
        <el-table-column prop="exchange" label="交易所" width="80" />
        <el-table-column prop="category" label="分类" width="100" />
        <el-table-column label="主力合约" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.dominant_contract" size="small" type="success">{{ row.dominant_contract }}</el-tag>
            <span v-else class="text-muted">未获取</span>
          </template>
        </el-table-column>
        <el-table-column label="数据状态" min-width="200">
          <template #default="{ row }">
            <el-tooltip v-for="(has, key) in row.data_status" :key="key"
              :content="moduleNames[key] + (has ? ' ✓' : ' ✗')" placement="top">
              <el-tag :type="has ? 'success' : 'info'" size="small" effect="plain" style="margin: 1px">
                {{ moduleShort[key] }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 数据更新 -->
    <el-card style="margin-top: 16px">
      <template #header>
        <div class="card-header">
          <span>🔄 数据更新</span>
          <el-tag type="info">选择品种和模块进行更新</el-tag>
        </div>
      </template>

      <!-- 品种选择 -->
      <el-form :inline="true" style="margin-bottom: 16px">
        <el-form-item label="更新品种">
          <el-select
            v-model="selectedVarieties"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="默认全部品种"
            style="width: 400px"
            filterable
            :reserve-keyword="false"
          >
            <el-option
              v-for="c in commodities"
              :key="c.symbol"
              :label="`${c.symbol} ${c.name}`"
              :value="c.symbol"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标日期">
          <el-date-picker
            v-model="targetDate"
            type="date"
            value-format="YYYY-MM-DD"
            :clearable="false"
            style="width: 150px"
          />
        </el-form-item>
        <el-form-item>
          <el-button @click="selectAllVarieties">全选</el-button>
          <el-button @click="selectedVarieties = []">清除</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        title="不选择品种则更新全部品种。更新已改为后台任务模式：提交后立即返回，本页实时展示进度；首次全量更新耗时较长，可放心等待（即使关闭页面，任务也会继续执行）。"
        type="info"
        :closable="false"
        style="margin-bottom: 16px"
      />

      <!-- 后台任务进度面板 -->
      <el-card v-if="currentTask" shadow="never" class="task-panel">
        <div class="task-head">
          <el-tag
            :type="currentTask.status === 'success' ? 'success' : (currentTask.status === 'failed' ? 'danger' : '')"
            size="small"
          >
            {{ currentTask.status === 'running' ? '运行中' : (currentTask.status === 'success' ? '成功' : '失败') }}
          </el-tag>
          <span class="task-title">{{ currentTask.module_name }}</span>
          <el-button link type="primary" style="margin-left: auto" @click="dismissTask">隐藏</el-button>
        </div>
        <template v-if="currentTask.status === 'running'">
          <div class="task-stage">
            {{ currentTask.stage }}
            <template v-if="currentTask.current">：{{ currentTask.current }}</template>
          </div>
          <el-progress
            :percentage="taskIndeterminate ? 0 : (currentTask.percent || 0)"
            :indeterminate="taskIndeterminate"
            :stroke-width="10"
          />
          <div class="task-meta">
            已运行 {{ elapsedSeconds }} 秒
            <template v-if="currentTask.total"> · 已完成 {{ currentTask.done }}/{{ currentTask.total }} 单元</template>
            <template v-if="currentTask.variety_count"> · 共 {{ currentTask.variety_count }} 个品种</template>
          </div>
        </template>
        <div v-else class="task-done">
          <span class="task-msg">{{ currentTask.message }}</span>
          <span v-if="currentTask.finished_at" class="task-meta">完成于 {{ currentTask.finished_at }}</span>
        </div>
        <div v-if="currentTask.details" class="log-details">{{ currentTask.details }}</div>
      </el-card>

      <!-- 模块更新按钮 -->
      <el-row :gutter="16">
        <el-col :span="8" v-for="item in updateItems" :key="item.key">
          <el-card shadow="hover" class="update-card">
            <div class="update-item">
              <div class="update-info">
                <el-icon :size="28" :color="item.color">
                  <component :is="item.icon" />
                </el-icon>
                <div>
                  <h4>{{ item.name }}</h4>
                  <p>{{ item.desc }}</p>
                </div>
              </div>
              <el-button
                type="primary"
                :loading="updating === item.key"
                :disabled="taskRunning && updating !== item.key"
                @click="handleUpdate(item.key)"
              >
                {{ updating === item.key ? '任务处理中...' : '更新' }}
              </el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 更新日志 -->
      <el-card v-if="updateLogs.length" style="margin-top: 16px">
        <template #header><span>📝 更新日志</span></template>
        <el-timeline>
          <el-timeline-item
            v-for="(log, idx) in updateLogs"
            :key="idx"
            :timestamp="log.time"
            :type="log.status === 'success' ? 'success' : 'danger'"
          >
            <strong>{{ log.module }}</strong>: {{ log.message }}
            <div v-if="log.details" class="log-details">{{ log.details }}</div>
          </el-timeline-item>
        </el-timeline>
      </el-card>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { dataApi } from '@/api'
import { useAppStore } from '@/stores/app'
import { ElMessage } from 'element-plus'

const store = useAppStore()

const loadingCommodities = ref(false)
const refreshingContracts = ref(false)
const commodities = ref([])
const selectedVarieties = ref([])
const targetDate = ref(new Date().toISOString().split('T')[0])
const updating = ref('')
const updateLogs = ref([])

// 修复(C1)：数据更新任务化。currentTask 为正在后台运行 / 最近一次完成的任务
const currentTask = ref(null)
const pollTimer = ref(null)
const taskRunning = computed(() => !!currentTask.value && currentTask.value.status === 'running')
const taskIndeterminate = computed(() => taskRunning.value && currentTask.value.percent == null)
const elapsedSeconds = computed(() => {
  const t = currentTask.value
  if (!t) return 0
  const from = t.submitted_at ? new Date(t.submitted_at.replace(' ', 'T')).getTime() : Date.now()
  const to = t.finished_at ? new Date(t.finished_at.replace(' ', 'T')).getTime() : Date.now()
  return Math.max(0, Math.round((to - from) / 1000))
})

const moduleNames = {
  inventory: '库存数据',
  positioning: '持仓席位',
  term_structure: '期限结构',
  technical_analysis: '技术面',
  basis: '基差数据',
  receipt: '仓单数据',
}
const moduleShort = {
  inventory: '库存',
  positioning: '持仓',
  term_structure: '期限',
  technical_analysis: '技术',
  basis: '基差',
  receipt: '仓单',
}

const updateItems = [
  { key: 'inventory', name: '库存数据', desc: '日度库存数据', icon: 'Box', color: '#409eff' },
  { key: 'positioning', name: '持仓席位', desc: '前20名持仓数据', icon: 'Aim', color: '#67c23a' },
  { key: 'term_structure', name: '期限结构', desc: '近月/远月价差', icon: 'TrendCharts', color: '#e6a23c' },
  { key: 'technical_analysis', name: '技术分析', desc: 'K线及技术指标', icon: 'DataLine', color: '#f56c6c' },
  { key: 'basis', name: '基差数据', desc: '期现基差', icon: 'Coin', color: '#909399' },
  { key: 'receipt', name: '仓单数据', desc: '仓单注册/注销', icon: 'Document', color: '#b37feb' },
]

async function loadCommodities() {
  loadingCommodities.value = true
  try {
    commodities.value = await dataApi.getCommodities()
  } catch (e) {
    ElMessage.error('获取品种列表失败')
  } finally {
    loadingCommodities.value = false
  }
}

async function refreshContracts() {
  if (taskRunning.value) {
    ElMessage.info(`已有任务（${currentTask.value.module_name}）正在运行，请等待完成后再试`)
    return
  }
  refreshingContracts.value = true
  try {
    // 修复(C1)：主力合约刷新同样改为后台任务 + 轮询
    const result = await dataApi.refreshDominantContracts()
    if (!result || !result.task_id) throw new Error('后端未返回任务 ID')
    startPolling(result.task)
  } catch (e) {
    refreshingContracts.value = false
    ElMessage.error('提交刷新任务失败，请确认后端服务正常')
  }
}

function selectAllVarieties() {
  selectedVarieties.value = commodities.value.map(c => c.symbol)
}

async function handleUpdate(moduleKey) {
  if (taskRunning.value) {
    ElMessage.info(`已有任务（${currentTask.value.module_name}）正在运行，请等待完成后再试`)
    return
  }
  updating.value = moduleKey
  try {
    const params = { target_date: targetDate.value }
    if (selectedVarieties.value.length) {
      params.varieties = selectedVarieties.value.join(',')
    }
    // 🔧 修复(C1)：更新接口已任务化——后端后台线程执行并逐品种上报进度，
    // 前端提交后立即拿到 task_id 轮询，不再因长耗时同步等待而误报失败。
    const result = await store.updateData(moduleKey, params)
    if (!result || !result.task_id) {
      throw new Error('后端未返回任务 ID')
    }
    if (result.already_running) {
      ElMessage.info(result.message || '已有任务在运行，已自动跟踪该任务')
    }
    startPolling(result.task)
  } catch (e) {
    updating.value = ''
    ElMessage.error('提交更新任务失败，请确认后端服务正常')
  }
}

// ================= 后台任务轮询 =================
function startPolling(task) {
  stopPolling()
  if (!task) return
  currentTask.value = { ...task }
  if (task.kind === 'refresh_contracts') {
    refreshingContracts.value = true
    updating.value = ''
  } else {
    updating.value = task.module_key || ''
    refreshingContracts.value = false
  }
  pollTaskOnce()
  pollTimer.value = setInterval(pollTaskOnce, 1500)
}

function stopPolling() {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function pollTaskOnce() {
  const task = currentTask.value
  if (!task || !task.task_id) return
  try {
    const t = await dataApi.getDataTask(task.task_id)
    currentTask.value = t
    if (t.status === 'success' || t.status === 'failed') {
      await handleTaskFinished(t)
    }
  } catch (e) {
    // 瞬时网络错误忽略，下一轮继续
  }
}

async function handleTaskFinished(t) {
  stopPolling()
  const isRefresh = t.kind === 'refresh_contracts'
  const moduleName = isRefresh
    ? '主力合约'
    : t.module_name || moduleNames[t.module_key] || t.module_key
  updateLogs.value.unshift({
    module: moduleName,
    message: t.message || (t.status === 'success' ? '完成' : '失败'),
    details: t.details,
    status: t.status,
    time: new Date().toLocaleString('zh-CN'),
  })
  if (t.status === 'success') {
    ElMessage.success(`${moduleName}更新完成：${t.message || ''}`)
  } else {
    ElMessage.error(`${moduleName}更新失败：${t.message || ''}`)
  }
  updating.value = ''
  refreshingContracts.value = false
  // 完成后刷新品种数据状态（主力合约/数据状态列）
  await loadCommodities()
}

function dismissTask() {
  stopPolling()
  updating.value = ''
  refreshingContracts.value = false
  currentTask.value = null
}

// 页面（重新）进入时，若仍有后台任务在运行则恢复跟踪，避免重复提交
async function resumeRunningTask() {
  try {
    const res = await dataApi.listDataTasks()
    const running = (res.tasks || []).find((t) => t.status === 'running')
    if (running) {
      ElMessage.info(`检测到后台任务（${running.module_name}）仍在运行，已恢复进度跟踪`)
      startPolling(running)
    }
  } catch (e) {
    // 忽略：首次进入或后端暂不可用时静默
  }
}

onMounted(() => {
  loadCommodities()
  resumeRunningTask()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.overview-card { margin-bottom: 16px; }
.update-card { margin-bottom: 16px; }
.update-item { display: flex; justify-content: space-between; align-items: center; }
.update-info { display: flex; align-items: center; gap: 12px; }
.update-info h4 { margin: 0 0 4px 0; font-size: 15px; }
.update-info p { margin: 0; font-size: 12px; color: #909399; }
.text-muted { color: #c0c4cc; font-size: 12px; }
.log-details { font-size: 12px; color: #909399; margin-top: 4px; word-break: break-all; }
.task-panel { margin-bottom: 16px; border: 1px solid var(--el-border-color); }
.task-head { display: flex; align-items: center; gap: 8px; }
.task-title { font-weight: 600; font-size: 13px; }
.task-stage { font-size: 13px; color: #606266; margin: 12px 0 6px; }
.task-meta { font-size: 12px; color: #909399; margin-top: 6px; }
.task-done { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.task-msg { font-size: 13px; color: #303133; }
</style>
