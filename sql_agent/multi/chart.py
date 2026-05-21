"""
Chart Agent — 根据查询结果自动选图表并生成 plotly JSON
纯规则推断：基于 DataFrame 列类型和意图自动决定图表类型和轴映射
"""

from .state import GraphState


def _is_homogeneous(numeric_cols: list) -> bool:
    """判断数值列是否属于同类指标（可堆叠），或不同量纲（应分组展示）。"""
    _AMOUNT_KWS = ["spend", "cost", "revenue", "amount", "price", "sales", "花费", "收入", "金额", "销售额"]
    _COUNT_KWS = ["count", "orders", "users", "num", "qty", "quantity", "数量", "订单", "人数"]
    _RATE_KWS = ["rate", "ratio", "pct", "percent", "roi", "ctr", "cvr", "率", "比", "转化"]

    types = set()
    for col in numeric_cols:
        col_lower = col.lower()
        if any(kw in col_lower for kw in _RATE_KWS):
            types.add("rate")
        elif any(kw in col_lower for kw in _AMOUNT_KWS):
            types.add("amount")
        elif any(kw in col_lower for kw in _COUNT_KWS):
            types.add("count")
        else:
            types.add("other")
    return len(types) <= 1


def _infer_chart_type(df, intent: str) -> str:
    """根据 DataFrame 列类型和用户意图推断图表类型。"""
    if df is None or df.empty:
        return "table"

    import pandas as pd

    cols = df.columns.tolist()
    if not cols:
        return "table"

    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    time_cols = [c for c in cols if pd.api.types.is_datetime64_any_dtype(df[c])
                 or any(kw in c.lower() for kw in ["date", "time", "month", "year", "day", "日", "月", "年"])]
    cat_cols = [c for c in cols if c not in numeric_cols and c not in time_cols]
    effective_time_cols = [c for c in time_cols if df[c].nunique() > 1]

    intent_lower = (intent or "").lower()
    is_trend = any(kw in intent_lower for kw in ["趋势", "变化", "走势", "trend", "增长", "下降", "波动"])

    if effective_time_cols and numeric_cols:
        return "area" if cat_cols else "line"
    if len(cat_cols) >= 2 and len(numeric_cols) == 1:
        return "heatmap"

    # 分类列唯一值过多时图表不可读，降级为表格
    if cat_cols and df[cat_cols[0]].nunique() > 30:
        return "table"

    if cat_cols and len(numeric_cols) == 1:
        if len(df) <= 6:
            return "pie"
        col_text = " ".join(cols).lower()
        if any(kw in col_text for kw in ["funnel", "stage", "step", "转化", "漏斗", "步骤"]):
            return "funnel"
        return "bar"
    if time_cols and cat_cols and numeric_cols:
        return "bar_stack"
    if len(numeric_cols) >= 2 and cat_cols:
        # 任意数值列含"率/比"关键词 → 双轴图（量和率同时展示）
        rate_kws = ["rate", "ratio", "pct", "percent", "roi", "率", "比", "增长"]
        has_rate_col = any(
            any(kw in col.lower() for kw in rate_kws)
            for col in numeric_cols
        )
        if has_rate_col or is_trend:
            return "dual_axis"
        # 量纲一致时用堆叠图，否则回退分组柱状图
        return "bar_stack" if _is_homogeneous(numeric_cols) else "bar"
    if len(numeric_cols) >= 2 and not cat_cols:
        return "scatter"
    if numeric_cols:
        return "line" if is_trend else "bar"
    return "table"


def _pick_y_col(numeric_cols: list, intent: str, chart_type: str = "") -> str:
    """从多个数值列中选出最合适的 Y 轴列（基于意图关键词匹配）。"""
    if not numeric_cols:
        return ""
    if len(numeric_cols) == 1:
        return numeric_cols[0]

    import re

    intent_lower = (intent or "").lower()
    intent_tokens = [t for t in re.split(r'[\s,，。？?、/\\]+', intent_lower) if len(t) >= 2]

    # 1. 意图关键词精确匹配列名
    for col in reversed(numeric_cols):
        col_lower = col.lower()
        if any(tok in col_lower or col_lower in tok for tok in intent_tokens):
            return col

    # 2. 优先选 amount/count 类指标（rate/率/比 作为派生指标不优先做主 Y）
    _RATE_KWS = ["rate", "ratio", "pct", "percent", "roi", "ctr", "cvr", "率", "比", "转化"]
    non_rate = [c for c in numeric_cols if not any(kw in c.lower() for kw in _RATE_KWS)]
    if non_rate:
        return non_rate[0]

    # 3. 只有 rate 类列时，取第一个
    return numeric_cols[0]


def _pick_x_col(df, chart_type: str) -> str:
    """根据图表类型和列类型自动选择 X 轴列。"""
    import pandas as pd

    cols = df.columns.tolist()
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    time_cols = [c for c in cols if any(kw in c.lower()
                  for kw in ["date", "time", "month", "year", "day", "日", "月", "年"])]
    cat_cols = [c for c in cols if c not in numeric_cols and c not in time_cols]
    effective_time_cols = [c for c in time_cols if df[c].nunique() > 1]

    if chart_type in ("line", "area") and effective_time_cols:
        return effective_time_cols[0]
    if chart_type == "pie":
        return cat_cols[0] if cat_cols else cols[0]
    if cat_cols:
        return cat_cols[0]
    if time_cols:
        return time_cols[0]
    return cols[0]


def _should_sort(chart_type: str) -> str:
    """判断是否需要排序及排序方向。"""
    if chart_type in ("bar", "pie", "funnel", "dual_axis"):
        return "desc"
    return "none"


def _should_show_label(df) -> bool:
    """数据点少时显示标签。"""
    return len(df) <= 15


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


def _build_figure(df, chart_type: str, title: str, intent: str = "") -> str:
    """用 plotly.express / plotly.graph_objects 生成图表 JSON。
    所有参数（x/y/sort/label）均从数据推断，不依赖外部 hint。
    出错时抛出异常，由调用方记录日志。
    """
    import plotly.express as px
    import plotly.graph_objects as go
    import pandas as pd

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

    _layout = dict(
        font=dict(family="Microsoft YaHei, Arial, sans-serif"),
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    def _try_parse_datetime(df, col):
        try:
            converted = pd.to_datetime(df[col], infer_datetime_format=True)
            df = df.copy()
            df[col] = converted
            return df, True
        except Exception:
            return df, False

    def _apply_sort(df, col, order):
        if order == "desc":
            return df.sort_values(col, ascending=False)
        if order == "asc":
            return df.sort_values(col, ascending=True)
        return df

    fig = None

    if chart_type == "dual_axis":
        _RATE_KWS = ["rate", "ratio", "pct", "percent", "roi", "ctr", "cvr", "率", "比", "转化"]
        # 扫描所有数值列找 rate 列作为副 Y 轴（折线）
        rate_cols = [c for c in numeric_cols if any(kw in c.lower() for kw in _RATE_KWS)]
        y2_col = rate_cols[0] if rate_cols else (numeric_cols[1] if len(numeric_cols) >= 2 else "")

        if not y2_col or y2_col == y_col:
            chart_type = "bar"
        else:
            df = _apply_sort(df, y_col, sort_order)

            # 第三指标（若有）写入 hover 自定义数据
            extra_cols = [c for c in numeric_cols if c not in (y_col, y2_col)]
            extra_hover = extra_cols[0] if extra_cols else ""
            customdata = df[[extra_hover]].values.tolist() if extra_hover else None

            fig = go.Figure()
            bar_trace = go.Bar(
                x=df[x_col], y=df[y_col], name=y_col,
                yaxis="y1",
                text=[f"{v:,.0f}" for v in df[y_col]] if show_label else None,
                textposition="outside" if show_label else None,
            )
            if customdata:
                bar_trace.customdata = customdata
                bar_trace.hovertemplate = (
                    f"{x_col}=%{{x}}<br>{y_col}=%{{y:,.0f}}"
                    f"<br>{extra_hover}=%{{customdata[0]:,.0f}}<extra></extra>"
                )
            fig.add_trace(bar_trace)

            # 分类 X 轴时折线无连续意义，仅显示标记点
            is_time_x = x_col in effective_time_cols
            line_mode = "lines+markers" if is_time_x else "markers"
            line_mode += "+text" if show_label else ""

            line_trace = go.Scatter(
                x=df[x_col], y=df[y2_col], name=y2_col,
                mode=line_mode,
                yaxis="y2",
                text=[f"{v:.2f}" for v in df[y2_col]] if show_label else None,
                textposition="top center" if show_label else None,
            )
            if customdata:
                line_trace.customdata = customdata
                line_trace.hovertemplate = (
                    f"{x_col}=%{{x}}<br>{y2_col}=%{{y:.2f}}"
                    f"<br>{extra_hover}=%{{customdata[0]:,.0f}}<extra></extra>"
                )
            fig.add_trace(line_trace)

            fig.update_layout(
                title=title,
                yaxis=dict(title=y_col),
                yaxis2=dict(title=y2_col, overlaying="y", side="right"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                **_layout,
            )
            return fig.to_json()

    if chart_type == "line":
        df, parsed = _try_parse_datetime(df, x_col)
        df = df.sort_values(x_col)
        color_col = cat_cols[0] if cat_cols else None
        # 多数值列无分类时，melt 为多条折线
        if len(numeric_cols) >= 2 and not cat_cols:
            df_melted = df.melt(id_vars=x_col, value_vars=numeric_cols,
                                var_name="_metric", value_name="_value")
            fig = px.line(df_melted, x=x_col, y="_value", color="_metric",
                          title=title, markers=True)
        elif not parsed:
            fig = go.Figure()
            if color_col:
                for grp, gdf in df.groupby(color_col):
                    fig.add_trace(go.Scatter(x=gdf[x_col], y=gdf[y_col],
                                             mode="lines+markers", name=str(grp)))
            else:
                fig.add_trace(go.Scatter(x=df[x_col], y=df[y_col],
                                         mode="lines+markers", name=y_col))
            fig.update_layout(title=title, xaxis_title=x_col, yaxis_title=y_col, **_layout)
        else:
            fig = px.line(df, x=x_col, y=y_col, color=color_col, title=title, markers=True)
        if show_label:
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="top center",
                              mode="lines+markers+text")

    elif chart_type == "area":
        df, _ = _try_parse_datetime(df, x_col)
        df = df.sort_values(x_col)
        color_col = cat_cols[0] if cat_cols else None
        if len(numeric_cols) >= 2 and not cat_cols:
            df_melted = df.melt(id_vars=x_col, value_vars=numeric_cols,
                                var_name="_metric", value_name="_value")
            fig = px.area(df_melted, x=x_col, y="_value", color="_metric", title=title)
        else:
            fig = px.area(df, x=x_col, y=y_col, color=color_col, title=title)

    elif chart_type == "bar":
        time_as_color = time_cols[0] if time_cols and df[time_cols[0]].nunique() > 1 else None
        color_col = time_as_color if cat_cols else None
        df = _apply_sort(df, y_col, sort_order)
        fig = px.bar(df, x=x_col, y=y_col, color=color_col, title=title, barmode="group")
        if show_label:
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")

    elif chart_type == "bar_stack":
        if cat_cols and len(numeric_cols) >= 2:
            id_col = cat_cols[0]
            value_cols = [c for c in numeric_cols if c != id_col]
            df_melted = df.melt(id_vars=id_col, value_vars=value_cols,
                                var_name="_metric", value_name="_value")
            fig = px.bar(df_melted, x=id_col, y="_value", color="_metric",
                         title=title, barmode="stack")
        else:
            color_col = time_cols[0] if time_cols else None
            fig = px.bar(df, x=x_col, y=y_col, color=color_col, title=title, barmode="stack")
        if show_label:
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="inside")

    elif chart_type == "pie":
        fig = px.pie(df, names=x_col, values=y_col, title=title)
        if show_label:
            fig.update_traces(textinfo="label+percent+value")

    elif chart_type == "scatter":
        scatter_x = numeric_cols[0] if len(numeric_cols) >= 2 else cols[0]
        scatter_y = numeric_cols[1] if len(numeric_cols) >= 2 else cols[1]
        color_col = cat_cols[0] if cat_cols else None
        fig = px.scatter(df, x=scatter_x, y=scatter_y, color=color_col, title=title)

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
            fig = px.bar(df_melted, x="stage", y="count", color=id_col,
                         title=title, barmode="group",
                         labels={"stage": "漏斗阶段", "count": "用户数"})
            if show_label:
                fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
        else:
            fig = px.funnel(df, x=y_col, y=x_col, title=title)

    elif chart_type == "heatmap":
        hm_x = cat_cols[0] if len(cat_cols) >= 1 else cols[0]
        hm_y = cat_cols[1] if len(cat_cols) >= 2 else cols[1]
        try:
            pivot = df.pivot(index=hm_y, columns=hm_x, values=y_col)
            fig = go.Figure(data=go.Heatmap(
                z=pivot.values.tolist(),
                x=pivot.columns.tolist(),
                y=pivot.index.tolist(),
                colorscale="Blues",
                text=[[f"{v:,.0f}" for v in row] for row in pivot.values.tolist()] if show_label else None,
                texttemplate="%{text}" if show_label else None,
            ))
            fig.update_layout(title=title, **_layout)
            return fig.to_json()
        except Exception:
            fig = px.bar(df, x=hm_x, y=y_col, color=hm_y, title=title, barmode="group")

    if fig is None:
        raise ValueError(f"未知图表类型：{chart_type}")

    fig.update_layout(**_layout)
    return fig.to_json()


def chart_node(state: GraphState) -> GraphState:
    """Chart Agent 节点：纯规则推断图表类型并生成 plotly JSON"""
    import pandas as pd

    sql_results = state.get("sql_results", [])
    intent = state.get("intent", state.get("question", ""))
    log = list(state.get("process_log") or [])
    log.append("📊 [Chart Agent] 正在判断图表类型...")

    target = None
    target_index = 0
    best_score = -1
    for i, r in enumerate(sql_results):
        if not (r.get("success") and r.get("data")):
            continue
        df_tmp = pd.DataFrame(r["data"])
        if df_tmp.empty:
            continue
        cols_tmp = df_tmp.columns.tolist()
        time_kws = ["date", "time", "month", "year", "day", "日", "月", "年"]
        has_time = any(any(kw in c.lower() for kw in time_kws) for c in cols_tmp)
        numeric_cnt = sum(1 for c in cols_tmp if pd.api.types.is_numeric_dtype(df_tmp[c]))
        # 多列数值数据（潜在漏斗/多维分析）给高分
        is_rich = numeric_cnt >= 2 and len(df_tmp) > 1
        score = 20 if is_rich else ((10 if has_time else 0) + numeric_cnt)
        if score > best_score:
            best_score = score
            target = r
            target_index = i

    if not target:
        log.append("   ⚠️ 无有效数据，跳过图表生成")
        return {**state, "chart_json": "", "chart_source_index": 0, "process_log": log}

    try:
        df = pd.DataFrame(target["data"])
        if df.empty:
            log.append("   ⚠️ 数据为空，跳过图表生成")
            return {**state, "chart_json": "", "chart_source_index": 0, "process_log": log}

        # 将字符串类型的数值列转为真正的数值类型（DB DECIMAL 可能经 JSON 序列化后变字符串）
        for c in df.columns:
            if pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object:
                try:
                    df[c] = pd.to_numeric(df[c])
                except (ValueError, TypeError):
                    pass  # 非数值文本列，保持原样

        chart_type = _infer_chart_type(df, intent)

        if chart_type == "table":
            log.append(f"   📋 判断结果：纯表格展示（数据列：{list(df.columns)}）")
            return {**state, "chart_json": "", "chart_source_index": target_index, "process_log": log}

        chart_json = _build_figure(df, chart_type, title=intent, intent=intent)
        log.append(
            f"   ✅ 图表类型：{chart_type}（规则推断）\n"
            f"   数据：{len(df)} 行 × {len(df.columns)} 列"
        )
        return {**state, "chart_json": chart_json, "chart_source_index": target_index, "process_log": log}

    except Exception as e:
        log.append(f"   ❌ 图表生成失败：{e}")
        return {**state, "chart_json": "", "chart_source_index": 0,
                "error": state.get("error") or f"Chart 生成失败: {e}", "process_log": log}
