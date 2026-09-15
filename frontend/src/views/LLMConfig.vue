<template>
  <div class="llm-config">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="card-title">🧠 LLM 服务配置</span>
          <el-tag :type="form.configured ? 'success' : 'danger'" effect="dark" size="small">
            {{ form.configured ? '已配置' : '未配置 API Key' }}
          </el-tag>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="block-tip">
        <template #title>
          保存后<strong>立即生效</strong>，无需重启服务。手动分析、多空辩论、定时分析都会使用这里的 Base URL / API Key / 模型。
        </template>
      </el-alert>

      <el-form :model="form" label-width="120px" class="llm-form" v-loading="loading">
        <el-form-item label="Base URL">
          <el-input
            v-model="form.base_url"
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
            clearable
          />
          <div class="hint">
            OpenAI 兼容接口地址（通常以 <code>/v1</code> 结尾）。支持阿里云百炼、DeepSeek、OpenAI 及任意兼容 OpenAI 协议的服务。
          </div>
        </el-form-item>

        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            placeholder="sk-xxxxxxxxxxxxxxxx"
            clearable
            autocomplete="new-password"
          />
          <div class="hint">
            当前：{{ form.api_key_masked || '未配置' }}。密钥仅保存在服务端配置文件中，不会写入代码仓库。
          </div>
        </el-form-item>

        <el-form-item label="模型名称">
          <el-select
            v-model="form.model"
            filterable
            allow-create
            default-first-option
            placeholder="选择或直接输入模型 ID"
            style="width: 100%"
          >
            <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
          </el-select>
          <div class="hint">
            可手动输入任意模型 ID；也可点击「拉取模型列表」从当前 Base URL 读取可用模型。
          </div>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="handleSave">
            <el-icon><Check /></el-icon> 保存并生效
          </el-button>
          <el-button :loading="testing" @click="handleTest">
            <el-icon><Connection /></el-icon> 测试连接
          </el-button>
          <el-button :loading="modelLoading" @click="handleFetchModels">
            <el-icon><Refresh /></el-icon> 拉取模型列表
          </el-button>
          <el-button type="danger" plain :loading="resetting" @click="handleReset">
            恢复默认
          </el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        :closable="false"
        show-icon
        class="block-tip"
      >
        <template #title>
          {{ testResult.ok ? '✅' : '❌' }} {{ testResult.message }}
          <span v-if="testResult.latency_ms">（耗时 {{ testResult.latency_ms }} ms）</span>
        </template>
      </el-alert>

      <el-descriptions title="当前生效信息" :column="1" border size="small">
        <el-descriptions-item label="配置来源">
          {{ form.source === 'file' ? '配置文件（前端保存）' : '环境变量 / .env 默认值' }}
        </el-descriptions-item>
        <el-descriptions-item label="生效模型">{{ form.model || '-' }}</el-descriptions-item>
        <el-descriptions-item label="持久化文件">
          <code>{{ form.config_path || '-' }}</code>
        </el-descriptions-item>
        <el-descriptions-item label="最近更新">{{ form.updated_at || '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider content-position="left">📦 两种部署模式说明</el-divider>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="直接启动">
          <code>npm start</code> 时配置写入 <code>backend/data/config/llm_config.json</code>
        </el-descriptions-item>
        <el-descriptions-item label="Docker">
          <code>docker compose up</code> 时配置写入容器内 <code>/app/data/config/llm_config.json</code>，
          落在 <code>futures-data</code> 命名卷中，容器重建/升级不丢失
        </el-descriptions-item>
        <el-descriptions-item label="自定义路径">
          设置环境变量 <code>LLM_CONFIG_PATH</code> 可覆盖配置文件位置
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'

const store = useAppStore()

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const resetting = ref(false)
const modelLoading = ref(false)
const testResult = ref(null)

const PRESET_MODELS = ['qwen-plus', 'qwen-max', 'qwen-turbo', 'deepseek-chat', 'gpt-4o-mini']
const modelOptions = ref([...PRESET_MODELS])

const form = reactive({
  base_url: '',
  api_key: '',
  api_key_masked: '',
  model: '',
  configured: false,
  source: 'env',
  config_path: '',
  updated_at: '',
})

function applyConfig(cfg) {
  if (!cfg) return
  form.base_url = cfg.base_url || ''
  form.api_key = cfg.api_key || ''
  form.api_key_masked = cfg.api_key_masked || ''
  form.model = cfg.model || ''
  form.configured = !!cfg.configured
  form.source = cfg.source || 'env'
  form.config_path = cfg.config_path || ''
  form.updated_at = cfg.updated_at || ''
  for (const m of PRESET_MODELS) {
    if (!modelOptions.value.includes(m)) modelOptions.value.push(m)
  }
  if (form.model && !modelOptions.value.includes(form.model)) {
    modelOptions.value.unshift(form.model)
  }
}

async function loadConfig() {
  loading.value = true
  try {
    applyConfig(await store.fetchLlmConfig())
  } catch (e) {
    ElMessage.error('加载 LLM 配置失败')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  if (!form.base_url.trim()) {
    ElMessage.warning('请填写 Base URL')
    return
  }
  if (!form.model.trim()) {
    ElMessage.warning('请填写模型名称')
    return
  }
  saving.value = true
  try {
    const res = await store.saveLlmConfig({
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      model: form.model.trim(),
    })
    applyConfig(res?.config || store.llmConfig)
    testResult.value = null
    ElMessage.success(res?.message || 'LLM 配置已保存并立即生效')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function handleTest() {
  testing.value = true
  testResult.value = null
  try {
    // 用页面上的取值测试（可能尚未保存），便于先验证再保存
    testResult.value = await store.testLlmConfig({
      base_url: form.base_url.trim() || undefined,
      api_key: form.api_key.trim() || undefined,
      model: form.model.trim() || undefined,
    })
  } catch (e) {
    testResult.value = { ok: false, message: '测试失败: ' + (e.response?.data?.detail || e.message) }
  } finally {
    testing.value = false
  }
}

async function handleFetchModels() {
  modelLoading.value = true
  try {
    const res = await store.fetchLlmModels({
      base_url: form.base_url.trim() || undefined,
      api_key: form.api_key.trim() || undefined,
    })
    if (!res?.ok) {
      ElMessage.error(res?.message || '获取模型列表失败')
      return
    }
    const ids = res.models || []
    if (!ids.length) {
      ElMessage.warning('服务端未提供模型列表')
      return
    }
    const merged = [...new Set([...ids, ...modelOptions.value])]
    modelOptions.value = merged
    ElMessage.success(`已获取 ${ids.length} 个模型`)
  } catch (e) {
    ElMessage.error('获取模型列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    modelLoading.value = false
  }
}

async function handleReset() {
  try {
    await ElMessageBox.confirm(
      '将删除已保存的 LLM 配置，恢复为环境变量（DASHSCOPE_API_KEY / BAILIAN_BASE_URL / LLM_MODEL）中的默认值。是否继续？',
      '恢复默认配置',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  resetting.value = true
  try {
    const res = await store.resetLlmConfig()
    applyConfig(res?.config || store.llmConfig)
    testResult.value = null
    ElMessage.success(res?.message || '已恢复为默认配置')
  } catch (e) {
    ElMessage.error('恢复失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    resetting.value = false
  }
}

onMounted(loadConfig)
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
.llm-form {
  max-width: 760px;
  margin-top: 8px;
}
.block-tip {
  margin-bottom: 18px;
}
.hint {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
  line-height: 1.6;
}
.hint code,
:deep(code) {
  background: #f5f7fa;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
}
</style>
