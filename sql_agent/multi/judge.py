"""
LLM-as-Judge — 质量评估节点
评估 SQL 准确性 / 图表适配性 / 结论质量，输出 0-10 分
同时将评分追加写入 QueryLogger
"""

import os
import json
import re
from .state import GraphState


_SYSTEM_PROMPT = """你是一个 AI 输出质量评审专家。
请对以下数据分析流程的输出质量进行评分（每项 0-10 分），输出 JSON 格式，不要解释。

评分维度：
- sql_correctness: SQL 是否准确回答了用户问题（参考统计摘要判断数据是否合理）
- chart_fitness: 图表类型是否适合展示该数据（参考统计摘要判断数据特征与图表匹配度，无图表时评 5）
- summary_quality: 分析结论是否准确、清晰、有价值（参考统计摘要验证关键数字是否一致）

输出格式：
{"sql_correctness": 8, "chart_fitness": 9, "summary_quality": 7}

注意：利用下方的"数据统计摘要"和"真实图表元数据"来客观评估——检查 SQL 是否查到了合理的数据分布，图表是否覆盖用户要求的 X/Y/分组/双轴/trace，结论中的数字是否与统计摘要一致。若用户要求分组趋势但图表未按分组拆分 trace，chart_fitness 应明显扣分。
"""


def _resolve_log_path(raw_path: str) -> str:
    """将相对日志路径解析为绝对路径（相对 sql_agent/ 向上两级 → 项目根目录）"""
    if os.path.isabs(raw_path):
        return raw_path
    # __file__ = sql_agent/agents/judge.py → ../../ = 项目根
    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.normpath(os.path.join(repo_root, raw_path))


def _format_chart_meta_for_judge(chart_meta: dict) -> str:
    """格式化 Chart Agent 真实图表元数据，避免把完整 chart_json 塞给 Judge。"""
    if not chart_meta:
        return "无图表元数据"
    traces = chart_meta.get("traces") or []
    trace_preview = traces[:20]
    lines = [
        f"chart_type: {chart_meta.get('chart_type')}",
        f"x_col: {chart_meta.get('x_col')}",
        f"y_cols: {chart_meta.get('y_cols')}",
        f"color_col: {chart_meta.get('color_col')}",
        f"series_mode: {chart_meta.get('series_mode')}",
        f"trace_count: {chart_meta.get('trace_count', len(traces))}",
        "traces:",
    ]
    for t in trace_preview:
        lines.append(
            f"  - name={t.get('name')}, type={t.get('type')}, "
            f"xaxis={t.get('xaxis')}, yaxis={t.get('yaxis')}, "
            f"legendgroup={t.get('legendgroup')}"
        )
    if len(traces) > len(trace_preview):
        lines.append(f"  ... 另 {len(traces) - len(trace_preview)} 条 trace")
    return "\n".join(lines)


def _build_statistical_summary(sql_results: list) -> str:
    """为每个成功的 SQL 结果生成 pandas describe() 统计摘要。"""
    import pandas as pd

    parts = []
    for i, r in enumerate(sql_results, 1):
        if not (r.get("success") and r.get("data")):
            continue
        try:
            df = pd.DataFrame(r["data"])
            if df.empty:
                continue
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            if not numeric_cols:
                parts.append(f"子问题 {i}：无数值列，未生成统计摘要")
                continue
            desc = df[numeric_cols].describe()
            parts.append(
                f"子问题 {i} 统计摘要（{r.get('question', '')}）：\n{desc.to_string()}"
            )
        except Exception:
            parts.append(f"子问题 {i}：统计摘要生成失败")
    return "\n\n".join(parts) if parts else "无有效数据，未生成统计摘要"


def judge_node(state: GraphState) -> GraphState:
    """LLM-as-Judge 节点：评估整体输出质量，并写入查询历史"""
    from sql_agent.llm import get_llm_client
    from sql_agent._config import load_settings
    from sql_agent.feedback.logger import QueryLogger

    question = state.get("question", "")
    sql_results = state.get("sql_results", [])
    chart_json = state.get("chart_json", "")
    chart_meta = state.get("chart_meta", {})
    summary = state.get("summary", "")
    log = list(state.get("process_log") or [])
    log.append("🏅 [LLM-as-Judge] 正在评估输出质量...")

    first_sql = ""
    first_data_preview = ""
    if sql_results:
        first_sql = sql_results[0].get("sql", "")
        rows = sql_results[0].get("data", [])[:3]
        first_data_preview = str(rows)

    chart_type = "无图表"
    if chart_meta:
        chart_type = chart_meta.get("chart_type", "已生成图表")
    elif chart_json:
        try:
            fig_dict = json.loads(chart_json)
            chart_type = fig_dict.get("data", [{}])[0].get("type", "unknown")
        except Exception:
            chart_type = "已生成图表"

    chart_meta_text = _format_chart_meta_for_judge(chart_meta)

    # 生成统计摘要
    stats_summary = _build_statistical_summary(sql_results)
    log.append("   📊 已生成数据统计摘要")

    user_content = (
        f"用户问题：{question}\n\n"
        f"生成的 SQL：\n{first_sql}\n\n"
        f"数据预览（前3行）：{first_data_preview}\n\n"
        f"数据统计摘要：\n{stats_summary}\n\n"
        f"图表类型：{chart_type}\n\n"
        f"真实图表元数据：\n{chart_meta_text}\n\n"
        f"分析结论：{summary}"
    )

    default_scores = {"sql_correctness": 5, "chart_fitness": 5, "summary_quality": 5}
    scores = default_scores

    try:
        settings = load_settings()
        llm = get_llm_client(settings["llm"])

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        raw = llm.chat(messages, temperature=0.0)

        match = re.search(r"\{[\s\S]+\}", raw)
        scores = json.loads(match.group()) if match else default_scores

    except Exception as e:
        log.append(f"   ⚠️ 评分 LLM 调用失败（{e}），使用默认分")
        scores = default_scores

    log.append(
        f"   SQL 准确性：{scores.get('sql_correctness', '?')}/10  "
        f"图表适配：{scores.get('chart_fitness', '?')}/10  "
        f"结论质量：{scores.get('summary_quality', '?')}/10"
    )

    # 写入查询历史（使用绝对路径，兼容从任意目录启动）
    try:
        settings = load_settings()
        raw_path = settings.get("feedback", {}).get("log_path", "./logs/queries.jsonl")
        log_path = _resolve_log_path(raw_path)
        logger = QueryLogger(log_path)

        first_result = sql_results[0] if sql_results else {}
        logger.log(
            question=question,
            sql=first_result.get("sql", ""),
            success=first_result.get("success", False),
            rows_count=len(first_result.get("data", [])),
            error=first_result.get("error", ""),
            retry_count=first_result.get("retry_count", 0),
            confidence=first_result.get("confidence", 0.0),
            judge_scores=scores,
            summary=summary,
            chart_json=chart_json,
        )
        log.append("   📝 已记录到查询历史")
    except Exception as e:
        log.append(f"   ⚠️ 写入历史失败：{e}")

    return {**state, "judge_scores": scores, "process_log": log}
