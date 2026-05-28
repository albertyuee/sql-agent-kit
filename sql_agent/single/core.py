import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm.base import BaseLLMClient
from ..schema.loader import SchemaLoader
from ..schema.annotator import SchemaAnnotator
from ..schema.selector import SchemaSelector
from ..fewshot.store import FewShotStore
from ..fewshot.retriever import FewShotRetriever
from ..validator.safety import SafetyValidator
from ..validator.syntax import SyntaxValidator
from ..validator.confidence import ConfidenceEstimator
from ..executor.runner import SQLRunner
from ..executor.formatter import ResultFormatter
from ..feedback.logger import QueryLogger
from .prompt_builder import PromptBuilder


@dataclass
class QueryResult:
    """查询结果的结构化封装"""
    question: str
    sql: str = ""
    success: bool = False
    data: list[dict] = field(default_factory=list)
    formatted_table: str = ""
    error: str = ""
    confidence: float = 1.0
    retry_count: int = 0
    # 低于置信度阈值时，需要用户确认
    need_confirm: bool = False


class SQLAgent:
    """
    SQL Agent 主链路
    完整流程：
      用户问题
        → Schema 上下文构建（白名单过滤 + 语义注释 + 智能筛选）
        → Few-shot 检索
        → Prompt 组装
        → LLM 生成 SQL
        → 安全校验 → 语法校验 → 置信度评估
        → 执行 SQL
          → 成功 → 格式化 → 记录日志
          → 失败 → 错误反馈给 LLM 重试（最多 max_retry 次）
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        schema_loader: SchemaLoader,
        annotator: SchemaAnnotator,
        runner: SQLRunner,
        fewshot_store: Optional[FewShotStore] = None,
        max_retry: int = 3,
        confidence_threshold: float = 0.6,
        max_tables: int = 10,
        log_path: str = "./logs/queries.jsonl",
    ):
        self._llm = llm
        self._schema_loader = schema_loader
        self._annotator = annotator
        self._runner = runner
        self._selector = SchemaSelector(max_tables=max_tables)
        self._retriever = FewShotRetriever(fewshot_store or FewShotStore()) if fewshot_store else None
        self._safety = SafetyValidator()
        self._syntax = SyntaxValidator()
        self._confidence = ConfidenceEstimator()
        self._formatter = ResultFormatter()
        self._logger = QueryLogger(log_path)
        self._prompt_builder = PromptBuilder()
        self._max_retry = max_retry
        self._confidence_threshold = confidence_threshold

    def query(self, question: str, progress_callback=None) -> QueryResult:
        """
        主入口：接收自然语言问题，返回查询结果。
        progress_callback 可选，用于向前端流式推送阶段进度。
        """
        result = QueryResult(question=question)

        def emit(source: str, status: str, **extra):
            if progress_callback:
                progress_callback({"source": source, "status": status, **extra})

        # 1. 构建 Schema 上下文
        emit("schema", "running")
        try:
            raw_schema = self._schema_loader.load()
            enriched_schema = self._annotator.annotate(raw_schema)
            selected_schema = self._selector.select(question, enriched_schema)
            schema_text = self._annotator.format_for_prompt(selected_schema)
            emit("schema", "done")
        except Exception:
            emit("schema", "error")
            raise

        # 2. Few-shot 检索
        emit("fewshot", "running")
        fewshot_text = ""
        try:
            if self._retriever:
                examples = self._retriever.retrieve(question)
                fewshot_text = self._retriever.format_for_prompt(examples)
            emit("fewshot", "done")
        except Exception:
            emit("fewshot", "error")
            raise

        # 3. ReAct 重试循环
        error_feedback = ""
        for attempt in range(self._max_retry + 1):
            # 3.1 生成 SQL
            emit("generate_sql", "running", attempt=attempt)
            messages = self._prompt_builder.build_sql_generation_prompt(
                question=question,
                schema_text=schema_text,
                fewshot_text=fewshot_text,
                error_feedback=error_feedback,
                retry_index=attempt,
            )
            raw_output = self._llm.chat(messages, temperature=0.0)
            sql = self._extract_sql(raw_output)
            result.sql = sql
            result.retry_count = attempt
            emit("generate_sql", "done", attempt=attempt, sql=sql)

            # 3.2 安全校验
            emit("validate", "running", attempt=attempt)
            safe, reason = self._safety.validate(sql)
            if not safe:
                emit("validate", "error", reason=reason)
                result.error = f"安全校验失败: {reason}"
                self._logger.log(question, sql, False, error=result.error)
                return result

            # 3.3 语法校验
            valid, reason = self._syntax.validate(sql)
            if not valid:
                emit("validate", "error", reason=reason)
                error_feedback = f"SQL 语法错误: {reason}"
                continue  # 重试

            # 3.4 置信度评估
            confidence = self._confidence.estimate(sql, question)
            result.confidence = confidence
            emit("validate", "done", attempt=attempt, confidence=confidence)

            if confidence < self._confidence_threshold:
                # 置信度过低，不执行，让用户确认
                result.need_confirm = True
                result.error = (
                    f"置信度较低（{confidence:.0%}），建议人工确认 SQL 再执行。"
                )
                self._logger.log(question, sql, False, confidence=confidence, error=result.error)
                return result

            # 3.5 执行 SQL
            emit("execute", "running", attempt=attempt)
            success, data_or_error = self._runner.run(sql)

            if success:
                rows = data_or_error
                emit("execute", "done", attempt=attempt, rows_count=len(rows))
                result.success = True
                result.data = rows
                result.formatted_table = self._formatter.to_table(rows)
                self._logger.log(
                    question, sql, True,
                    rows_count=len(rows),
                    retry_count=attempt,
                    confidence=confidence,
                )
                return result
            else:
                # 执行失败，把错误信息反馈给 LLM，下一轮重试
                error_feedback = data_or_error
                emit("execute", "error", attempt=attempt, reason=error_feedback)
                if attempt == self._max_retry:
                    result.error = f"执行失败（已重试 {self._max_retry} 次）: {error_feedback}"
                    self._logger.log(question, sql, False, error=result.error, retry_count=attempt)
                    return result

        return result

    def _extract_sql(self, text: str) -> str:
        """
        从 LLM 输出中提取 SQL 语句
        兼容带 markdown 代码块和不带代码块的输出
        """
        # 去掉 ```sql ... ``` 包裹
        match = re.search(r"```(?:sql)?\s*([\s\S]+?)```", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # 没有代码块，直接返回清理后的文本
        return text.strip()
