"""
SSE 流式管道运行器模块

本模块实现多 Agent 管道的流式执行，支持通过 Server-Sent Events (SSE) 实时推送处理进度和结果。

核心设计思路：
1. 使用 LangGraph 构建多 Agent 协作流程（planner -> sql -> chart -> summary -> judge）
2. 为每个节点函数包装一层代理，在节点执行前后检测 process_log 的变化
3. 将新增的日志条目通过 run_coroutine_threadsafe 推送到 asyncio.Queue
4. 在独立线程（ThreadPoolExecutor）中运行管道，主线程异步消费队列并 yield SSE 事件
5. 不修改 sql_agent/ 核心包，实现零侵入式流式输出

SSE 事件类型：
- log: 实时日志条目，包含处理进度信息
- result: 最终结果，包含 SQL 查询结果、图表数据、摘要等
- chart: 图表配置数据（单独发送，支持大图表传输）
- error: 错误信息
- _heartbeat: 心跳包，保持连接活跃
- _done: 内部标记，表示管道执行完成
"""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

# 导入各 Agent 节点函数
from sql_agent.multi.classifier import classifier_node  # 简单问题分类节点
from sql_agent.multi.planner import planner_node    # 任务规划节点
from sql_agent.multi.sql_node import sql_node       # SQL 执行节点
from sql_agent.multi.chart import chart_node        # 图表生成节点
from sql_agent.multi.summary import summary_node    # 结果摘要节点
from sql_agent.multi.judge import judge_node        # 结果评估节点
from sql_agent.multi.state import GraphState        # 图状态类型定义
from sql_agent.graph.pipeline import _route_after_classifier  # 分类器后路由
from sql_agent.graph.pipeline import _route_after_sql  # SQL 执行后的路由逻辑

# -----------------------------------------------------------------------------
# 全局配置
# -----------------------------------------------------------------------------

# 线程池执行器，最多同时运行 4 个管道任务
_executor = ThreadPoolExecutor(max_workers=4)

# 日志记录器
logger = logging.getLogger(__name__)

# 整个管道的最长运行时间（秒），超时后强制终止
PIPELINE_TIMEOUT = 240


# -----------------------------------------------------------------------------
# 核心函数
# -----------------------------------------------------------------------------

_STAGE_LABELS = {
    "classifier": "意图识别",
    "planner": "任务规划",
    "sql": "SQL生成与执行",
    "chart": "图表生成",
    "summary": "结论生成",
    "judge": "质量评分",
}


def _emit_stage(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                source: str, status: str, elapsed_ms: int | None = None):
    payload = {
        "type": "stage",
        "source": source,
        "status": status,
        "label": _STAGE_LABELS.get(source, source),
    }
    if elapsed_ms is not None:
        payload["elapsed_ms"] = elapsed_ms
    asyncio.run_coroutine_threadsafe(queue.put(payload), loop)


def _wrap_node(node_fn, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, source: str = ""):
    """
    包装 LangGraph 节点函数，实现流式日志输出和中间结果推送。

    工作原理：
    1. 记录节点执行前的 process_log / sql_results / chart_json
    2. 执行原始节点函数
    3. 对比执行后的变化，将新增日志和中间结果推送到异步队列
    4. 通过 run_coroutine_threadsafe 实现跨线程通信

    Args:
        node_fn: 原始的节点函数，如 planner_node, sql_node 等
        queue: asyncio 异步队列，用于传递事件
        loop: asyncio 事件循环，用于 run_coroutine_threadsafe
        source: 节点标识，如 "planner", "sql", "chart" 等

    Returns:
        wrapped: 包装后的节点函数，接收并返回 GraphState
    """
    def wrapped(state: GraphState) -> GraphState:
        stage_start = time.perf_counter()
        _emit_stage(queue, loop, source, "running")

        # 记录执行前的快照
        prev_log_len = len(state.get("process_log") or [])
        prev_sql_results = state.get("sql_results") or []
        prev_chart_json = state.get("chart_json", "")

        try:
            # 执行原始节点函数，获取更新后的状态
            new_state = node_fn(state)
        except Exception:
            elapsed_ms = int((time.perf_counter() - stage_start) * 1000)
            _emit_stage(queue, loop, source, "error", elapsed_ms)
            raise

        elapsed_ms = int((time.perf_counter() - stage_start) * 1000)
        _emit_stage(queue, loop, source, "done", elapsed_ms)

        # 1. 推送新增的日志条目（现有逻辑）
        new_log = new_state.get("process_log") or []
        for entry in new_log[prev_log_len:]:
            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "log",
                    "text": entry,
                    "source": source
                }),
                loop,
            )

        # 2. sql_node 完成后立即推送 SQL 结果
        if source == "sql":
            new_sql_results = new_state.get("sql_results") or []
            if len(new_sql_results) > len(prev_sql_results):
                payload = []
                for r in new_sql_results:
                    data = r.get("data", [])
                    truncated = len(data) > 500
                    payload.append({
                        "question": r.get("question", ""),
                        "sql": r.get("sql", ""),
                        "success": r.get("success", False),
                        "data": data[:500] if truncated else data,
                        "truncated": truncated,
                        "error": r.get("error", ""),
                        "confidence": r.get("confidence", 0.0),
                    })
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "sql_result", "sql_results": payload}),
                    loop,
                )

        # 3. chart_node 完成后立即推送图表
        if source == "chart":
            new_chart = new_state.get("chart_json", "")
            if new_chart and new_chart != prev_chart_json:
                try:
                    chart_obj = json.loads(new_chart)
                    asyncio.run_coroutine_threadsafe(
                        queue.put({
                            "type": "chart",
                            "chart": chart_obj,
                            "chart_source_index": new_state.get("chart_source_index", 0),
                        }),
                        loop,
                    )
                except Exception:
                    pass

        return new_state

    return wrapped


def _build_streaming_pipeline(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    """
    构建支持流式输出的 LangGraph 管道。

    管道流程：
    planner -> sql_agent -> (ok) chart_agent -> summary_agent -> judge_agent -> END
                       \
                        (error) -> END

    每个节点都通过 _wrap_node 包装以支持流式日志输出。

    Args:
        queue: 异步队列，用于接收日志事件
        loop: asyncio 事件循环

    Returns:
        compiled_graph: 编译后的 LangGraph，可通过 invoke() 调用
    """
    from langgraph.graph import StateGraph, END

    # 创建状态图，使用 GraphState 定义状态结构
    graph = StateGraph(GraphState)

    # 添加节点：每个节点都包装了流式日志功能
    graph.add_node("classifier",   _wrap_node(classifier_node, queue, loop, source="classifier"))
    graph.add_node("planner",      _wrap_node(planner_node,  queue, loop, source="planner"))
    graph.add_node("sql_agent",    _wrap_node(sql_node,      queue, loop, source="sql"))
    graph.add_node("chart_agent",  _wrap_node(chart_node,    queue, loop, source="chart"))
    graph.add_node("summary_agent",_wrap_node(summary_node,  queue, loop, source="summary"))
    graph.add_node("judge_agent",  _wrap_node(judge_node,    queue, loop, source="judge"))

    # 设置入口点：管道从 classifier 开始
    graph.set_entry_point("classifier")

    # classifier 后路由：简单问题跳过 planner，否则进入 planner
    graph.add_conditional_edges(
        "classifier",
        _route_after_classifier,
        {"skip": "sql_agent", "plan": "planner"},
    )

    # planner 完成后进入 sql_agent
    graph.add_edge("planner", "sql_agent")
    
    # sql_agent 执行后根据结果路由：
    # - ok: 继续生成图表
    # - error: 直接结束
    graph.add_conditional_edges(
        "sql_agent",
        _route_after_sql,
        {"ok": "chart_agent", "error": END},
    )
    
    # 后续固定流程
    graph.add_edge("chart_agent", "summary_agent")
    graph.add_edge("summary_agent", "judge_agent")
    graph.add_edge("judge_agent", END)
    
    return graph.compile()


async def run_pipeline_streaming(question: str) -> AsyncGenerator[dict, None]:
    """
    运行多 Agent 管道并以流式方式返回结果。

    执行流程：
    1. 创建异步队列和结果容器
    2. 在线程池中启动管道执行
    3. 主线程异步消费队列，yield SSE 事件
    4. 管道完成后整理结果并返回

    Args:
        question: 用户的问题或查询

    Yields:
        dict: SSE 兼容的事件字典，包含以下类型：
        - {"type": "log", "text": str, "source": str}: 实时日志
        - {"type": "result", "sql_results": [...], ...}: 最终结果
        - {"type": "chart", "chart": {...}, ...}: 图表数据
        - {"type": "error", "message": str}: 错误信息
        - {"type": "_heartbeat"}: 心跳包（保持连接）
    """
    # 获取当前 asyncio 事件循环（必须在 async 函数中调用）
    loop = asyncio.get_event_loop()
    
    # 创建异步队列，用于跨线程传递日志事件
    queue: asyncio.Queue = asyncio.Queue()
    
    # 结果容器，用于从线程中获取最终状态
    result_holder: dict = {}
    
    # 线程完成事件，用于检测管道是否执行完毕
    thread_done = asyncio.Event()

    # 构建流式管道
    pipeline = _build_streaming_pipeline(queue, loop)

    def run_in_thread():
        """
        在线程池中执行的管道运行函数。

        包含完整的异常处理，确保无论成功还是失败都能通知主线程。
        """
        try:
            # 调用 LangGraph 管道的 invoke 方法
            # 初始状态包含问题文本和空的 process_log
            state = pipeline.invoke({
                "question": question,
                "process_log": []
            })
            
            # 将最终状态保存到结果容器
            result_holder["state"] = state
            
            # 记录成功完成的日志
            logger.info(
                f"[pipeline] done, "
                f"sql_results={len(state.get('sql_results') or [])}, "
                f"chart_json={'yes' if state.get('chart_json') else 'no'}"
            )
        except Exception as e:
            # 捕获并记录异常
            logger.exception(f"[pipeline] error: {e}")
            result_holder["error"] = str(e)
        finally:
            # 发送完成标记到队列
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "_done"}),
                loop
            )
            # 通知主线程线程已结束
            loop.call_soon_threadsafe(thread_done.set)

    # 在线程池中异步执行管道
    future = loop.run_in_executor(_executor, run_in_thread)
    
    # 计算截止时间
    deadline = loop.time() + PIPELINE_TIMEOUT

    # 主循环：持续消费队列直到收到完成标记或超时
    while True:
        # 计算剩余时间
        remaining = deadline - loop.time()
        
        # 检查是否超时
        if remaining <= 0:
            logger.warning("[pipeline] global timeout reached, aborting")
            future.cancel()  # 取消正在执行的任务
            yield {
                "type": "error",
                "message": f"分析超时（超过 {PIPELINE_TIMEOUT} 秒），请简化问题后重试"
            }
            return

        try:
            # 从队列中获取事件，设置超时避免永久阻塞
            item = await asyncio.wait_for(
                queue.get(),
                timeout=min(15.0, remaining)  # 最长等待 15 秒
            )
        except asyncio.TimeoutError:
            # 超时时检查线程是否已完成
            # 如果线程已结束则退出循环，避免无限心跳
            if thread_done.is_set():
                break
            # 否则发送心跳包保持连接
            yield {"type": "_heartbeat"}
            continue

        # 收到完成标记，退出循环
        if item["type"] == "_done":
            break
        
        # 正常日志事件，直接 yield 给调用方
        yield item

    # 等待线程执行器任务完成，确保资源清理
    await future

    # 检查是否有执行错误
    if "error" in result_holder:
        yield {
            "type": "error",
            "message": result_holder["error"]
        }
        return

    # 获取最终状态
    state = result_holder.get("state", {})
    sql_results = state.get("sql_results") or []
    chart_source_index = state.get("chart_source_index", 0)

    # 构建每个子问题的结果，并截断超大数据避免传输过大
    results_payload = []
    for r in sql_results:
        data = r.get("data", [])
        # 如果数据超过 500 条，则截断并标记
        truncated = len(data) > 500
        results_payload.append({
            "question": r.get("question", ""),
            "sql": r.get("sql", ""),
            "success": r.get("success", False),
            "data": data[:500] if truncated else data,  # 截断数据
            "truncated": truncated,  # 标记是否被截断
            "error": r.get("error", ""),
            "confidence": r.get("confidence", 0.0),
        })

    # 图表已由 chart_node 完成后通过 _wrap_node 增量推送，此处不重复

    # 返回最终结果（summary + judge_scores 在后几个节点才生成）
    yield {
        "type": "result",
        "sql_results": results_payload,  # 兜底：若前端未收到增量事件，提供完整数据
        "chart_source_index": chart_source_index,
        "summary": state.get("summary", ""),
        "judge_scores": state.get("judge_scores", {}),
        "error": state.get("error", ""),
        "skip_planner": state.get("skip_planner", False),
    }
