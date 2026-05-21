# Chart Agent LLM 决策层 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `chart_node()` 中新增 LLM 决策步骤，让 LLM 根据数据结构和意图选择图表类型、轴映射、标题，失败时规则兜底。

**Architecture:** 新增 `_llm_chart_decision()` 函数调用 LLM 出 JSON 决策参数；`_build_figure()` 新增 `chart_config` 可选参数接收决策结果；`chart_node()` 在绘图前先尝试 LLM 决策。

**Tech Stack:** Python, LangGraph, Plotly, pandas

**影响文件:** 仅 `sql_agent/multi/chart.py`

---

### Task 1: 新增 `_llm_chart_decision()` 函数

**Files:**
- Modify: `sql_agent/multi/chart.py`（在 `_should_show_label` 之后、`_build_figure` 之前插入）

- [ ] **Step 1: 插入 `_llm_chart_decision()` 函数**

在 `_should_show_label` 函数定义（约第 144 行）之后、`_build_figure` 函数定义（约第 147 行）之前，插入以下代码：

```python
def _llm_chart_decision(df, intent: str, settings: dict) -> dict | None:
    """调用 LLM 决策图表类型、轴映射和标题。成功返回 dict，失败返回 None。"""
    import json
    import re
    from sql_agent.llm import get_llm_client

    # 构建列信息（列名 + 类型 + 样本值）
    col_lines = []
    for col in df.columns:
        dtype_str = str(df[col].dtype)
        sample_val = "N/A"
        if len(df) > 0:
            try:
                sample_val = str(df[col].iloc[0])
            except Exception:
                pass
        col_lines.append(f"  - {col} (dtype: {dtype_str}, 样本: {sample_val})")

    head_text = df.head(3).to_string(index=False) if len(df) > 0 else "（空数据）"

    try:
        stats_text = df.describe(include="all").to_string()
    except Exception:
        stats_text = "无法生成统计摘要"

    system_prompt = (
        "你是数据可视化专家。根据提供的 DataFrame 结构、数据预览、统计摘要和分析意图，"
        "选择最合适的图表配置。\n\n"
        "可用图表类型及典型场景：\n"
        "- line: 时间序列趋势，X 轴为时间/日期列\n"
        "- area: 面积趋势图，强调累积变化\n"
        "- bar: 分类对比，X 轴为分类列，Y 轴为数值列\n"
        "- bar_stack: 多指标堆叠对比，同量纲多数值列\n"
        "- pie: 占比分布，≤6 个分类 + 单数值列\n"
        "- scatter: 两数值列的相关性/分布分析\n"
        "- funnel: 转化漏斗，含阶段/步骤列\n"
        "- heatmap: 两个分类维度的交叉矩阵\n"
        "- dual_axis: 双 Y 轴，同时展示量和率（如销售额 + 转化率）\n"
        "- table: 数据不适合图表展示（列数过多/无有意义的图表映射）\n\n"
        "要求：\n"
        "1. x_col / y_col 必须是 DataFrame 中真实存在的列名，不能编造\n"
        "2. color_col 是可选的，仅当有自然分组维度时填写，否则填 null\n"
        "3. title / x_label / y_label 用中文\n"
        "4. 只输出 JSON，不要任何解释\n\n"
        '输出格式：\n'
        '{"chart_type": "bar", "x_col": "类别", "y_col": "销售额", "color_col": null, "title": "各类别销售额对比", "x_label": "类别", "y_label": "销售额（元）"}'
    )

    user_content = (
        f"分析意图：{intent}\n\n"
        f"DataFrame 列信息：\n{chr(10).join(col_lines)}\n\n"
        f"数据预览（前 3 行）：\n{head_text}\n\n"
        f"统计摘要：\n{stats_text}\n\n"
        "请输出图表配置 JSON："
    )

    try:
        llm = get_llm_client(settings["llm"])
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        raw = llm.chat(messages, temperature=0.0)

        match = re.search(r"\{[\s\S]+\}", raw)
        if not match:
            return None
        config = json.loads(match.group())

        # 校验 chart_type 合法性
        valid_types = {
            "line", "area", "bar", "bar_stack", "pie",
            "scatter", "funnel", "heatmap", "dual_axis", "table",
        }
        if config.get("chart_type") not in valid_types:
            return None

        # 校验列名真实存在
        if config.get("x_col") not in df.columns:
            return None
        if config.get("y_col") not in df.columns:
            return None
        color = config.get("color_col")
        if color and color not in df.columns:
            return None

        return config

    except Exception:
        return None
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "import sql_agent.multi.chart; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add sql_agent/multi/chart.py
git commit -m "feat: add _llm_chart_decision() function to Chart Agent"
```

---

### Task 2: 修改 `_build_figure()` 接受 `chart_config` 参数

**Files:**
- Modify: `sql_agent/multi/chart.py` — `_build_figure()` 函数签名及内部逻辑

- [ ] **Step 1: 修改函数签名**

将第 147 行：

```python
def _build_figure(df, chart_type: str, title: str, intent: str = "") -> str:
```

改为：

```python
def _build_figure(df, chart_type: str, title: str, intent: str = "",
                  chart_config: dict | None = None) -> str:
```

- [ ] **Step 2: 在 `_build_figure` 函数体内，用 `chart_config` 替换轴推断逻辑**

找到 `_build_figure` 内部的变量声明区域（约第 156-167 行），当前代码为：

```python
    cols = df.columns.tolist()
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    time_cols = [c for c in cols if any(kw in c.lower()
                  for kw in ["date", "time", "month", "year", "day", "日", "月", "年"])]
    cat_cols = [c for c in cols if c not in numeric_cols and c not in time_cols]
    effective_time_cols = [c for c in time_cols if df[c].nunique() > 1]

    x_col = _pick_x_col(df, chart_type)
    y_col = _pick_y_col(numeric_cols, intent, chart_type) or (numeric_cols[-1] if numeric_cols else cols[-1])
    show_label = _should_show_label(df)
    sort_order = _should_sort(chart_type)
```

替换为：

```python
    cols = df.columns.tolist()
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    time_cols = [c for c in cols if any(kw in c.lower()
                  for kw in ["date", "time", "month", "year", "day", "日", "月", "年"])]
    cat_cols = [c for c in cols if c not in numeric_cols and c not in time_cols]
    effective_time_cols = [c for c in time_cols if df[c].nunique() > 1]

    if chart_config:
        # LLM 决策模式：直接用 LLM 给出的轴映射和标题
        x_col = chart_config["x_col"]
        y_col = chart_config["y_col"]
        title = chart_config.get("title", title)
        # color_col 用于 hue/分组，传给各 chart_type 分支使用
        _llm_color = chart_config.get("color_col")
    else:
        # 规则推断模式（现有逻辑）
        x_col = _pick_x_col(df, chart_type)
        y_col = _pick_y_col(numeric_cols, intent, chart_type) or (numeric_cols[-1] if numeric_cols else cols[-1])
        _llm_color = None

    show_label = _should_show_label(df)
    sort_order = _should_sort(chart_type)
```

- [ ] **Step 3: 在 chart_type 分支中使用 `_llm_color` 作为分组/颜色列**

在各 chart_type 分支（line/area/bar/bar_stack 等）中，当 `_llm_color` 非空时，优先用它作为 color/hue 列。

找到 `_build_figure` 中下述各分支的 `color_col` / 分组变量声明处，按以下规则修改：

**line 分支**（约第 253-277 行）：将决定 `color_col` 的行改为优先使用 `_llm_color`：

```python
    if chart_type == "line":
        df, parsed = _try_parse_datetime(df, x_col)
        df = df.sort_values(x_col)
        color_col = _llm_color or (cat_cols[0] if cat_cols else None)
```

**area 分支**（约第 279-288 行）：同理：

```python
    elif chart_type == "area":
        df, _ = _try_parse_datetime(df, x_col)
        df = df.sort_values(x_col)
        color_col = _llm_color or (cat_cols[0] if cat_cols else None)
```

**bar 分支**（约第 290-296 行）：同理：

```python
    elif chart_type == "bar":
        time_as_color = time_cols[0] if time_cols and df[time_cols[0]].nunique() > 1 else None
        color_col = _llm_color or (time_as_color if cat_cols else None)
```

**bar_stack 分支**（约第 298-310 行）：同理：

```python
    elif chart_type == "bar_stack":
        if cat_cols and len(numeric_cols) >= 2:
            id_col = cat_cols[0]
            value_cols = [c for c in numeric_cols if c != id_col]
            df_melted = df.melt(id_vars=id_col, value_vars=value_cols,
                                var_name="_metric", value_name="_value")
            fig = px.bar(df_melted, x=id_col, y="_value", color="_metric",
                         title=title, barmode="stack")
        else:
            color_col = _llm_color or (time_cols[0] if time_cols else None)
            fig = px.bar(df, x=x_col, y=y_col, color=color_col, title=title, barmode="stack")
```

**scatter 分支**（约第 317-321 行）：同理：

```python
    elif chart_type == "scatter":
        scatter_x = x_col if chart_config else (numeric_cols[0] if len(numeric_cols) >= 2 else cols[0])
        scatter_y = y_col if chart_config else (numeric_cols[1] if len(numeric_cols) >= 2 else cols[1])
        color_col = _llm_color or (cat_cols[0] if cat_cols else None)
```

**funnel 分支**（约第 323-346 行）：添加 `_llm_color` 支持：

```python
    elif chart_type == "funnel":
        if len(df) == 1 and not cat_cols and len(numeric_cols) >= 2:
            step_col = "step"
            val_col = "value"
            df_funnel = df[numeric_cols].T.reset_index()
            df_funnel.columns = [step_col, val_col]
            df_funnel[val_col] = pd.to_numeric(df_funnel[val_col], errors="coerce")
            df_funnel = df_funnel.sort_values(val_col, ascending=False)
            fig = px.funnel(df_funnel, x=val_col, y=step_col, title=title)
        elif len(df) > 1 and cat_cols and len(numeric_cols) >= 2:
            id_col = cat_cols[0]
            count_cols = [c for c in numeric_cols if not any(
                kw in c.lower() for kw in ["rate", "ratio", "pct", "percent", "率", "比"]
            )]
            value_cols = count_cols if count_cols else numeric_cols
            df_melted = df.melt(id_vars=id_col, value_vars=value_cols,
                                var_name="stage", value_name="count")
            color_col = _llm_color or id_col
            fig = px.bar(df_melted, x="stage", y="count", color=color_col,
                         title=title, barmode="group",
                         labels={"stage": "漏斗阶段", "count": "用户数"})
            if show_label:
                fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
        else:
            fig = px.funnel(df, x=y_col, y=x_col, title=title)
```

**dual_axis 分支**（约第 193-251 行）：使用 `_llm_color` 优化：

dual_axis 分支使用显式的 `x_col` / `y_col`（已有变量赋值），不需要额外修改 color_col，因为 dual_axis 的 color 由 y1/y2 轴自然区分。

**heatmap 分支**（约第 348-364 行）：同理：

```python
    elif chart_type == "heatmap":
        hm_x = x_col if chart_config else (cat_cols[0] if len(cat_cols) >= 1 else cols[0])
        hm_y = _llm_color or (cat_cols[1] if len(cat_cols) >= 2 else cols[1])
```

- [ ] **Step 4: 添加 x_label / y_label 到 layout**

在 `_layout` 定义处（约第 168-171 行）之后，图表生成完成后的 `fig.update_layout()` 调用处，添加轴标签支持。

现有 layout：
```python
    _layout = dict(
        font=dict(family="Microsoft YaHei, Arial, sans-serif"),
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
```

在所有 `fig.update_layout(**_layout)` 调用（包含 dual_axis 内部的 layout 设置）之前，根据 `chart_config` 动态补充 axis titles。最简单的方式是在 `_layout` 中条件性添加：

将上述 `_layout` 定义改为：

```python
    _layout = dict(
        font=dict(family="Microsoft YaHei, Arial, sans-serif"),
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    if chart_config:
        if chart_config.get("x_label"):
            _layout["xaxis_title"] = chart_config["x_label"]
        if chart_config.get("y_label"):
            _layout["yaxis_title"] = chart_config["y_label"]
```

- [ ] **Step 5: 验证语法**

Run: `python -c "from sql_agent.multi.chart import _build_figure; print('OK')"`

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add sql_agent/multi/chart.py
git commit -m "feat: adapt _build_figure() to accept LLM chart_config parameter"
```

---

### Task 3: 修改 `chart_node()` 调用 LLM 决策层

**Files:**
- Modify: `sql_agent/multi/chart.py` — `chart_node()` 函数（约第 373-437 行）

- [ ] **Step 1: 在 `chart_node()` 的图表生成逻辑前插入 LLM 决策步骤**

找到 `chart_node()` 中调用 `_infer_chart_type` 和 `_build_figure` 的代码段（约第 420-431 行），当前为：

```python
        chart_type = _infer_chart_type(df, intent)

        if chart_type == "table":
            log.append(f"   📋 判断结果：纯表格展示（数据列：{list(df.columns)}）")
            return {**state, "chart_json": "", "chart_source_index": target_index, "process_log": log}

        chart_json = _build_figure(df, chart_type, title=intent, intent=intent)
        log.append(
            f"   ✅ 图表类型：{chart_type}（规则推断）\n"
            f"   数据：{len(df)} 行 × {len(df.columns)} 列"
        )
```

替换为：

```python
        # 尝试 LLM 决策
        chart_config = None
        try:
            from sql_agent._config import load_settings
            settings = load_settings()
            chart_config = _llm_chart_decision(df, intent, settings)
        except Exception:
            chart_config = None

        if chart_config:
            chart_type = chart_config["chart_type"]
            src = "AI 决策"
            if chart_type == "table":
                log.append(
                    f"   ✅ AI 决策：table（数据更适合表格展示）\n"
                    f"   数据：{len(df)} 行 × {len(df.columns)} 列"
                )
                return {**state, "chart_json": "", "chart_source_index": target_index, "process_log": log}
            chart_json = _build_figure(df, chart_type, title=intent, intent=intent,
                                       chart_config=chart_config)
            log.append(
                f"   ✅ AI 决策：{chart_config['chart_type']}"
                f" | X={chart_config['x_col']} | Y={chart_config['y_col']}"
                f" | 标题=\"{chart_config.get('title', '')}\"\n"
                f"   数据：{len(df)} 行 × {len(df.columns)} 列"
            )
        else:
            # 规则兜底
            log.append("   ⚠️ AI 决策未生效，回退到规则推断")
            chart_type = _infer_chart_type(df, intent)
            src = "规则推断"

            if chart_type == "table":
                log.append(f"   📋 判断结果：纯表格展示（数据列：{list(df.columns)}）")
                return {**state, "chart_json": "", "chart_source_index": target_index, "process_log": log}

            chart_json = _build_figure(df, chart_type, title=intent, intent=intent)
            log.append(
                f"   ✅ 图表类型：{chart_type}（规则推断）\n"
                f"   数据：{len(df)} 行 × {len(df.columns)} 列"
            )
```

- [ ] **Step 2: 验证语法**

Run: `python -c "from sql_agent.multi.chart import chart_node; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add sql_agent/multi/chart.py
git commit -m "feat: add LLM decision layer to chart_node() with rule fallback"
```

---

### Task 4: 端到端验证

**Files:**
- 无新建文件

- [ ] **Step 1: 验证模块导入完整**

Run:
```bash
cd /Users/albert/Desktop/Ai/测试/sql-agent-kit
python -c "
from sql_agent.multi.chart import (
    _infer_chart_type,
    _build_figure,
    _is_homogeneous,
    _pick_x_col,
    _pick_y_col,
    _should_sort,
    _should_show_label,
    _llm_chart_decision,
    chart_node,
)
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 2: 验证规则推断降级路径未受影响**

Run:
```bash
python -c "
import pandas as pd
from sql_agent.multi.chart import _infer_chart_type, _build_figure

# 模拟常见的分析结果数据
df = pd.DataFrame({
    '月份': ['1月', '2月', '3月', '4月', '5月', '6月'],
    '销售额': [12000, 15000, 13000, 17000, 16000, 19000],
    '订单数': [120, 150, 130, 170, 160, 190],
})
chart_type = _infer_chart_type(df, '各月销售趋势')
assert chart_type == 'bar'  # 无时间列时的规则结果
print(f'规则推断 chart_type: {chart_type}')

# 无 chart_config 时 _build_figure 使用规则推断
result = _build_figure(df, chart_type, title='test', intent='各月销售趋势')
assert result and '销售额' in result
print('规则推断 _build_figure: OK')
"
```

Expected: 无报错，打印推断结果。

- [ ] **Step 3: 验证 chart_config 模式**

Run:
```bash
python -c "
import pandas as pd
from sql_agent.multi.chart import _build_figure

df = pd.DataFrame({
    '月份': ['1月', '2月', '3月', '4月', '5月', '6月'],
    '销售额': [12000, 15000, 13000, 17000, 16000, 19000],
    '订单数': [120, 150, 130, 170, 160, 190],
})

# 模拟 LLM 返回的 chart_config
chart_config = {
    'chart_type': 'bar',
    'x_col': '月份',
    'y_col': '销售额',
    'color_col': None,
    'title': '各月销售额对比',
    'x_label': '月份',
    'y_label': '销售额（元）',
}

result = _build_figure(df, 'bar', title='test', intent='', chart_config=chart_config)
assert result and '月份' in result
print('chart_config 模式 _build_figure: OK')
"
```

Expected: 无报错。

- [ ] **Step 4: Commit**

```bash
git add sql_agent/multi/chart.py
git commit -m "test: verify chart LLM decision and rule fallback paths"
```
