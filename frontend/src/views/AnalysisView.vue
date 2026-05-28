<template>
  <div>
    <div class="section-title">🧠 多Agent智能分析</div>

    <!-- 输入区 -->
    <div class="card">
      <div class="query-row">
        <input
          v-model="question"
          class="input"
          placeholder="输入分析问题，例如：分析上个月各品类的销售趋势"
          @keydown.enter="startAnalysis"
        />
        <button class="btn btn--primary" :disabled="streaming || !question.trim()" @click="startAnalysis">
          {{ streaming ? '分析中...' : '开始分析' }}
        </button>
        <button v-if="streaming" class="btn btn--ghost" @click="stopAnalysis">停止</button>
      </div>

      <!-- 分析建议 -->
      <div class="suggestions-row">
        <button class="btn btn--ghost btn--sm" :disabled="loadingSuggestions" @click="fetchSuggestions">
          {{ loadingSuggestions ? '生成中...' : '💡 获取分析建议' }}
        </button>
        <div v-if="suggestions.length" class="suggestion-chips">
          <span
            v-for="(s, i) in suggestions"
            :key="i"
            class="chip"
            @click="question = s"
          >{{ s }}</span>
        </div>
      </div>
    </div>

    <template v-if="started">

      <!-- 阶段进度 -->
      <div class="card progress-card">
        <div class="progress-head">
          <div>
            <div class="label">⏱️ 分析进度</div>
            <div class="stage-current">
              当前阶段：{{ currentStageLabel }}
              <span v-if="streaming" class="elapsed">已耗时 {{ elapsedSeconds }}s</span>
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

      <!-- 过程日志 -->
      <div class="two-col">
        <div class="card" style="flex:1">
          <div class="label">📋 分析过程</div>
          <ProcessLog :logs="analysisLogs" />
        </div>
        <div class="card" style="flex:1">
          <div class="label">📊 做图过程</div>
          <ProcessLog :logs="chartLogs" />
        </div>
      </div>

      <!-- 子问题结果 tab 区域 -->
      <div class="card" v-if="result || streaming">
        <!-- 多子问题时显示 tab 栏 -->
        <div v-if="sqlResults.length > 1" class="tab-bar">
          <button
            v-for="(r, i) in sqlResults"
            :key="i"
            class="tab-btn"
            :class="{ 'tab-btn--active': activeTab === i }"
            @click="activeTab = i"
          >
            {{ r.success === false ? '❌' : '✅' }} 子问题 {{ i + 1 }}
            <span v-if="i === chartSourceIndex" class="tab-chart-badge">📊</span>
          </button>
        </div>

        <!-- 当前 tab 内容 -->
        <template v-if="currentResult">
          <!-- SQL -->
          <div class="label" :style="sqlResults.length > 1 ? 'margin-top:12px' : ''">🔍 执行的 SQL</div>
          <div v-if="currentResult.sql">
            <SqlDisplay
              :sql="currentResult.sql"
              :editable="!streaming"
              :intent="result?.intent || question"
              @result="(d) => handleSqlResult(d, activeTab)"
            />
          </div>
          <div v-else class="placeholder">{{ streaming ? '等待 SQL 生成...' : '无 SQL' }}</div>

          <!-- 查询结果 -->
          <div class="label" style="margin-top:16px">
            查询结果
            <span v-if="currentResult.data?.length" class="row-count">{{ currentResult.data.length }} 行</span>
            <span v-if="currentResult.truncated" class="row-count" style="color:#f59e0b">（仅显示前 500 行）</span>
          </div>
          <div v-if="currentResult.data?.length">
            <ResultTable :data="currentResult.data" />
          </div>
          <div v-else-if="tabSqlErrors[activeTab]" class="alert alert--error" style="margin-top:8px">
            {{ tabSqlErrors[activeTab] }}
          </div>
          <div v-else-if="currentResult.success === false" class="alert alert--error" style="margin-top:8px">
            {{ currentResult.error || '查询失败' }}
          </div>
          <div v-else class="placeholder">{{ streaming ? '等待查询结果...' : '无数据' }}</div>

          <!-- 图表（仅对应 tab 显示） -->
          <template v-if="activeTab === chartSourceIndex">
            <div v-if="chartData" style="margin-top:16px">
              <div class="label">📈 数据图表</div>
              <PlotlyChart :chart-data="chartData" />
            </div>
            <div v-else-if="result && !chartData && !streaming" style="margin-top:16px">
              <div class="label">📈 数据图表</div>
              <div class="placeholder">数据不适合生成图表，请查看做图过程了解原因</div>
            </div>
          </template>
        </template>

        <!-- streaming 且还没有任何结果时的占位 -->
        <template v-else-if="streaming">
          <div class="label">🔍 执行的 SQL</div>
          <div class="placeholder">等待 SQL 生成...</div>
        </template>
      </div>

      <!-- 分析结论 + 质量评分 -->
      <div class="two-col">
        <div class="card" style="flex:1">
          <div class="label">💡 分析结论</div>
          <p v-if="result?.summary" class="summary-text">{{ result.summary }}</p>
          <div v-else class="placeholder">{{ streaming ? '等待结论生成...' : '无结论' }}</div>
        </div>
        <div class="card" style="width:220px;flex-shrink:0">
          <div class="label">🏅 质量评分</div>
          <JudgeScores v-if="result?.judge_scores" :scores="result.judge_scores" />
          <div v-else class="placeholder">{{ streaming ? '等待评分...' : '-' }}</div>
        </div>
      </div>

    </template>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { analysisApi, suggestionsApi } from '../api/index.js'
import ProcessLog from '../components/ProcessLog.vue'
import JudgeScores from '../components/JudgeScores.vue'
import SqlDisplay from '../components/SqlDisplay.vue'
import PlotlyChart from '../components/PlotlyChart.vue'
import ResultTable from '../components/ResultTable.vue'

const question = ref('')
const streaming = ref(false)
const started = ref(false)
const result = ref(null)
const chartData = ref(null)
const chartSourceIndex = ref(0)
const sqlResults = ref([])       // 所有子问题结果
const activeTab = ref(0)         // 当前激活的 tab
const tabSqlErrors = ref({})     // 每个 tab 手动重跑 SQL 的错误
let es = null

const analysisLogs = ref([])
const chartLogs = ref([])
const suggestions = ref([])
const loadingSuggestions = ref(false)
const CHART_SOURCES = new Set(['chart'])
const stages = [
  { id: 'classifier', label: '意图识别' },
  { id: 'planner', label: '任务规划' },
  { id: 'sql', label: 'SQL生成与执行' },
  { id: 'chart', label: '图表生成' },
  { id: 'summary', label: '结论生成' },
  { id: 'judge', label: '质量评分' },
]
const stageStatus = ref(createInitialStageStatus())
const currentStage = ref('')
const elapsedSeconds = ref(0)
let startedAt = 0
let elapsedTimer = null

// 当前 tab 对应的结果
const currentResult = computed(() => sqlResults.value[activeTab.value] || null)
const completedStageCount = computed(() => stages.filter(s => stageStatus.value[s.id]?.status === 'done').length)
const currentStageLabel = computed(() => {
  if (!streaming.value && result.value) return '已完成'
  if (!streaming.value && !result.value) return '未开始'
  const stage = stages.find(s => s.id === currentStage.value)
  return stage?.label || '准备中'
})

// 组件卸载时关闭 SSE（修复5：切换页面不关闭连接）
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

async function fetchSuggestions() {
  loadingSuggestions.value = true
  try {
    const { data } = await suggestionsApi.get()
    suggestions.value = data.suggestions || []
  } finally {
    loadingSuggestions.value = false
  }
}

async function handleSqlResult(data, tabIndex) {
  tabSqlErrors.value = { ...tabSqlErrors.value, [tabIndex]: '' }
  if (data.success) {
    const updated = [...sqlResults.value]
    updated[tabIndex] = { ...updated[tabIndex], data: data.data || [], sql: data.sql, success: true, error: '' }
    sqlResults.value = updated
    if (tabIndex === chartSourceIndex.value) {
      chartData.value = data.chart || null
    }
  } else {
    tabSqlErrors.value = { ...tabSqlErrors.value, [tabIndex]: data.error || '执行失败' }
    if (tabIndex === chartSourceIndex.value) {
      chartData.value = null
    }
  }
}

function startAnalysis() {
  if (!question.value.trim() || streaming.value) return
  analysisLogs.value = []
  chartLogs.value = []
  result.value = null
  chartData.value = null
  sqlResults.value = []
  activeTab.value = 0
  tabSqlErrors.value = {}
  chartSourceIndex.value = 0
  stageStatus.value = createInitialStageStatus()
  currentStage.value = ''
  started.value = true
  streaming.value = true
  startElapsedTimer()

  es = analysisApi.stream(question.value.trim())

  es.addEventListener('log', (e) => {
    const { text, source } = JSON.parse(e.data)
    if (CHART_SOURCES.has(source)) {
      chartLogs.value.push(text)
    } else {
      analysisLogs.value.push(text)
    }
  })

  es.addEventListener('stage', (e) => {
    try {
      handleStageEvent(JSON.parse(e.data))
    } catch {}
  })

  es.addEventListener('sql_result', (e) => {
    try {
      const parsed = JSON.parse(e.data)
      if (parsed.sql_results?.length) {
        sqlResults.value = parsed.sql_results
        // 增量数据先到，提前显示表格（不等待 result 事件）
        if (!result.value) {
          result.value = { sql_results: parsed.sql_results }
        }
      }
    } catch {}
  })

  es.addEventListener('chart', (e) => {
    try {
      const parsed = JSON.parse(e.data)
      chartData.value = parsed.chart || null
      if (parsed.chart_source_index !== undefined) {
        chartSourceIndex.value = parsed.chart_source_index
        if (activeTab.value === 0) {
          activeTab.value = parsed.chart_source_index
        }
      }
    } catch {}
  })

  es.addEventListener('result', (e) => {
    try {
      const parsed = JSON.parse(e.data)
      result.value = parsed
      // 只更新 summary/judge_scores/error，sql_results 优先使用增量事件的版本
      if (parsed.sql_results?.length && !sqlResults.value.length) {
        sqlResults.value = parsed.sql_results
      }
      chartSourceIndex.value = parsed.chart_source_index ?? 0
    } catch (err) {
      console.error('result parse error:', err, e.data?.slice(0, 200))
    }
  })

  es.addEventListener('error', (e) => {
    try {
      const { message } = JSON.parse(e.data)
      analysisLogs.value.push(`❌ 错误：${message}`)
      if (currentStage.value && stageStatus.value[currentStage.value]) {
        handleStageEvent({ source: currentStage.value, status: 'error' })
      }
    } catch {}
  })

  es.addEventListener('done', () => {
    streaming.value = false
    stopElapsedTimer()
    es?.close()
    es = null
  })

  es.onerror = () => {
    // 修复1：无论 readyState 是 CLOSED 还是 CONNECTING，都终止流式状态
    // CONNECTING 表示浏览器在自动重连，但我们的管道是一次性的，不需要重连
    streaming.value = false
    stopElapsedTimer()
    es?.close()
    es = null
  }
}

function stopAnalysis() {
  streaming.value = false
  stopElapsedTimer()
  es?.close()
  es = null
}
</script>

<style scoped>
.query-row { display: flex; gap: 10px; }
.suggestions-row { display: flex; align-items: flex-start; gap: 12px; margin-top: 12px; flex-wrap: wrap; }
.suggestion-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  display: inline-block;
  padding: 5px 12px;
  background: #f0f1ff;
  color: #7c83fd;
  border-radius: 16px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid #e0e1ff;
  transition: background .15s;
  white-space: nowrap;
}
.chip:hover { background: #e0e1ff; }
.two-col { display: flex; gap: 16px; align-items: flex-start; }
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
.summary-text { font-size: 14px; line-height: 1.8; color: #333; margin-top: 4px; }
.placeholder { color: #bbb; font-size: 13px; padding: 12px 0; }
.row-count {
  font-size: 12px;
  font-weight: normal;
  color: #888;
  margin-left: 6px;
}
.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid #e8e8e8;
  margin-bottom: 4px;
}
.tab-btn {
  padding: 6px 14px;
  font-size: 13px;
  border: none;
  background: none;
  cursor: pointer;
  color: #888;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color .15s, border-color .15s;
  display: flex;
  align-items: center;
  gap: 4px;
}
.tab-btn:hover { color: #7c83fd; }
.tab-btn--active { color: #7c83fd; border-bottom-color: #7c83fd; font-weight: 500; }
.tab-chart-badge { font-size: 12px; }
</style>
