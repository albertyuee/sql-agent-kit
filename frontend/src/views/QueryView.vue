<template>
  <div>
    <div class="section-title">💬 单Agent查询</div>

    <div class="card">
      <div class="query-row">
        <input
          v-model="question"
          class="input"
          placeholder="输入自然语言问题，例如：查询销售额最高的前5个产品"
          @keydown.enter="runQuery"
        />
        <button class="btn btn--primary" :disabled="loading || !question.trim()" @click="runQuery">
          {{ loading ? '查询中...' : '查询' }}
        </button>
      </div>
    </div>

    <div v-if="started" class="card progress-card">
      <div class="progress-head">
        <div>
          <div class="label">⏱️ 查询进度</div>
          <div class="stage-current">
            当前阶段：{{ currentStageLabel }}
            <span v-if="loading" class="elapsed">已耗时 {{ elapsedSeconds }}s</span>
          </div>
        </div>
        <div class="progress-percent">{{ completedStageCount }}/{{ stages.length }}</div>
      </div>
      <div class="stage-steps">
        <div
          v-for="stage in stages"
          :key="stage.id"
          class="stage-step"
          :class="`stage-step--${stageStatus[stage.id]?.status || 'pending'}`"
        >
          <div class="stage-dot">{{ stageIcon(stage.id) }}</div>
          <div class="stage-info">
            <div class="stage-name">{{ stage.label }}</div>
            <div class="stage-desc">{{ stageSubtitle(stage.id) }}</div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="result" class="card">
      <div class="result-header">
        <span :class="['tag', statusTag]">{{ statusText }}</span>
        <span class="result-meta">置信度</span>
        <ConfidenceBar :value="result.confidence" style="width:120px" />
        <span class="result-meta">重试 {{ result.retry_count }} 次</span>
      </div>

      <div v-if="editableSql" style="margin-top:12px">
        <div class="label">生成的 SQL
          <span class="hint-text">（可直接编辑后重新执行）</span>
        </div>
        <SqlDisplay
          :sql="editableSql"
          :editable="true"
          :intent="question"
          @update:sql="editableSql = $event"
          @result="onSqlResult"
        />
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
          <button class="btn btn--ghost btn--sm" @click="editableSql = result.sql">还原</button>
          <span v-if="rerunError" class="error-hint">{{ rerunError }}</span>
        </div>
      </div>

      <div v-if="result.status === 'need_confirm'" class="alert alert--warn" style="margin-top:12px">
        置信度较低，请确认 SQL 后再执行。
      </div>

      <div v-if="result.error" class="alert alert--error" style="margin-top:12px">{{ result.error }}</div>

      <div v-if="displayData?.length" style="margin-top:16px">
        <div class="label">查询结果
          <span v-if="rerunResult" class="hint-text">（已更新为手动执行结果）</span>
        </div>
        <ResultTable :data="displayData" />
      </div>
    </div>

    <div v-if="error" class="alert alert--error">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, computed, shallowRef, onUnmounted } from 'vue'
import { queryApi } from '../api/index.js'
import SqlDisplay from '../components/SqlDisplay.vue'
import ResultTable from '../components/ResultTable.vue'
import ConfidenceBar from '../components/ConfidenceBar.vue'

const question = ref('')
const loading = ref(false)
const started = ref(false)
const result = ref(null)
const error = ref('')
const editableSql = ref('')
const rerunResult = shallowRef(null)
const rerunError = ref('')
let es = null

const statusTag = ref('tag--info')
const statusText = ref('')
const stages = [
  { id: 'schema', label: '读取Schema' },
  { id: 'fewshot', label: '检索示例' },
  { id: 'generate_sql', label: '生成SQL' },
  { id: 'validate', label: '校验SQL' },
  { id: 'execute', label: '执行查询' },
  { id: 'chart', label: '生成图表' },
]
const stageStatus = ref(createInitialStageStatus())
const currentStage = ref('')
const elapsedSeconds = ref(0)
let startedAt = 0
let elapsedTimer = null

const displayData = computed(() => rerunResult.value?.data ?? result.value?.data ?? [])
const completedStageCount = computed(() => stages.filter(s => stageStatus.value[s.id]?.status === 'done').length)
const currentStageLabel = computed(() => {
  if (!loading.value && result.value) return '已完成'
  if (!loading.value && !result.value) return '未开始'
  const stage = stages.find(s => s.id === currentStage.value)
  return stage?.label || '准备中'
})

onUnmounted(() => {
  es?.close()
  es = null
  stopElapsedTimer()
})

function createInitialStageStatus() {
  return Object.fromEntries(stages.map(s => [s.id, { status: 'pending', elapsed_ms: null }]))
}

function startElapsedTimer() {
  stopElapsedTimer()
  startedAt = Date.now()
  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
  }, 1000)
}

function stopElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function stageIcon(id) {
  const status = stageStatus.value[id]?.status || 'pending'
  if (status === 'done') return '✓'
  if (status === 'running') return '…'
  if (status === 'error') return '!'
  return ''
}

function stageSubtitle(id) {
  const s = stageStatus.value[id] || {}
  if (s.status === 'running') return '进行中'
  if (s.status === 'done') return s.elapsed_ms ? `${(s.elapsed_ms / 1000).toFixed(1)}s` : '已完成'
  if (s.status === 'error') return '失败'
  return '等待中'
}

function handleStageEvent(payload) {
  const source = payload.source
  if (!source || !stageStatus.value[source]) return
  stageStatus.value = {
    ...stageStatus.value,
    [source]: {
      status: payload.status,
      elapsed_ms: payload.elapsed_ms ?? stageStatus.value[source].elapsed_ms,
    },
  }
  if (payload.status === 'running') currentStage.value = source
}

function applyResult(data) {
  result.value = data
  editableSql.value = data.sql || ''
  if (data.status === 'success') { statusTag.value = 'tag--success'; statusText.value = '查询成功' }
  else if (data.status === 'need_confirm') { statusTag.value = 'tag--warn'; statusText.value = '需要确认' }
  else { statusTag.value = 'tag--error'; statusText.value = '查询失败' }
}

function runQuery() {
  if (!question.value.trim() || loading.value) return
  loading.value = true
  started.value = true
  result.value = null
  rerunResult.value = null
  rerunError.value = ''
  error.value = ''
  editableSql.value = ''
  stageStatus.value = createInitialStageStatus()
  currentStage.value = ''
  startElapsedTimer()

  es?.close()
  es = queryApi.stream(question.value.trim())

  es.addEventListener('stage', (e) => {
    try { handleStageEvent(JSON.parse(e.data)) } catch {}
  })

  es.addEventListener('result', (e) => {
    try { applyResult(JSON.parse(e.data)) } catch (err) { error.value = err.message }
  })

  es.addEventListener('error', (e) => {
    try {
      const { message } = JSON.parse(e.data)
      error.value = message || '查询失败'
      if (currentStage.value && stageStatus.value[currentStage.value]) {
        handleStageEvent({ source: currentStage.value, status: 'error' })
      }
    } catch {}
  })

  es.addEventListener('done', () => {
    loading.value = false
    stopElapsedTimer()
    es?.close()
    es = null
  })

  es.onerror = () => {
    loading.value = false
    stopElapsedTimer()
    es?.close()
    es = null
  }
}

function onSqlResult(data) {
  if (!data.success) {
    rerunError.value = data.error || '执行失败'
    return
  }
  rerunError.value = ''
  rerunResult.value = data
}
</script>

<style scoped>
.query-row { display: flex; gap: 10px; }
.progress-card { border-left: 4px solid #7c83fd; }
.progress-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 14px; }
.stage-current { margin-top: 4px; font-size: 13px; color: #555; }
.elapsed { margin-left: 8px; color: #888; }
.progress-percent { font-size: 13px; color: #7c83fd; font-weight: 700; }
.stage-steps { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.stage-step { display: flex; gap: 8px; align-items: center; padding: 10px; border: 1px solid #eee; border-radius: 10px; background: #fafafa; min-width: 0; }
.stage-dot { width: 22px; height: 22px; border-radius: 50%; background: #e5e7eb; color: #888; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.stage-info { min-width: 0; }
.stage-name { font-size: 13px; font-weight: 600; color: #333; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stage-desc { margin-top: 2px; font-size: 12px; color: #999; }
.stage-step--running { border-color: #7c83fd; background: #f5f6ff; }
.stage-step--running .stage-dot { background: #7c83fd; color: #fff; animation: pulse 1s infinite; }
.stage-step--done { border-color: #d9f7be; background: #f6ffed; }
.stage-step--done .stage-dot { background: #52c41a; color: #fff; }
.stage-step--error { border-color: #ffd6d6; background: #fff2f0; }
.stage-step--error .stage-dot { background: #ff4d4f; color: #fff; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .55; } }
@media (max-width: 1100px) { .stage-steps { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.result-header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.result-meta { font-size: 12px; color: #888; }
.hint-text { font-size: 12px; color: #aaa; font-weight: normal; margin-left: 6px; }
.error-hint { font-size: 12px; color: #e74c3c; }
</style>
