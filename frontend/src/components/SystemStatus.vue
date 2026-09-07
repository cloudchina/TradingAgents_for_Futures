<template>
  <div class="system-status">
    <el-tooltip content="百炼API" placement="bottom">
      <el-tag :type="status.bailian_api_configured ? 'success' : 'danger'" size="small" effect="dark">
        <el-icon><Cpu /></el-icon>
        {{ status.bailian_api_configured ? 'LLM' : 'LLM✗' }}
      </el-tag>
    </el-tooltip>
    <el-tooltip content="Serper搜索API" placement="bottom">
      <el-tag :type="status.serper_api_configured ? 'success' : 'danger'" size="small" effect="dark">
        <el-icon><Search /></el-icon>
        {{ status.serper_api_configured ? '搜索' : '搜索✗' }}
      </el-tag>
    </el-tooltip>
    <el-tooltip content="Word报告" placement="bottom">
      <el-tag :type="status.docx_available ? 'success' : 'info'" size="small" effect="dark">
        <el-icon><Document /></el-icon>
        Word
      </el-tag>
    </el-tooltip>
    <el-tooltip content="缓存数量" placement="bottom">
      <el-tag type="info" size="small" effect="dark">
        <el-icon><Files /></el-icon>
        {{ status.cache_count }}
      </el-tag>
    </el-tooltip>
    <el-tooltip v-if="status.scheduler_running" content="定时分析运行中" placement="bottom">
      <el-tag type="warning" size="small" effect="dark">
        <el-icon class="is-loading"><Loading /></el-icon>
        定时
      </el-tag>
    </el-tooltip>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'

// 🔧 修复(A1)：统一走 Pinia store，消除组件直连 API 的重复实现
const store = useAppStore()
const status = computed(() => store.systemStatus)

let timer = null
onMounted(() => {
  store.fetchSystemStatus()
  timer = setInterval(() => store.fetchSystemStatus(), 30000)
})
</script>

<style scoped>
.system-status {
  display: flex;
  gap: 8px;
  align-items: center;
}
.system-status .el-tag {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
