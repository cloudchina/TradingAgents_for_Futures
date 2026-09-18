<template>
  <div class="memory-view">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="card-title">🧠 记忆体系</span>
          <div class="header-actions">
            <el-select
              v-model="symbol"
              filterable
              placeholder="选择品种"
              style="width: 160px"
              @change="loadAll"
            >
              <el-option v-for="c in commodities" :key="c" :label="c" :value="c" />
            </el-select>
            <el-button type="primary" :loading="loading" @click="loadAll">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
            <el-button :loading="backfilling" @click="handleBackfill">复盘回填</el-button>
            <el-button :loading="consolidating" @click="handleConsolidate">语义巩固</el-button>
            <el-button :loading="refreshingRel" @click="handleRefreshRelations">
              重算关联
            </el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="!detail" type="info" :closable="false" show-icon>
        <template #title>
          记忆体系会在「完整流程」分析结束后自动落库；每日 20:00 定时回填复盘结果。
          无历史结论属正常现象——先跑一次分析。
        </template>
      </el-alert>

      <template v-if="detail">
        <!-- 概览 -->
        <el-row :gutter="12" class="stat-row">
          <el-col :span="6">
            <div class="stat-box">
              <div class="stat-value">{{ detail.stm.length }}</div>
              <div class="stat-label">近期结论（短期记忆）</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-box">
              <div class="stat-value">{{ pct(stats.hit_rate) }}</div>
              <div class="stat-label">方向命中率（{{ stats.n || 0 }} 个已复盘样本）</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-box">
              <div class="stat-value">{{ detail.ltm.length }}</div>
              <div class="stat-label">语义记忆（长期记忆）</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-box">
              <div class="stat-value">{{ detail.relations.length }}</div>
              <div class="stat-label">关联品种</div>
            </div>
          </el-col>
        </el-row>

        <el-tabs v-model="activeTab" class="memory-tabs">
          <!-- 短期记忆 -->
          <el-tab-pane label="📅 短期记忆" name="stm">
            <el-table :data="detail.stm" size="small" border max-height="420">
              <el-table-column prop="analysis_date" label="日期" width="110" />
              <el-table-column label="方向" width="90">
                <template #default="{ row }">
                  <el-tag :type="directionType(row.direction)" size="small">
                    {{ row.direction_view || row.direction }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="置信度" width="90">
                <template #default="{ row }">
                  {{ (row.confidence ?? 0).toFixed(2) }}
                  <span class="sub-text">{{ row.confidence_level }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="summary" label="核心结论" min-width="220" show-overflow-tooltip />
              <el-table-column label="状态" width="120">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.status)" size="small" effect="plain">
                    {{ statusText(row) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="复盘收益" width="110">
                <template #default="{ row }">
                  <span v-if="row.outcome?.realized_return !== undefined"
                        :class="row.outcome.realized_return >= 0 ? 'up' : 'down'">
                    {{ (row.outcome.realized_return * 100).toFixed(2) }}%
                  </span>
                  <span v-else class="sub-text">—</span>
                </template>
              </el-table-column>
              <el-table-column label="依据" min-width="200" show-overflow-tooltip>
                <template #default="{ row }">
                  {{ (row.key_evidence || []).join('、') || '—' }}
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <!-- 长期记忆 -->
          <el-tab-pane label="📚 长期记忆" name="ltm">
            <el-table :data="detail.ltm" size="small" border max-height="420">
              <el-table-column prop="category" label="类别" width="90" />
              <el-table-column prop="claim" label="规律" min-width="280" show-overflow-tooltip />
              <el-table-column prop="evidence_count" label="样本" width="70" />
              <el-table-column label="置信度" width="90">
                <template #default="{ row }">
                  {{ (row.confidence ?? 0).toFixed(2) }}
                </template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="semanticType(row.status)" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="last_seen" label="最近出现" width="160" />
              <el-table-column label="人工覆盖" width="220" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="reviewSemantic(row, 'activate')">转 active</el-button>
                  <el-button size="small" @click="reviewSemantic(row, 'force_draft')">回退 draft</el-button>
                  <el-button size="small" type="danger" plain @click="reviewSemantic(row, 'archive')">
                    归档
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!detail.ltm.length" description="暂无语义记忆（需积累 episode 后由巩固 Job 产出）" />
          </el-tab-pane>

          <!-- 关联品种 -->
          <el-tab-pane label="🔗 关联品种" name="relation">
            <el-table :data="detail.relations" size="small" border max-height="420">
              <el-table-column prop="symbol" label="品种" width="90" />
              <el-table-column prop="relation_type_cn" label="关系" width="110" />
              <el-table-column label="实测相关" width="120">
                <template #default="{ row }">
                  <span v-if="row.corr !== null && row.corr !== undefined">
                    {{ row.corr.toFixed(2) }}（{{ row.window }}日）
                  </span>
                  <el-tag v-else size="small" type="info">样本不足</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="lead_text" label="领先/滞后" min-width="160" />
              <el-table-column label="近5日" width="100">
                <template #default="{ row }">
                  <span v-if="row.recent_return !== null && row.recent_return !== undefined"
                        :class="row.recent_return >= 0 ? 'up' : 'down'">
                    {{ (row.recent_return * 100).toFixed(2) }}%
                  </span>
                  <span v-else class="sub-text">—</span>
                </template>
              </el-table-column>
              <el-table-column label="最近结论" min-width="180">
                <template #default="{ row }">
                  <span v-if="row.last_episode">
                    {{ row.last_episode.analysis_date }}
                    {{ row.last_episode.direction_view || row.last_episode.direction }}
                    <span v-if="row.stale" class="sub-text">（陈旧）</span>
                  </span>
                  <span v-else class="sub-text">—</span>
                </template>
              </el-table-column>
              <el-table-column label="相关度" width="110">
                <template #default="{ row }">
                  {{ (row.related_relevance ?? 0).toFixed(3) }}
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <!-- 校准曲线 -->
          <el-tab-pane label="📈 置信度校准" name="stats">
            <el-descriptions :column="2" border size="small" class="block">
              <el-descriptions-item label="样本数">{{ stats.n || 0 }}</el-descriptions-item>
              <el-descriptions-item label="命中数">{{ stats.hits || 0 }}</el-descriptions-item>
              <el-descriptions-item label="命中率">{{ pct(stats.hit_rate) }}</el-descriptions-item>
              <el-descriptions-item label="平均收益">{{ pct(stats.avg_return) }}</el-descriptions-item>
              <el-descriptions-item label="平均 MAE">{{ pct(stats.avg_mae) }}</el-descriptions-item>
              <el-descriptions-item label="不可验证">{{ stats.unverifiable ?? 0 }}</el-descriptions-item>
            </el-descriptions>

            <el-divider content-position="left">置信度分档 vs 实际胜率</el-divider>
            <el-table :data="stats.by_confidence || []" size="small" border>
              <el-table-column prop="bin" label="置信度档" width="120" />
              <el-table-column prop="n" label="样本" width="90" />
              <el-table-column label="实际胜率" min-width="240">
                <template #default="{ row }">
                  <el-progress
                    :percentage="Math.round((row.hit_rate || 0) * 100)"
                    :color="progressColor(row.hit_rate)"
                  />
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!(stats.by_confidence || []).length" description="暂无已复盘样本" />

            <el-divider content-position="left">错误模式</el-divider>
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="高置信度说错">
                {{ stats.error_patterns?.high_confidence_wrong ?? 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="跨换月样本">
                {{ stats.error_patterns?.rolled_samples ?? 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="复权降级">
                {{ stats.error_patterns?.adjust_degraded ?? 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="缺主力合约映射">
                {{ stats.error_patterns?.contract_map_missing ?? 0 }}
              </el-descriptions-item>
            </el-descriptions>
          </el-tab-pane>

          <!-- 人工记忆 -->
          <el-tab-pane label="✍️ 人工记忆" name="notes">
            <div class="note-toolbar">
              <el-button type="primary" size="small" @click="noteDialog = true">
                新增人工记忆
              </el-button>
              <span class="sub-text">人工备注与 LLM 产出的语义记忆分开存放，可直接删除</span>
            </div>
            <el-table :data="detail.notes" size="small" border max-height="360">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="symbol" label="品种" width="90" />
              <el-table-column prop="content" label="内容" min-width="320" show-overflow-tooltip />
              <el-table-column prop="author" label="作者" width="100" />
              <el-table-column prop="created_at" label="时间" width="170" />
              <el-table-column label="操作" width="90" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="danger" plain @click="handleDeleteNote(row.id)">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!detail.notes.length" description="暂无人工记忆" />
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-card>

    <!-- 全量关联图谱 -->
    <el-card shadow="never" class="graph-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">🕸️ 品种关联图谱（{{ graph.edges?.length || 0 }} 条关系）</span>
          <el-button size="small" :loading="loadingGraph" @click="loadGraph">加载图谱</el-button>
        </div>
      </template>
      <el-table :data="graph.edges || []" size="small" border max-height="320">
        <el-table-column prop="pair_key" label="Pair" width="110" />
        <el-table-column prop="type_cn" label="关系" width="110" />
        <el-table-column prop="strength" label="静态强度" width="100" />
        <el-table-column label="实测 corr" width="120">
          <template #default="{ row }">
            <span v-if="row.corr !== null && row.corr !== undefined">{{ row.corr.toFixed(2) }}</span>
            <el-tag v-else size="small" type="info">{{ row.corr_source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lead_symbol" label="领先方" width="90" />
        <el-table-column prop="lag_days" label="lag" width="70" />
        <el-table-column prop="note" label="说明" min-width="220" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 新增人工记忆 -->
    <el-dialog v-model="noteDialog" title="新增人工记忆" width="520px">
      <el-form label-width="80px">
        <el-form-item label="品种">
          <el-input v-model="noteForm.symbol" :placeholder="symbol" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="noteForm.content" type="textarea" :rows="4" placeholder="例如：RB 库存去化 + 基差走强时，5 日上涨概率偏高" />
        </el-form-item>
        <el-form-item label="作者">
          <el-input v-model="noteForm.author" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="noteDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingNote" @click="handleSaveNote">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { memoryApi } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

const store = useAppStore()

const symbol = ref('RB')
const activeTab = ref('stm')
const loading = ref(false)
const backfilling = ref(false)
const consolidating = ref(false)
const refreshingRel = ref(false)
const loadingGraph = ref(false)

const detail = ref(null)
const graph = ref({ nodes: [], edges: [] })

const noteDialog = ref(false)
const savingNote = ref(false)
const noteForm = reactive({ symbol: '', content: '', author: '' })

const commodities = computed(() => store.availableCommodities?.length ? store.availableCommodities : ['RB'])
const stats = computed(() => detail.value?.stats || {})

function pct(v) {
  const n = Number(v || 0)
  return `${(n * 100).toFixed(1)}%`
}
function directionType(d) {
  return d === 'long' ? 'danger' : d === 'short' ? 'success' : 'info'
}
function statusType(row) {
  if (row.status === 'resolved') return row.outcome?.hit ? 'success' : 'warning'
  if (row.status === 'pending') return 'info'
  return 'danger'
}
function statusText(row) {
  if (row.status === 'resolved') return row.outcome?.hit ? '已命中' : '未命中'
  if (row.status === 'pending') return '待复盘'
  return row.status
}
function semanticType(s) {
  return s === 'active' ? 'success' : s === 'draft' ? 'info' : 'warning'
}
function progressColor(rate) {
  const r = Number(rate || 0)
  if (r >= 0.6) return '#67c23a'
  if (r >= 0.4) return '#e6a23c'
  return '#f56c6c'
}

async function loadDetail() {
  try {
    detail.value = await memoryApi.getSymbol(symbol.value)
  } catch (e) {
    detail.value = null
    ElMessage.error('加载品种记忆失败')
  }
}

async function loadAll() {
  loading.value = true
  try {
    await loadDetail()
  } finally {
    loading.value = false
  }
}

async function loadGraph() {
  loadingGraph.value = true
  try {
    graph.value = await memoryApi.getRelations()
  } catch (e) {
    ElMessage.error('加载关联图谱失败')
  } finally {
    loadingGraph.value = false
  }
}

async function handleBackfill() {
  backfilling.value = true
  try {
    const res = await memoryApi.backfill(symbol.value)
    ElMessage.success(`回填完成：${JSON.stringify(res?.backfill || res)}`)
    await loadDetail()
  } catch (e) {
    ElMessage.error('回填失败')
  } finally {
    backfilling.value = false
  }
}

async function handleConsolidate() {
  consolidating.value = true
  try {
    await memoryApi.consolidate(symbol.value)
    ElMessage.success('语义巩固已触发')
    await loadDetail()
  } catch (e) {
    ElMessage.error('巩固失败')
  } finally {
    consolidating.value = false
  }
}

async function handleRefreshRelations() {
  refreshingRel.value = true
  try {
    const res = await memoryApi.refreshRelations(symbol.value)
    ElMessage.success(`关联重算完成：动态 ${res?.dynamic ?? 0} 条 / 静态回退 ${res?.static_only ?? 0} 条`)
    await Promise.all([loadDetail(), loadGraph()])
  } catch (e) {
    ElMessage.error('关联重算失败')
  } finally {
    refreshingRel.value = false
  }
}

async function reviewSemantic(row, action) {
  try {
    await memoryApi.reviewSemantic(row.id, action)
    ElMessage.success(`已更新为 ${action}`)
    await loadDetail()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

async function handleSaveNote() {
  if (!noteForm.content.trim()) {
    ElMessage.warning('请填写内容')
    return
  }
  savingNote.value = true
  try {
    await memoryApi.addNote({
      symbol: noteForm.symbol.trim() || symbol.value,
      content: noteForm.content.trim(),
      author: noteForm.author.trim(),
    })
    noteDialog.value = false
    noteForm.content = ''
    ElMessage.success('已保存')
    await loadDetail()
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    savingNote.value = false
  }
}

async function handleDeleteNote(id) {
  try {
    await ElMessageBox.confirm('确认删除该条人工记忆？', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await memoryApi.deleteNote(id)
    ElMessage.success('已删除')
    await loadDetail()
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

onMounted(async () => {
  if (!store.availableCommodities?.length) {
    await store.fetchDataStatus()
  }
  await loadAll()
})
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stat-row {
  margin: 8px 0 16px;
}
.stat-box {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
  text-align: center;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
}
.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.memory-tabs {
  margin-top: 4px;
}
.graph-card {
  margin-top: 16px;
}
.block {
  margin-bottom: 12px;
}
.note-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.sub-text {
  color: #909399;
  font-size: 12px;
}
.up {
  color: #f56c6c;
}
.down {
  color: #67c23a;
}
</style>
