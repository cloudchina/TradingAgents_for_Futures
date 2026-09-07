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
            <el-button size="small" type="warning" @click="refreshContracts" :loading="refreshingContracts">
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
        title="不选择品种则更新全部品种。更新过程可能需要几分钟，请勿关闭页面。"
        type="info"
        :closable="false"
        style="margin-bottom: 16px"
      />

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
                @click="handleUpdate(item.key)"
              >
                {{ updating === item.key ? '更新中...' : '更新' }}
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
import { ref, onMounted } from 'vue'
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
  refreshingContracts.value = true
  try {
    const result = await dataApi.refreshDominantContracts()
    ElMessage.success(result.message || '主力合约已更新')
    await loadCommodities()
  } catch (e) {
    ElMessage.error('更新主力合约失败')
  } finally {
    refreshingContracts.value = false
  }
}

function selectAllVarieties() {
  selectedVarieties.value = commodities.value.map(c => c.symbol)
}

async function handleUpdate(moduleKey) {
  updating.value = moduleKey
  try {
    const params = { target_date: targetDate.value }
    if (selectedVarieties.value.length) {
      params.varieties = selectedVarieties.value.join(',')
    }
    // 🔧 修复(A1)：复用 store.updateData，消除重复实现
    const result = await store.updateData(moduleKey, params)
    const moduleName = updateItems.find(i => i.key === moduleKey)?.name || moduleKey
    updateLogs.value.unshift({
      module: moduleName,
      message: result.message,
      details: result.details,
      status: result.status,
      time: new Date().toLocaleString('zh-CN'),
    })
    if (result.status === 'success') {
      ElMessage.success(result.message)
    } else {
      ElMessage.warning(result.message)
    }
    // 刷新品种数据状态
    await loadCommodities()
  } catch (e) {
    ElMessage.error('更新失败')
  } finally {
    updating.value = ''
  }
}

onMounted(() => {
  loadCommodities()
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
</style>
