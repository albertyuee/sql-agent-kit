from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from backend.utils import sanitize

router = APIRouter()


class QueryRequest(BaseModel):
    question: str


_QUERY_STAGE_LABELS = {
    "schema": "读取Schema",
    "fewshot": "检索示例",
    "generate_sql": "生成SQL",
    "validate": "校验SQL",
    "execute": "执行查询",
    "chart": "生成图表",
}


def _status(result):
    if result.need_confirm:
        return "need_confirm"
    if result.success:
        return "success"
    return "failed"


def _make_response_payload(result, chart_obj=None):
    return sanitize({
        "sql": result.sql or "",
        "success": result.success,
        "status": _status(result),
        "confidence": result.confidence,
        "retry_count": result.retry_count,
        "data": result.data or [],
        "formatted_table": result.formatted_table or "",
        "error": result.error or "",
        "chart": chart_obj,
    })


@router.post("/query")
async def run_query(req: QueryRequest):
    import json
    import pandas as pd
    from backend.services.agent_service import get_agent
    from sql_agent.multi.chart import _infer_chart_type, _build_figure

    agent = get_agent()
    result = agent.query(req.question)

    # 生成图表
    chart_obj = None
    if result.success and result.data:
        try:
            df = pd.DataFrame(result.data)
            chart_type = _infer_chart_type(df, req.question)
            if chart_type != "table":
                chart_json = _build_figure(
                    df, chart_type,
                    title=req.question,
                    intent=req.question,
                )
                if chart_json:
                    chart_obj = json.loads(chart_json)
        except Exception:
            pass

    return JSONResponse(_make_response_payload(result, chart_obj))


@router.get("/query/stream")
async def query_stream(question: str):
    import asyncio
    import json
    import time
    import pandas as pd
    from backend.utils import dumps
    from backend.services.agent_service import get_agent
    from sql_agent.multi.chart import _infer_chart_type, _build_figure

    async def event_generator():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        result_holder: dict = {}
        done = asyncio.Event()
        stage_started_at: dict[str, float] = {}

        def emit_stage(payload: dict):
            source = payload.get("source", "")
            status = payload.get("status", "")
            if status == "running":
                stage_started_at[source] = time.perf_counter()
            event = {
                "type": "stage",
                "source": source,
                "status": status,
                "label": _QUERY_STAGE_LABELS.get(source, source),
            }
            if source in stage_started_at and status in ("done", "error"):
                event["elapsed_ms"] = int((time.perf_counter() - stage_started_at[source]) * 1000)
            for key in ("attempt", "confidence", "rows_count", "reason"):
                if key in payload:
                    event[key] = payload[key]
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)

        def run_in_thread():
            try:
                agent = get_agent()
                result = agent.query(question, progress_callback=emit_stage)
                chart_obj = None
                if result.success and result.data:
                    emit_stage({"source": "chart", "status": "running"})
                    try:
                        df = pd.DataFrame(result.data)
                        chart_type = _infer_chart_type(df, question)
                        if chart_type != "table":
                            chart_json = _build_figure(df, chart_type, title=question, intent=question)
                            if chart_json:
                                chart_obj = json.loads(chart_json)
                        emit_stage({"source": "chart", "status": "done"})
                    except Exception as e:
                        emit_stage({"source": "chart", "status": "error", "reason": str(e)})
                result_holder["payload"] = _make_response_payload(result, chart_obj)
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put({"type": "_done"}), loop)
                loop.call_soon_threadsafe(done.set)

        task = asyncio.create_task(asyncio.to_thread(run_in_thread))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    if done.is_set():
                        break
                    yield ": heartbeat\n\n"
                    continue
                if item.get("type") == "_done":
                    break
                event_type = item.get("type", "log")
                payload = {k: v for k, v in item.items() if k != "type"}
                yield f"event: {event_type}\ndata: {dumps(payload)}\n\n"

            await task
            if "error" in result_holder:
                yield f"event: error\ndata: {dumps({'message': result_holder['error']})}\n\n"
            else:
                yield f"event: result\ndata: {dumps(result_holder.get('payload', {}))}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
