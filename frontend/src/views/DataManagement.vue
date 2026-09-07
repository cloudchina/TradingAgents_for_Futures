<template>
  <div class="data-management">
    <!-- 概览卡片 -->
    <el-row :gutter="16" class="overview-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header><span>📊 可用模块</span></template>
          <div class="metric-value">{{ dataStatus.summary.success_modules || 0 }}/{{ dataStatus.summary.total_modules || 6 }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header><span>📦 品种数量</span></template>
          <div class="metric-value">{{ dataStatus.summary.common_commodities?.length || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header><span>🔄 总记录数</span></template>
          <div class="metric-value">{{ totalRecords.toLocaleString() }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header><span>🕐 最近更新</span></template>
          <div class="metric-value-sm">{{ lastUpdate }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 各模块状态 -->
    <el-card class="module-card-wrap">
      <template #header>
        <div class="card-header">
          <span>📋 各模块数据状态</span>
          <el-button size="small" @click="refresh" :loading="loading">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>

      <el-collapse v-model="activeModules" @change="onCollapseChange">
        <el-collapse-item
          v-for="(info, key) in dataStatus.modules"
          :key="key"
          :name="key"
          :title="`${moduleNames[key] || key} - ${info.status}`"
        >
          <template #title>
            <div class="module-title">
              <el-tag :type="statusType(info.status)" size="small">
                {{ moduleNames[key] || key }}
              </el-tag>
              <span class="module-status">{{ info.status }}</span>
            </div>
          </template>

          <el-row :gutter="16">
            <el-col :span="8">
              <el-statistic title="品种数" :value="info.commodities_count" />
            </el-col>
            <el-col :span="8">
              <el-statistic title="记录数" :value="info.total_records" />
            </el-col>
            <el-col :span="8">
              <p>最后更新: {{ info.last_update }}</p>
              <p v-if="info.error" class="error-text">{{ info.error }}</p>
            </el-col>
          </el-row>

          <el-table
            v-if="varieties[key]?.length"
            :data="varieties[key]"
            size="small"
            style="margin-top: 12px"
            max-height="300"
          >
            <el-table-column prop="variety" label="品种" width="100" />
            <el-table-column prop="start_date" label="起始日期" width="120" />
            <el-table-column prop="end_date" label="结束日期" width="120" />
            <el-table-column prop="record_count" label="数据量" width="100" />
          </el-table>
          <div v-else-if="varietyLoading[key]" class="table-hint">
            <el-icon class="is-loading" style="margin-right: 6px"><Loading /></el-icon>正在加载品种列表...
          </div>
          <div v-else class="table-hint">该模块暂无品种数据（可先在「数据更新」页更新数据）</div>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { dataApi } from '@/api'
import { ElMessage } from 'element-plus'

const store = useAppStore()
const loading = ref(false)
const activeModules = ref(['inventory'])
const varieties = ref({})
const varietyLoading = ref({})

const moduleNames = {
  inventory: '库存数据',
  positioning: '持仓席位',
  term_structure: '期限结构',
  technical_analysis: '技术面指标',
  basis: '基差数据',
  receipt: '仓单数据',
}

const dataStatus = computed(() => store.dataStatus)

const totalRecords = computed(() => {
  return Object.values(dataStatus.value.modules || {}).reduce(
    (sum, m) => sum + (m.total_records || 0), 0
  )
})

const lastUpdate = computed(() => {
  const dates = Object.values(dataStatus.value.modules || {})
    .map(m => m.last_update)
    .filter(d => d && d !== '未知')
  return dates.sort().pop() || '未知'
})

function statusType(status) {
  const map = { success: 'success', error: 'danger', empty: 'warning' }
  return map[status] || 'info'
}

async function loadVarieties(moduleKey) {
  if (varieties.value[moduleKey]) return
  try {
    const list = await dataApi.getVarieties(moduleKey)
    varieties.value[moduleKey] = list || []
  } catch (e) {
    varieties.value[moduleKey] = []
  }
}

// 🔧 修复：折叠面板展开时才加载对应模块的品种列表，
// 此前只有第一个模块(onMounted)会被加载，其余模块表格永远为空。
async function onCollapseChange(activeNames) {
  const names = Array.isArray(activeNames) ? activeNames : [activeNames]
  for (const key of names) {
    if (varieties.value[key] || varietyLoading.value[key]) continue
    varietyLoading.value[key] = true
    try {
      await loadVarieties(key)
    } finally {
      varietyLoading.value[key] = false
    }
  }
}

async function refresh() {
  loading.value = true
  try {
    await store.fetchDataStatus()
    ElMessage.success('数据状态已刷新')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await store.fetchDataStatus()
  // 预加载默认展开的第一个模块的品种数据
  const keys = Object.keys(dataStatus.value.modules || {})
  if (keys.length) await loadVarieties(keys[0])
})
</script>

<style scoped>
.overview-row { margin-bottom: 16px; }
.metric-value { font-size: 28px; font-weight: 700; color: #409eff; }
.metric-value-sm { font-size: 14px; color: #606266; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.module-title { display: flex; align-items: center; gap: 8px; }
.module-status { color: #909399; font-size: 13px; }
.error-text { color: #f56c6c; font-size: 12px; }
.table-hint {
  margin-top: 12px;
  padding: 18px 0;
  text-align: center;
  color: #909399;
  font-size: 13px;
  border: 1px dashed #dcdfe6;
  border-radius: 4px;
}
</style>
