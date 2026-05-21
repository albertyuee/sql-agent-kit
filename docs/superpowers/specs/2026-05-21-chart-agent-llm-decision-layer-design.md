# Chart Agent LLM 决策层 — 设计文档

**日期**: 2026-05-21
**状态**: 待实现

---

## 背景

当前 `sql_agent/multi/chart.py` 的 `chart_node()` 是纯规则推断，全程未调用 LLM。图表类型选择 (`_infer_chart_type`) 和轴映射 (`_pick_x_col`, `_pick_y_col`) 均基于硬编码的关键词匹配和列类型判断，存在以下盲区：

- 列名英文缩写（`rev`, `cvr`, `roi`）无法被中文关键词匹配
- 多指标场景下 Y 轴选择仅靠关键词匹配，不理解语义
- 图表类型判断规则僵化，无法适应边界场景

## 目标

在 Chart Agent 中引入 LLM 决策层，负责图表类型选择 + 轴映射 + 标题生成，同时保留现有规则作为失败兜底。

## 设计方案

### 架构

```
chart_node()
  ├── 1. 选择绘图数据源（逻辑不变）
  │
  ├── 2. 🆕 LLM 决策层
  │     输入：intent + 列名&dtype + head(3) + describe()
  │     输出：{chart_type, x_col, y_col, color_col?, title, x_label?, y_label?}
  │     校验：col_name 存在性 + chart_type 合法性
  │     失败 → 规则兜底 + 日志警告
  │
  ├── 3. _build_figure()（适配 LLM 决策参数）
  │
  └── 4. 流式日志（同现有）
```

### LLM 决策层细节

**输入数据：**

| 字段 | 来源 | 说明 |
|------|------|------|
| intent | state.intent | 用户分析意图 |
| columns | df.dtypes + df.head(1) 样本值 | 列名 + 类型 + 示例值 |
| data_preview | df.head(3).to_dict() | 前 3 行预览 |
| stats | df.describe() | 数值列统计摘要 |

**输出 JSON：**

```json
{
  "chart_type": "line",
  "x_col": "月份",
  "y_col": "销售额",
  "color_col": null,
  "title": "各月销售额变化趋势",
  "x_label": "月份",
  "y_label": "销售额（元）"
}
```

**Prompt 设计要点：**

- system prompt 列出 8 种可用图表类型及其适用场景（line / area / bar / bar_stack / pie / scatter / funnel / heatmap / dual_axis / table）
- 强调 `x_col`、`y_col`、`color_col` 必须是 DataFrame 中实际存在的列名
- `table` 类型表示不适合生成图表

**校验规则：**

1. 输出必须为合法 JSON
2. `chart_type` 必须在已知类型列表中
3. `x_col`、`y_col` 必须在 df.columns 中
4. `color_col` 如果非空，必须在 df.columns 中
5. 任一校验不通过 → fallback

**LLM 配置：**

- 复用 `load_settings()` 中的 LLM provider 配置
- temperature = 0.0（确保确定性）
- 超时使用现有 LLM client 默认值

### `_build_figure()` 适配

新增可选参数 `chart_config: dict | None = None`：

- 当 `chart_config` 非空时，直接使用其中的 x_col / y_col / color_col / title / x_label / y_label
- 当 `chart_config` 为空时，保持现有的 `_pick_x_col` / `_pick_y_col` 推断逻辑（规则兜底）

### 流式日志

LLM 决策成功：

```
📊 [Chart Agent] 正在通过 AI 判断图表类型...
   ✅ AI 决策：line | X=月份 | Y=销售额 | 标题="各月销售趋势"
   数据：12 行 × 4 列
```

LLM 决策失败（任一校验不通过）：

```
📊 [Chart Agent] 正在通过 AI 判断图表类型...
   ⚠️ AI 决策异常（列名不匹配），回退规则推断
   ✅ 图表类型：line（规则推断）
   数据：12 行 × 4 列
```

### GraphState 变更

无需变更。LLM 决策信息仅用于 `_build_figure` 内部，不需要持久化到 state。

## 影响范围

| 文件 | 改动 | 说明 |
|------|------|------|
| `sql_agent/multi/chart.py` | `chart_node()` 新增 LLM 决策步骤 | 核心改动 |
| `sql_agent/multi/chart.py` | `_build_figure()` 新增 `chart_config` 参数 | 适配 LLM 输出 |
| 其他文件 | 无 | 不改动 |

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| LLM 调用增加延迟 1-3s | 仅一次调用，可接受；超时复用现有 client 超时 |
| LLM 返回不存在的列名 | 校验 + fallback 规则 |
| LLM 选择不合适的图表类型 | system prompt 明确各类型适用场景 |
| token 消耗增加 | 仅传 head(3) 和 describe()，不传全量数据 |

## 不在范围内

- 不修改 Planner 节点（不新增 chart_hint 字段）
- 不修改 GraphState 结构
- 不修改前端
- 不修改 streaming.py 的节点包装逻辑
