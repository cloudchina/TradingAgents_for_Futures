<template>
  <div class="debate-history">
    <!-- 顶部状态条 -->
    <div class="debate-status-bar">
      <el-tag :type="winnerType" size="default" effect="dark">
        {{ winnerLabel }}
      </el-tag>
      <el-tag type="info" size="small">
        实际轮数: {{ actualRounds }} / {{ rounds }}
      </el-tag>
      <el-tag :type="reasonType" size="small">
        {{ reasonLabel }}
      </el-tag>
    </div>

    <!-- 空历史 -->
    <el-empty v-if="!history.length" description="无辩论历史" :image-size="60" />

    <!-- 时间轴 -->
    <el-timeline v-else>
      <el-timeline-item
        v-for="round in history"
        :key="round.round"
        :timestamp="`第 ${round.round} 轮`"
        placement="top"
        :type="round.referee?.consensus ? 'success' : 'warning'"
      >
        <el-row :gutter="12">
          <!-- Bull 栏 -->
          <el-col :span="10">
            <div class="agent-card bull-card">
              <div class="agent-header">
                <span class="agent-name">🐂 多头</span>
                <el-tag :type="dirType(round.bull?.view?.direction)" size="small">
                  {{ dirLabel(round.bull?.view?.direction) }}
                </el-tag>
                <span class="confidence-text">
                  置信度 {{ pct(round.bull?.view?.confidence) }}
                </span>
              </div>
              <el-progress
                :percentage="pctNum(round.bull?.view?.confidence)"
                :color="'#67c23a'"
                :stroke-width="10"
                :show-text="false"
                style="margin: 6px 0"
              />
              <div v-if="round.bull?.view?.summary" class="agent-summary">
                {{ round.bull.view.summary }}
              </div>
              <el-collapse v-if="round.bull?.trace?.length" class="trace-collapse">
                <el-collapse-item :name="`bull-${round.round}`" title="推理轨迹">
                  <div v-for="(t, idx) in round.bull.trace" :key="idx" class="trace-item">
                    <div v-if="t.thought" class="trace-thought">
                      <el-tag size="small" type="info">思考</el-tag>
                      <span>{{ truncate(t.thought, 200) }}</span>
                    </div>
                    <div v-if="t.action" class="trace-action">
                      <el-tag size="small" type="warning">行动</el-tag>
                      <code>{{ t.action }}{{ t.action_args ? '(' + JSON.stringify(t.action_args) + ')' : '' }}</code>
                    </div>
                    <div v-if="t.observation" class="trace-obs">
                      <el-tag size="small" type="success">观察</el-tag>
                      <pre>{{ truncate(t.observation, 300) }}</pre>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>
              <div v-if="round.bull?.view?.key_evidence?.length" class="evidence-list">
                <div v-for="(ev, i) in round.bull.view.key_evidence" :key="i" class="evidence-item">
                  • {{ ev }}
                </div>
              </div>
            </div>
          </el-col>

          <!-- Bear 栏 -->
          <el-col :span="10">
            <div class="agent-card bear-card">
              <div class="agent-header">
                <span class="agent-name">🐻 空头</span>
                <el-tag :type="dirType(round.bear?.view?.direction)" size="small">
                  {{ dirLabel(round.bear?.view?.direction) }}
                </el-tag>
                <span class="confidence-text">
                  置信度 {{ pct(round.bear?.view?.confidence) }}
                </span>
              </div>
              <el-progress
                :percentage="pctNum(round.bear?.view?.confidence)"
                :color="'#f56c6c'"
                :stroke-width="10"
                :show-text="false"
                style="margin: 6px 0"
              />
              <div v-if="round.bear?.view?.summary" class="agent-summary">
                {{ round.bear.view.summary }}
              </div>
              <el-collapse v-if="round.bear?.trace?.length" class="trace-collapse">
                <el-collapse-item :name="`bear-${round.round}`" title="推理轨迹">
                  <div v-for="(t, idx) in round.bear.trace" :key="idx" class="trace-item">
                    <div v-if="t.thought" class="trace-thought">
                      <el-tag size="small" type="info">思考</el-tag>
                      <span>{{ truncate(t.thought, 200) }}</span>
                    </div>
                    <div v-if="t.action" class="trace-action">
                      <el-tag size="small" type="warning">行动</el-tag>
                      <code>{{ t.action }}{{ t.action_args ? '(' + JSON.stringify(t.action_args) + ')' : '' }}</code>
                    </div>
                    <div v-if="t.observation" class="trace-obs">
                      <el-tag size="small" type="success">观察</el-tag>
                      <pre>{{ truncate(t.observation, 300) }}</pre>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>
              <div v-if="round.bear?.view?.key_evidence?.length" class="evidence-list">
                <div v-for="(ev, i) in round.bear.view.key_evidence" :key="i" class="evidence-item">
                  • {{ ev }}
                </div>
              </div>
            </div>
          </el-col>

          <!-- Referee 栏 -->
          <el-col :span="4">
            <div class="agent-card referee-card">
              <div class="agent-header">
                <span class="agent-name">⚖️ 裁判</span>
              </div>
              <div v-if="round.referee">
                <el-tag :type="round.referee.consensus ? 'success' : 'danger'" size="small" effect="dark">
                  {{ round.referee.consensus ? '达成共识' : '仍有分歧' }}
                </el-tag>
                <div class="referee-gap">
                  置信度差: {{ round.referee.confidence_gap ?? '-' }}
                </div>
                <div class="referee-reason">
                  {{ round.referee.reason || '-' }}
                </div>
                <el-tag :type="round.referee.next_action === 'stop' ? 'success' : 'warning'" size="small">
                  {{ round.referee.next_action === 'stop' ? '终止辩论' : '继续辩论' }}
                </el-tag>
              </div>
              <div v-else class="no-referee">
                <el-text type="info" size="small">无裁判（跳过模式）</el-text>
              </div>
            </div>
          </el-col>
        </el-row>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  history: { type: Array, default: () => [] },
  rounds: { type: Number, default: 0 },
  actualRounds: { type: Number, default: 0 },
  terminatedReason: { type: String, default: '' },
  winner: { type: String, default: 'split' },
})

const winnerLabel = computed(() => {
  const m = { bullish: '多头胜 🐂', bearish: '空头胜 🐻', split: '势均力敌 ⚖️' }
  return m[props.winner] || props.winner
})

const winnerType = computed(() => {
  if (props.winner === 'bullish') return 'success'
  if (props.winner === 'bearish') return 'danger'
  return 'info'
})

const reasonLabel = computed(() => {
  const m = {
    consensus: '达成共识',
    max_rounds: '达到轮数上限',
    skipped: '跳过辩论',
    failed: '辩论失败',
  }
  return m[props.terminatedReason] || props.terminatedReason || '-'
})

const reasonType = computed(() => {
  if (props.terminatedReason === 'consensus') return 'success'
  if (props.terminatedReason === 'failed') return 'danger'
  return 'info'
})

function dirLabel(d) {
  const m = { long: '看多', short: '看空', neutral: '中性' }
  return m[d] || d || '-'
}

function dirType(d) {
  if (d === 'long') return 'success'
  if (d === 'short') return 'danger'
  return 'info'
}

function pct(v) {
  const n = Number(v)
  if (isNaN(n)) return '-'
  return (n * 100).toFixed(0) + '%'
}

function pctNum(v) {
  const n = Number(v)
  if (isNaN(n)) return 0
  return Math.round(n * 100)
}

function truncate(s, n) {
  if (!s) return ''
  const str = String(s)
  return str.length > n ? str.slice(0, n) + '...' : str
}
</script>

<style scoped>
.debate-history {
  margin-top: 8px;
}

.debate-status-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  align-items: center;
}

.agent-card {
  border: 1px solid var(--el-border-color-light, #e4e7ed);
  border-radius: 6px;
  padding: 10px;
  background: var(--el-bg-color-page, #fafafa);
  min-height: 100px;
}

.bull-card {
  border-left: 3px solid #67c23a;
}

.bear-card {
  border-left: 3px solid #f56c6c;
}

.referee-card {
  border-left: 3px solid #909399;
  text-align: center;
}

.agent-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.agent-name {
  font-weight: bold;
  font-size: 14px;
}

.confidence-text {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}

.agent-summary {
  font-size: 13px;
  margin: 6px 0;
  color: var(--el-text-color-primary, #303030);
  line-height: 1.5;
}

.trace-collapse {
  margin-top: 6px;
}

.trace-collapse :deep(.el-collapse-item__header) {
  font-size: 12px;
  height: 28px;
  line-height: 28px;
}

.trace-item {
  border-left: 2px solid var(--el-border-color-lighter, #ebeef5);
  padding-left: 8px;
  margin-bottom: 6px;
}

.trace-thought,
.trace-action,
.trace-obs {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 4px;
  font-size: 12px;
  word-break: break-word;
}

.trace-thought span,
.trace-obs pre {
  white-space: pre-wrap;
  margin: 0;
  font-family: inherit;
  flex: 1;
}

.evidence-list {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-regular, #606266);
}

.evidence-item {
  margin-bottom: 2px;
  line-height: 1.4;
}

.referee-gap,
.referee-reason {
  font-size: 11px;
  margin: 4px 0;
  color: var(--el-text-color-secondary, #909399);
}

.no-referee {
  margin-top: 8px;
}
</style>
