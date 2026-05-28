"""
Chart Agent — 根据查询结果自动选图表并生成 plotly JSON
纯规则推断：基于 DataFrame 列类型和意图自动决定图表类型和轴映射
"""

from .state import GraphState


_RATE_KWS = ["rate", "ratio", "pct", "percent", "roi", "ctr", "cvr", "率", "比", "转化"]
_AMOUNT_KWS = ["spend", "cost", "revenue", "amount", "price", "sales", "金额", "销售额", "收入", "花费", "客单价"]
_COUNT_KWS = ["count", "orders", "users", "num", "qty", "quantity", "数量", "订单数", "人数"]


def _is_rate_col(col: str) -> bool:
    return any(kw in str(col).lower() for kw in _RATE_KWS)


def _is_amount_col(col: str) -> bool:
    return any(kw in str(col).lower() for kw in _AMOUNT_KWS)


def _is_count_col(col: str) -> bool:
    return any(kw in str(col).lower() for kw in _COUNT_KWS)


def _requested_numeric_cols(df, question: str, intent: str = "") -> list[str]:
    """根据用户问题和列名/常见中文语义，识别用户明确要求展示的数值指标列。"""
    import pandas as pd
    import re

    text = f"{question or ''} {intent or ''}".lower()
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    aliases = {
        "avg": ["平均", "客单价"],
        "average": ["平均", "客单价"],
        "mean": ["平均"],
        "order": ["订单"],
        "orders": ["订单"],
        "amount": ["金额", "销售额"],
        "sales": ["销售额", "销售"],
        "total": ["总", "总计"],
        "completion": ["完成", "已完成"],
        "completed": ["完成", "已完成"],
        "rate": ["率", "比例", "占比"],
        "ratio": ["率", "比例", "占比"],
        "count": ["数量", "数", "订单数"],
        "num": ["数量", "数"],
        "user": ["用户"],
        "users": ["用户"],
    }

    requested = []
    for col in numeric_cols:
        col_lower = str(col).lower()
        if col_lower in text:
            requested.append(col)
            continue
        tokens = [t for t in re.split(r"[_\W]+", col_lower) if t]
        score = 0
        for token in tokens:
            if token in text:
                score += 1
            if any(alias in text for alias in aliases.get(token, [])):
                score += 1
        if score >= 2:
            requested.append(col)

    if requested:
        # “完成率（已完成订单数/总订单数）与平均订单金额”这类问题中，
        # total_orders / completed_orders 是计算率的辅助列，不应替代真正要展示的金额指标。
        rate_requested = [c for c in requested if _is_rate_col(c)]
        amount_requested = [c for c in requested if _is_amount_col(c)]
        if rate_requested and amount_requested and any(kw in text for kw in ["完成率", "转化率", "率"]):
            return list(dict.fromkeys(amount_requested + rate_requested))
        return requested

    explicit_multi = any(kw in text for kw in ["双轴", "和", "与", "及", "、", "同时", "分别", "对比"])
    if explicit_multi and 2 <= len(numeric_cols) <= 2:
        return numeric_cols
    return []


def _trace_meta(fig, chart_type: str, x_col: str, y_cols: list[str],
                series_mode: str = "", color_col: str | None = None) -> dict:
    traces = []
    for trace in fig.data:
        traces.append({
            "type": getattr(trace, "type", ""),
            "name": getattr(trace, "name", "") or "",
            "yaxis": getattr(trace, "yaxis", "y") or "y",
            "xaxis": getattr(trace, "xaxis", "x") or "x",
            "legendgroup": getattr(trace, "legendgroup", "") or "",
        })
    return {
        "chart_type": chart_type,
        "x_col": x_col,
        "y_cols": y_cols,
        "color_col": color_col,
        "series_mode": series_mode,
        "trace_count": len(traces),
        "traces": traces,
    }


def _format_chart_meta(meta: dict) -> str:
    y_cols = meta.get("y_cols") or []
    y_text = " | ".join(
        f"Y{i + 1}={col}" if i else f"Y={col}"
        for i, col in enumerate(y_cols)
    ) or "Y=未识别"
    mode = f" | 模式={meta.get('series_mode')}" if meta.get("series_mode") else ""
    color = f" | 分组={meta.get('color_col')}" if meta.get("color_col") else ""
    traces = meta.get("traces", [])
    display_traces = traces[:12]
    trace_text = ", ".join(
        f"{t.get('name') or '-'}:{t.get('type')}@{t.get('yaxis')}"
        for t in display_traces
    ) or "无"
    if len(traces) > len(display_traces):
        trace_text += f" ... 另 {len(traces) - len(display_traces)} 条"
    return (
        f"   ✅ 实际图表：{meta.get('chart_type')} | X={meta.get('x_col')} | {y_text}{color}{mode}\n"
        f"   Trace：{trace_text}"
    )


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
                 or any(kw in c.lower() for kw in ["date", "time", "month", "year", "week", "day", "日", "月", "年", "周", "星期"])]
    cat_cols = [c for c in cols if c not in numeric_cols and c not in time_cols]
    effective_time_cols = [c for c in time_cols if df[c].nunique() > 1]

    intent_lower = (intent or "").lower()
    is_trend = any(kw in intent_lower for kw in ["趋势", "变化", "走势", "trend", "增长", "下降", "波动"])

    # 单行全数字数据 → 漏斗图（转置展示，如：曝光→点击→订单）
    if len(df) == 1 and not cat_cols and len(numeric_cols) >= 2:
        return "funnel"

    if effective_time_cols and numeric_cols:
        return "area" if cat_cols else "line"
    if len(cat_cols) >= 2 and len(numeric_cols) == 1:
        return "heatmap"

    # 分类列唯一值过多时图表不可读，降级为表格
    if cat_cols and df[cat_cols[0]].nunique() > 30:
        return "table"

    if cat_cols and len(numeric_cols) == 1:
        if len(df) <= 6:
            return "donut"
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
                  for kw in ["date", "time", "month", "year", "week", "day", "日", "月", "年", "周", "星期"])]
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
    if chart_type in ("bar", "barh", "bar_group", "bar_stack", "percent_stack", "pie", "donut", "funnel", "waterfall", "treemap", "dual_axis"):
        return "desc"
    return "none"


def _should_show_label(df) -> bool:
    """数据点少时显示标签。"""
    return len(df) <= 15


def _llm_chart_decision(df, intent: str, settings: dict,
                       log: list | None = None,
                       question: str = "") -> dict | None:
    """调用 LLM 决策图表类型、轴映射和标题。成功返回 dict，失败返回 None 并写 log。"""
    import json
    import re
    from sql_agent.llm import get_llm_client

    def _note(msg: str):
        if log is not None:
            log.append(f"   🔍 {msg}")

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
        "- bar: 分类对比，X 轴为分类列，Y 轴为数值列（竖柱状图）\n"
        "- barh: 横向柱状图，适合分类名较长或 Top-N 排行\n"
        "- bar_group: 分组柱状图，适合同一分类下多指标或多组对比\n"
        "- bar_stack: 多指标堆叠对比，同量纲多数值列\n"
        "- percent_stack: 百分比堆叠柱状图，适合构成占比随分类/时间变化\n"
        "- pie: 占比分布，≤6 个分类 + 单数值列\n"
        "- donut: 环形图，同 pie 但中心留空，视觉效果更好\n"
        "- scatter: 两数值列的相关性/分布分析\n"
        "- histogram: 单个数值字段的分布直方图\n"
        "- boxplot: 按分类查看数值分布/离群点\n"
        "- funnel: 转化漏斗，含阶段/步骤列\n"
        "- waterfall: 瀑布图，适合增减变化或收入/成本拆解\n"
        "- heatmap: 两个分类维度的交叉矩阵\n"
        "- treemap: 树图，适合层级分类占比/规模展示\n"
        "- kpi_card: KPI 指标卡，适合单行核心指标展示\n"
        "- table_chart: 图形表格，适合列较多但仍要在图表区域展示\n"
        "- dual_axis: 双 Y 轴，同时展示两个不同量纲指标（如销售额 + 转化率）\n"
        "- table: 数据不适合图表展示（列数过多/无有意义的图表映射）\n\n"
        "要求：\n"
        '1. 仔细阅读\u201c用户原始问题\u201d，用户的图表类型偏好、要展示的指标、特殊标注需求（如参考线）都必须尊重\n'
        "2. x_col / y_col / y2_col 必须是 DataFrame 中真实存在的列名，不能编造；dual_axis 必须填写 y2_col，且 y_col 与 y2_col 不能相同\n"
        "3. 用户问题中明确提到的指标必须全部映射到图表；双轴图用 y_col 和 y2_col 分别承载两个指标\n"
        "4. dual_axis 的 series_mode 只能是 line_line、bar_line、line_bar、bar_bar；用户明确要求“双轴折线图”时用 line_line，明确要求“双轴柱状图”时用 bar_bar，否则默认 bar_line\n"
        "5. color_col 用于区分不同组/类别。重要规则：\n"
        "   - 如果数据有 channel、category、region、类型、渠道、品类 等分类列，且问题要\"对比各XX\"，必须设 color_col\n"
        "   - 如果数据按 channel+month 分组，X 轴选了 month，则 color_col 必须设为 channel（否则所有渠道会混在一起）\n"
        "   - 只有确实不需要分组对比时才填 null\n"
        "6. title / x_label / y_label / y2_label 用中文\n"
        "7. 如果数据中有多个相关数值列（比如同时有花费、收入、ROI），优先考虑 bar_stack 或 dual_axis 来全面展示\n"
        "8. 只输出 JSON，不要任何解释\n\n"
        '输出格式（dual_axis 示例）：\n'
        '{"chart_type": "dual_axis", "x_col": "月份", "y_col": "平均订单金额", "y2_col": "完成率", "series_mode": "line_line", "color_col": null, "title": "完成率与平均订单金额趋势", "x_label": "月份", "y_label": "平均订单金额（元）", "y2_label": "完成率"}'
    )

    user_parts = []
    if question and question != intent:
        user_parts.append(f"用户原始问题：{question}")
    user_parts.append(f"分析意图：{intent}")
    user_parts.append(f"DataFrame 列信息：\n{chr(10).join(col_lines)}")
    user_parts.append(f"数据预览（前 3 行）：\n{head_text}")
    user_parts.append(f"统计摘要：\n{stats_text}")
    user_parts.append("请输出图表配置 JSON：")
    user_content = "\n\n".join(user_parts)

    try:
        llm = get_llm_client(settings["llm"])
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        raw = llm.chat(messages, temperature=0.0)

        match = re.search(r"\{[\s\S]+?\}", raw)
        if not match:
            _note(f"LLM 返回中未找到 JSON（原始输出前200字：{raw[:200]}）")
            return None
        config = json.loads(match.group())

        # 归一化：部分模型可能返回列表、逗号分隔字符串、"None"/"null" 等非标量值
        def _scalar(v):
            if v is None:
                return None
            if isinstance(v, list) and len(v) > 0:
                return str(v[0])
            if isinstance(v, str):
                if v.lower() in ("none", "null", "nan", ""):
                    return None
                if "," in v:
                    return v.split(",")[0].strip()
            return v

        config["chart_type"] = _scalar(config.get("chart_type", ""))
        config["x_col"] = _scalar(config.get("x_col", ""))
        config["y_col"] = _scalar(config.get("y_col", ""))
        config["y2_col"] = _scalar(config.get("y2_col"))
        config["series_mode"] = _scalar(config.get("series_mode")) or ""
        if config.get("color_col"):
            config["color_col"] = _scalar(config["color_col"])

        valid_series_modes = ("line_line", "bar_line", "line_bar", "bar_bar")
        if config["series_mode"] and config["series_mode"] not in valid_series_modes:
            _note(f"LLM 返回非法 series_mode：{config['series_mode']}，已忽略")
            config["series_mode"] = ""

        # 校验 chart_type 合法性
        valid_types = {
            "line", "area", "bar", "barh", "bar_group", "bar_stack", "percent_stack",
            "pie", "donut", "scatter", "histogram", "boxplot", "funnel", "waterfall",
            "heatmap", "treemap", "kpi_card", "table_chart", "dual_axis", "table",
        }
        if config.get("chart_type") not in valid_types:
            _note(f"LLM 返回非法图表类型：{config.get('chart_type')}")
            return None

        # 校验列名真实存在（None 允许通过，漏斗图等场景 x_col/y_col 可为空）
        x_col = config.get("x_col")
        y_col = config.get("y_col")
        if x_col is not None and x_col not in df.columns:
            _note(f"LLM 返回的 x_col='{x_col}' 不在数据列 {list(df.columns)} 中")
            return None
        if y_col is not None and y_col not in df.columns:
            _note(f"LLM 返回的 y_col='{y_col}' 不在数据列 {list(df.columns)} 中")
            return None
        y2_col = config.get("y2_col")
        if y2_col is not None and y2_col not in df.columns:
            _note(f"LLM 返回的 y2_col='{y2_col}' 不在数据列 {list(df.columns)} 中")
            return None
        if config.get("chart_type") == "dual_axis" and y_col and y2_col and y_col == y2_col:
            _note(f"LLM 返回的 y_col 与 y2_col 相同：{y_col}，将交由规则自检修正")
        color = config.get("color_col")
        if color and color not in df.columns:
            _note(f"LLM 返回的 color_col='{color}' 不在数据列 {list(df.columns)} 中")
            return None

        return config

    except Exception as e:
        _note(f"LLM 图表决策调用异常：{type(e).__name__}：{e}")
        return None


def _build_figure(df, chart_type: str, title: str, intent: str = "",
                  chart_config: dict | None = None, return_meta: bool = False):
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
                  for kw in ["date", "time", "month", "year", "week", "day", "日", "月", "年", "周", "星期"])]
    cat_cols = [c for c in cols if c not in numeric_cols and c not in time_cols]
    effective_time_cols = [c for c in time_cols if df[c].nunique() > 1]

    if chart_config:
        # LLM 决策模式：直接用 LLM 给出的轴映射和标题
        x_col = chart_config["x_col"]
        y_col = chart_config["y_col"]
        y2_col = chart_config.get("y2_col")
        series_mode = chart_config.get("series_mode") or ""
        title = chart_config.get("title", title)
        _llm_color = chart_config.get("color_col")
    else:
        # 规则推断模式（现有逻辑）
        x_col = _pick_x_col(df, chart_type)
        y_col = _pick_y_col(numeric_cols, intent, chart_type) or (numeric_cols[-1] if numeric_cols else cols[-1])
        y2_col = None
        series_mode = ""
        _llm_color = None

    show_label = _should_show_label(df)
    sort_order = _should_sort(chart_type)

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

    def _try_parse_datetime(df, col):
        try:
            # 单独的数值月份/年份不是完整日期，不能按 epoch 转成 datetime，
            # 否则 1/2/3/4 会变成 1970-01-01 附近，图表挤成一条竖线。
            if pd.api.types.is_numeric_dtype(df[col]):
                return df, False
            converted = pd.to_datetime(df[col])
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
        rate_cols = [c for c in numeric_cols if _is_rate_col(c)]
        if not y2_col or y2_col == y_col:
            y2_col = next((c for c in rate_cols if c != y_col), "")
        if not y2_col or y2_col == y_col:
            y2_col = next((c for c in numeric_cols if c != y_col), "")
        if _is_rate_col(y_col) and y2_col and not _is_rate_col(y2_col):
            y_col, y2_col = y2_col, y_col
        if not y2_col or y2_col == y_col:
            raise ValueError(f"dual_axis 需要两个不同的数值列，当前 y_col={y_col}, y2_col={y2_col}")

        text_lower = str(intent or "").lower()
        if not series_mode:
            if any(kw in text_lower for kw in ["双轴柱状", "双柱状", "双轴柱形", "bar_bar"]):
                series_mode = "bar_bar"
            elif any(kw in text_lower for kw in ["双轴折线", "双折线", "折线图", "line_line"]):
                series_mode = "line_line"
            elif any(kw in text_lower for kw in ["折线柱状", "线柱", "line_bar"]):
                series_mode = "line_bar"
            else:
                series_mode = "bar_line"
        if series_mode not in ("line_line", "bar_line", "line_bar", "bar_bar"):
            series_mode = "bar_line"

        # 时间序列 X 轴按时间排序，避免折线因 Y 值排序而错乱
        time_kws = ["date", "time", "month", "year", "week", "day", "日", "月", "年", "周", "星期", "时间"]
        is_time_x = x_col in effective_time_cols or any(
            kw in str(x_col).lower() for kw in time_kws
        )
        if is_time_x:
            df, _ = _try_parse_datetime(df, x_col)
            df = df.sort_values(x_col)
        else:
            df = _apply_sort(df, y_col, sort_order)

        # 第三指标（若有）写入 hover 自定义数据
        extra_cols = [c for c in numeric_cols if c not in (y_col, y2_col)]
        extra_hover = extra_cols[0] if extra_cols else ""

        fig = go.Figure()
        line_mode = "lines+markers" if is_time_x else "markers"
        line_mode += "+text" if show_label else ""
        color_candidates = [c for c in cat_cols if c != x_col]
        color_col = _llm_color if _llm_color and _llm_color != x_col else None
        color_col = color_col or (color_candidates[0] if color_candidates else None)

        def _make_trace(kind, gdf, y, name, axis, offsetgroup=None, opacity=None):
            customdata = gdf[[extra_hover]].values.tolist() if extra_hover else None
            if kind == "line":
                trace = go.Scatter(
                    x=gdf[x_col], y=gdf[y], name=name,
                    mode=line_mode,
                    yaxis=axis,
                    legendgroup=str(name).split(" - ")[0],
                    text=[f"{v:,.2f}" for v in gdf[y]] if show_label else None,
                    textposition="top center" if show_label else None,
                )
            else:
                trace = go.Bar(
                    x=gdf[x_col], y=gdf[y], name=name,
                    yaxis=axis,
                    offsetgroup=offsetgroup or name,
                    legendgroup=str(name).split(" - ")[0],
                    opacity=opacity,
                    text=[f"{v:,.2f}" for v in gdf[y]] if show_label else None,
                    textposition="outside" if show_label else None,
                )
            if customdata:
                trace.customdata = customdata
                trace.hovertemplate = (
                    f"{x_col}=%{{x}}<br>{y}=%{{y:,.2f}}"
                    f"<br>{extra_hover}=%{{customdata[0]:,.2f}}<extra></extra>"
                )
            return trace

        y1_kind = "line" if series_mode in ("line_line", "line_bar") else "bar"
        y2_kind = "line" if series_mode in ("line_line", "bar_line") else "bar"
        if color_col:
            for grp, gdf in df.groupby(color_col, sort=False):
                gdf = gdf.sort_values(x_col)
                grp_name = str(grp)
                fig.add_trace(_make_trace(
                    y1_kind, gdf, y_col, f"{grp_name} - {y_col}", "y1",
                    offsetgroup=f"{grp_name}-{y_col}",
                ))
                fig.add_trace(_make_trace(
                    y2_kind, gdf, y2_col, f"{grp_name} - {y2_col}", "y2",
                    offsetgroup=f"{grp_name}-{y2_col}", opacity=0.65,
                ))
        else:
            fig.add_trace(_make_trace(
                y1_kind, df, y_col, y_col, "y1", offsetgroup="y1",
            ))
            fig.add_trace(_make_trace(
                y2_kind, df, y2_col, y2_col, "y2", offsetgroup="y2", opacity=0.65,
            ))

        y1_title = chart_config.get("y_label") if chart_config else None
        y2_title = chart_config.get("y2_label") if chart_config else None
        fig.update_layout(
            title=title,
            yaxis=dict(title=y1_title or y_col),
            yaxis2=dict(title=y2_title or y2_col, overlaying="y", side="right"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            barmode="group",
            **_layout,
        )
        chart_json = fig.to_json()
        meta = _trace_meta(fig, "dual_axis", x_col, [y_col, y2_col], series_mode, color_col)
        return (chart_json, meta) if return_meta else chart_json

    if chart_type == "line":
        df, parsed = _try_parse_datetime(df, x_col)
        df = df.sort_values(x_col)
        color_candidates = [c for c in cat_cols if c != x_col]
        color_col = _llm_color if _llm_color != x_col else None
        color_col = color_col or (color_candidates[0] if color_candidates else None)
        # 规则推断且无分类列时，才将多数值列 melt 为多条折线；LLM 指定 y_col 时尊重单指标映射
        if len(numeric_cols) >= 2 and not cat_cols and not chart_config:
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
        color_candidates = [c for c in cat_cols if c != x_col]
        color_col = _llm_color if _llm_color != x_col else None
        color_col = color_col or (color_candidates[0] if color_candidates else None)
        if len(numeric_cols) >= 2 and not cat_cols and not chart_config:
            df_melted = df.melt(id_vars=x_col, value_vars=numeric_cols,
                                var_name="_metric", value_name="_value")
            fig = px.area(df_melted, x=x_col, y="_value", color="_metric", title=title)
        else:
            fig = px.area(df, x=x_col, y=y_col, color=color_col, title=title)

    elif chart_type == "bar":
        time_as_color = time_cols[0] if time_cols and df[time_cols[0]].nunique() > 1 else None
        color_col = _llm_color or (time_as_color if cat_cols else None)
        df = _apply_sort(df, y_col, sort_order)
        fig = px.bar(df, x=x_col, y=y_col, color=color_col, title=title, barmode="group")
        if show_label:
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")

    elif chart_type == "barh":
        color_col = _llm_color or (cat_cols[0] if cat_cols else None)
        df = _apply_sort(df, y_col, sort_order)
        fig = px.bar(df, x=y_col, y=x_col, color=color_col, title=title, orientation="h")
        if show_label:
            fig.update_traces(texttemplate="%{x:,.0f}", textposition="outside")

    elif chart_type == "bar_group":
        color_col = _llm_color
        if len(numeric_cols) >= 2 and not color_col:
            id_col = x_col
            value_cols = [c for c in numeric_cols if c != id_col]
            df_melted = df.melt(id_vars=id_col, value_vars=value_cols,
                                var_name="_metric", value_name="_value")
            fig = px.bar(df_melted, x=id_col, y="_value", color="_metric",
                         title=title, barmode="group")
        else:
            df = _apply_sort(df, y_col, sort_order)
            fig = px.bar(df, x=x_col, y=y_col, color=color_col, title=title, barmode="group")
        if show_label:
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")

    elif chart_type == "bar_stack":
        if cat_cols and len(numeric_cols) >= 2:
            id_col = x_col
            value_cols = [c for c in numeric_cols if c != id_col]
            df_melted = df.melt(id_vars=id_col, value_vars=value_cols,
                                var_name="_metric", value_name="_value")
            fig = px.bar(df_melted, x=id_col, y="_value", color="_metric",
                         title=title, barmode="stack")
        else:
            color_col = _llm_color or (time_cols[0] if time_cols else None)
            fig = px.bar(df, x=x_col, y=y_col, color=color_col, title=title, barmode="stack")
        if show_label:
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="inside")

    elif chart_type == "percent_stack":
        if len(numeric_cols) >= 2 and not _llm_color:
            value_cols = [c for c in numeric_cols if c != x_col]
            df_pct = df[[x_col] + value_cols].copy()
            denom = df_pct[value_cols].sum(axis=1).replace(0, pd.NA)
            for c in value_cols:
                df_pct[c] = df_pct[c] / denom * 100
            df_melted = df_pct.melt(id_vars=x_col, value_vars=value_cols,
                                    var_name="_metric", value_name="_percent")
            fig = px.bar(df_melted, x=x_col, y="_percent", color="_metric",
                         title=title, barmode="stack", labels={"_percent": "占比（%）"})
        else:
            color_col = _llm_color or (cat_cols[0] if cat_cols and cat_cols[0] != x_col else None)
            if color_col:
                df_pct = df.copy()
                total = df_pct.groupby(x_col)[y_col].transform("sum").replace(0, pd.NA)
                df_pct["_percent"] = df_pct[y_col] / total * 100
                fig = px.bar(df_pct, x=x_col, y="_percent", color=color_col,
                             title=title, barmode="stack", labels={"_percent": "占比（%）"})
            else:
                fig = px.bar(df, x=x_col, y=y_col, title=title)
        if show_label:
            fig.update_traces(texttemplate="%{y:.1f}%", textposition="inside")

    elif chart_type in ("pie", "donut"):
        hole = 0.4 if chart_type == "donut" else 0
        fig = px.pie(df, names=x_col, values=y_col, title=title, hole=hole)
        if show_label:
            fig.update_traces(textinfo="label+percent+value")

    elif chart_type == "scatter":
        scatter_x = x_col if chart_config else (numeric_cols[0] if len(numeric_cols) >= 2 else cols[0])
        scatter_y = y_col if chart_config else (numeric_cols[1] if len(numeric_cols) >= 2 else cols[1])
        color_col = _llm_color or (cat_cols[0] if cat_cols else None)
        fig = px.scatter(df, x=scatter_x, y=scatter_y, color=color_col, title=title)

    elif chart_type == "histogram":
        hist_col = y_col if y_col in numeric_cols else (numeric_cols[0] if numeric_cols else x_col)
        color_col = _llm_color or (cat_cols[0] if cat_cols else None)
        fig = px.histogram(df, x=hist_col, color=color_col, title=title)

    elif chart_type == "boxplot":
        box_y = y_col if y_col in numeric_cols else (numeric_cols[0] if numeric_cols else cols[-1])
        box_x = x_col if x_col in cat_cols else (cat_cols[0] if cat_cols else None)
        fig = px.box(df, x=box_x, y=box_y, color=_llm_color, points="outliers", title=title)

    elif chart_type == "waterfall":
        df = _apply_sort(df, y_col, sort_order) if x_col not in time_cols else df
        fig = go.Figure(go.Waterfall(
            x=df[x_col], y=df[y_col], measure=["relative"] * len(df), name=y_col,
            text=[f"{v:,.0f}" for v in df[y_col]] if show_label else None,
        ))
        fig.update_layout(title=title, xaxis_title=x_col, yaxis_title=y_col, **_layout)
        chart_json = fig.to_json()
        meta = _trace_meta(fig, "waterfall", x_col, [y_col])
        return (chart_json, meta) if return_meta else chart_json

    elif chart_type == "treemap":
        value_col = y_col if y_col in numeric_cols else (numeric_cols[0] if numeric_cols else None)
        path_cols = [c for c in [_llm_color, x_col] if c]
        if value_col:
            fig = px.treemap(df, path=path_cols, values=value_col, title=title)
        else:
            fig = px.treemap(df, path=path_cols, title=title)

    elif chart_type == "kpi_card":
        value_col = y_col if y_col in numeric_cols else (numeric_cols[0] if numeric_cols else "")
        value = df[value_col].iloc[0] if value_col and len(df) else 0
        fig = go.Figure(go.Indicator(mode="number", value=value, title={"text": title or value_col}))
        fig.update_layout(**_layout)
        chart_json = fig.to_json()
        meta = _trace_meta(fig, "kpi_card", x_col, [value_col] if value_col else [])
        return (chart_json, meta) if return_meta else chart_json

    elif chart_type == "table_chart":
        fig = go.Figure(data=[go.Table(
            header=dict(values=list(df.columns), fill_color="#C8D4E3", align="left"),
            cells=dict(values=[df[c].tolist() for c in df.columns], fill_color="#EBF0F8", align="left"),
        )])
        fig.update_layout(title=title, **_layout)
        chart_json = fig.to_json()
        meta = _trace_meta(fig, "table_chart", x_col, numeric_cols)
        return (chart_json, meta) if return_meta else chart_json

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

    elif chart_type == "heatmap":
        hm_x = x_col if chart_config else (cat_cols[0] if len(cat_cols) >= 1 else cols[0])
        hm_y = _llm_color or (cat_cols[1] if len(cat_cols) >= 2 else cols[1])
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
            chart_json = fig.to_json()
            meta = _trace_meta(fig, "heatmap", hm_x, [y_col])
            return (chart_json, meta) if return_meta else chart_json
        except Exception:
            fig = px.bar(df, x=hm_x, y=y_col, color=hm_y, title=title, barmode="group")

    if fig is None:
        raise ValueError(f"未知图表类型：{chart_type}")

    # 自动检测参考线：数值列中若有近乎常数的列（如 avg_roi、mean_xxx），添加参考线
    _add_reference_lines(fig, df, chart_type)

    fig.update_layout(**_layout)
    chart_json = fig.to_json()
    meta_y_cols = [y_col] if y_col else []
    if chart_type in ("line", "area") and len(numeric_cols) >= 2 and not _llm_color and not chart_config:
        meta_y_cols = numeric_cols
    elif chart_type in ("bar_group", "bar_stack", "percent_stack") and len(numeric_cols) >= 2 and not _llm_color:
        meta_y_cols = numeric_cols
    meta = _trace_meta(fig, chart_type, x_col, meta_y_cols, color_col=_llm_color)
    return (chart_json, meta) if return_meta else chart_json


def _add_reference_lines(fig, df, chart_type: str):
    """检测 DataFrame 中的常数/近常数数值列，自动添加水平/垂直参考线。"""
    import pandas as pd

    REF_KWS = ["avg", "mean", "median", "average", "基准", "平均", "参考", "target", "目标"]
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        if not any(kw in col.lower() for kw in REF_KWS):
            continue
        vals = df[col].dropna()
        if len(vals) < 1:
            continue
        # 检查是否近乎常数：变异系数 < 1%
        ref_val = vals.iloc[0]
        if len(vals) >= 2 and vals.std() > abs(vals.mean()) * 0.01:
            continue

        is_horizontal = chart_type in ("barh",)
        col_label = col.replace("_", " ")

        if is_horizontal:
            fig.add_vline(
                x=ref_val, line_dash="dash", line_color="#ff4d4f",
                annotation_text=f"{col_label}: {ref_val:,.2f}",
                annotation_position="top",
            )
        else:
            fig.add_hline(
                y=ref_val, line_dash="dash", line_color="#ff4d4f",
                annotation_text=f"{col_label}: {ref_val:,.2f}",
                annotation_position="top right",
            )


def _sanity_check(df, chart_type: str, chart_config: dict | None,
                 question: str, intent: str) -> tuple[list[str], dict | None]:
    """对图表决策做规则自检，返回 (警告列表, 修正后的 chart_config)。"""
    import pandas as pd

    warnings = []
    if not chart_config:
        return warnings, chart_config

    fixed = dict(chart_config)  # 拷贝，避免修改原对象
    question_lower = (question + " " + intent).lower()
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in df.columns if c not in numeric_cols]
    time_kws = ["date", "time", "month", "year", "week", "day", "日", "月", "年", "周", "星期", "时间"]
    time_cols = [c for c in df.columns if any(kw in str(c).lower() for kw in time_kws)]
    if fixed.get("x_col") not in df.columns:
        fallback_x = time_cols[0] if time_cols else df.columns[0]
        warnings.append(f"💡 自动修正：x_col 无效，已设置为 {fallback_x}")
        fixed["x_col"] = fallback_x

    # 1. 意图提到"各XX"/"不同"/"按XX"但未设分组 → 自动补 color_col
    if cat_cols:
        has_group_intent = any(kw in question_lower for kw in
            ["各", "不同", "每个", "每种", "分别", "按", "per", "each", "by "])
        has_color = bool(fixed.get("color_col"))
        if has_group_intent and not has_color:
            # 找最佳分组列：排除 X 轴和时间列后的分类列
            x = fixed.get("x_col", "")
            candidates = [c for c in cat_cols
                         if c != x and c not in time_cols and df[c].nunique() <= 20]
            if candidates:
                # 选唯一值数量最合适的（2-10 个最佳）
                best = max(candidates, key=lambda c: df[c].nunique() if df[c].nunique() <= 10 else 0)
                fixed["color_col"] = best
                warnings.append(
                    f"💡 自动修正：检测到分组意图，已设置 color_col=\"{best}\""
                    f"（可用分组列：{candidates}）"
                )
            elif x in cat_cols:
                warnings.append(f"✅ 分组维度：已使用 X 轴分类列 {x}，无需额外 color_col")
            else:
                warnings.append(
                    f"⚠️ 问题提到分组对比，但未找到合适的分组列。"
                    f"分类列：{cat_cols}"
                )

    # 2. 意图提到"趋势"/"变化"/"走势"但用了非时序图表
    has_trend_intent = any(kw in question_lower for kw in
        ["趋势", "变化", "走势", "trend", "增长", "下降", "波动", "随时间", "time"])
    if has_trend_intent and time_cols and chart_type not in ("line", "area", "dual_axis"):
        # 如果有时间列 + 多个数值列 → dual_axis，否则 line
        new_type = "dual_axis" if len(numeric_cols) >= 2 else "line"
        fixed["chart_type"] = new_type
        warnings.append(
            f"💡 自动修正：检测到时间趋势意图 + 时间列 {time_cols}，"
            f"图表类型已从 {chart_type} 改为 {new_type}"
        )

    # 3. 多指标场景用了单指标图表 → 自动升级
    if numeric_cols and fixed:
        single_metric_types = ("bar", "barh", "pie", "donut")
        current_type = fixed.get("chart_type", chart_type)
        if len(numeric_cols) >= 2 and current_type in single_metric_types and not fixed.get("color_col"):
            new_type = "bar_stack" if current_type in ("bar", "barh") else "dual_axis"
            fixed["chart_type"] = new_type
            warnings.append(
                f"💡 自动修正：数据有 {len(numeric_cols)} 个数值列但用了单指标图，"
                f"已升级为 {new_type}"
            )

    # 4. 用户明确要求的指标必须进入图表映射
    requested_cols = _requested_numeric_cols(df, question, intent)
    current_type = fixed.get("chart_type", chart_type)
    mapped_cols = [c for c in [fixed.get("y_col"), fixed.get("y2_col")] if c]
    missing_cols = [c for c in requested_cols if c not in mapped_cols]
    if missing_cols:
        if current_type in ("bar", "barh", "pie", "donut") and len(requested_cols) >= 2:
            current_type = "dual_axis"
            fixed["chart_type"] = current_type
            warnings.append(
                f"💡 自动修正：检测到用户要求多个指标 {requested_cols}，已升级为 dual_axis"
            )
        if current_type == "dual_axis" and len(requested_cols) >= 2:
            y1 = fixed.get("y_col")
            y2 = fixed.get("y2_col")
            preferred_rate = next((c for c in requested_cols if _is_rate_col(c)), None)
            preferred_non_rate = next((c for c in requested_cols if not _is_rate_col(c)), None)
            if preferred_rate and preferred_non_rate:
                y1, y2 = preferred_non_rate, preferred_rate
            else:
                y1, y2 = requested_cols[0], requested_cols[1]
            if fixed.get("y_col") != y1 or fixed.get("y2_col") != y2:
                fixed["y_col"] = y1
                fixed["y2_col"] = y2
                warnings.append(
                    f"💡 自动修正：为覆盖用户要求指标，设置 Y1={y1}，Y2={y2}"
                )
        elif current_type in ("line", "area", "bar_stack"):
            warnings.append(f"✅ 指标覆盖校验：将展示数值指标 {requested_cols or numeric_cols}")
        elif missing_cols:
            warnings.append(f"⚠️ 指标覆盖风险：用户要求的指标未全部映射到图表 {missing_cols}")

    # 5. dual_axis 必须有两个不同数值列，并根据用户要求选择折线/柱线模式
    current_type = fixed.get("chart_type", chart_type)
    if current_type == "dual_axis" and len(numeric_cols) >= 2:
        y1 = fixed.get("y_col") if fixed.get("y_col") in numeric_cols else ""
        y2 = fixed.get("y2_col") if fixed.get("y2_col") in numeric_cols else ""
        if not y1:
            non_rate_cols = [c for c in numeric_cols if not _is_rate_col(c)]
            y1 = non_rate_cols[0] if non_rate_cols else numeric_cols[0]
        if not y2 or y2 == y1:
            rate_cols = [c for c in numeric_cols if _is_rate_col(c) and c != y1]
            y2 = rate_cols[0] if rate_cols else next((c for c in numeric_cols if c != y1), "")
        if _is_rate_col(y1) and y2 and not _is_rate_col(y2):
            y1, y2 = y2, y1
        fixed["y_col"] = y1
        fixed["y2_col"] = y2
        if not fixed.get("series_mode"):
            if any(kw in question_lower for kw in ["双轴柱状", "双柱状", "双轴柱形", "bar_bar"]):
                fixed["series_mode"] = "bar_bar"
            elif any(kw in question_lower for kw in ["双轴折线", "双折线", "折线图", "line line", "line_line"]):
                fixed["series_mode"] = "line_line"
            elif any(kw in question_lower for kw in ["折线柱状", "线柱", "line_bar"]):
                fixed["series_mode"] = "line_bar"
            else:
                fixed["series_mode"] = "bar_line"
        if y2:
            warnings.append(
                f"✅ 指标覆盖校验：dual_axis 将展示 Y1={y1}，Y2={y2}，模式={fixed.get('series_mode')}"
            )
        else:
            warnings.append("⚠️ 指标覆盖风险：dual_axis 未找到第二个数值指标")

    return warnings, fixed


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
        time_kws = ["date", "time", "month", "year", "week", "day", "日", "月", "年", "周", "星期"]
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
        return {**state, "chart_json": "", "chart_source_index": 0, "chart_meta": {}, "process_log": log}

    try:
        df = pd.DataFrame(target["data"])
        if df.empty:
            log.append("   ⚠️ 数据为空，跳过图表生成")
            return {**state, "chart_json": "", "chart_source_index": 0, "chart_meta": {}, "process_log": log}

        # 将字符串类型的数值列转为真正的数值类型（DB DECIMAL 可能经 JSON 序列化后变字符串）
        for c in df.columns:
            if pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object:
                try:
                    df[c] = pd.to_numeric(df[c])
                except (ValueError, TypeError):
                    pass  # 非数值文本列，保持原样

        # 尝试 LLM 决策
        chart_config = None
        try:
            from sql_agent._config import load_settings
            settings = load_settings()
            chart_config = _llm_chart_decision(df, intent, settings,
                                                   log=log,
                                                   question=state.get("question", ""))
        except Exception as e:
            log.append(f"   ⚠️ LLM 图表决策加载失败：{type(e).__name__}：{e}")

        if chart_config:
            chart_type = chart_config["chart_type"]
            if chart_type == "table":
                log.append(
                    f"   ✅ AI 决策：table（数据更适合表格展示）\n"
                    f"   数据：{len(df)} 行 × {len(df.columns)} 列"
                )
                return {**state, "chart_json": "", "chart_source_index": target_index, "chart_meta": {}, "process_log": log}

            # 规则自检 + 自动修正
            check_warnings, chart_config = _sanity_check(
                df, chart_type, chart_config,
                question=state.get("question", ""), intent=intent,
            )
            log.extend(check_warnings)
            if chart_config:
                chart_type = chart_config["chart_type"]

            chart_json, chart_meta = _build_figure(
                df, chart_type, title=intent, intent=intent,
                chart_config=chart_config, return_meta=True,
            )
            y2_part = f" | Y2={chart_config.get('y2_col')}" if chart_config.get("y2_col") else ""
            mode_part = f" | 模式={chart_config.get('series_mode')}" if chart_config.get("series_mode") else ""
            log.append(
                f"   ✅ AI 决策：{chart_config['chart_type']}"
                f" | X={chart_config['x_col']} | Y={chart_config['y_col']}"
                f"{y2_part}{mode_part} | 标题=\"{chart_config.get('title', '')}\"\n"
                f"   数据：{len(df)} 行 × {len(df.columns)} 列"
            )
            log.append(_format_chart_meta(chart_meta))
        else:
            # 规则兜底
            log.append("   ⚠️ AI 决策未生效，回退到规则推断")
            chart_type = _infer_chart_type(df, intent)

            if chart_type == "table":
                log.append(f"   📋 判断结果：纯表格展示（数据列：{list(df.columns)}）")
                return {**state, "chart_json": "", "chart_source_index": target_index, "chart_meta": {}, "process_log": log}

            chart_json, chart_meta = _build_figure(
                df, chart_type, title=intent, intent=intent, return_meta=True
            )
            log.append(
                f"   ✅ 图表类型：{chart_type}（规则推断）\n"
                f"   数据：{len(df)} 行 × {len(df.columns)} 列"
            )
            log.append(_format_chart_meta(chart_meta))
        return {**state, "chart_json": chart_json, "chart_source_index": target_index,
                "chart_meta": chart_meta, "process_log": log}

    except Exception as e:
        log.append(f"   ❌ 图表生成失败：{e}")
        return {**state, "chart_json": "", "chart_source_index": 0, "chart_meta": {},
                "error": state.get("error") or f"Chart 生成失败: {e}", "process_log": log}
