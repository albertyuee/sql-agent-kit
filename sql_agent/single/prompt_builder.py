from ..schema.annotator import SchemaAnnotator


class PromptBuilder:
    """
    Prompt 构建器
    将 Schema、Few-shot 示例、用户问题组合成最终发送给 LLM 的 Prompt
    设计原则：结构清晰、信息密度高、避免歧义
    """

    SYSTEM_PROMPT = """你是一个专业的 Text-to-SQL 助手。
你的任务是根据用户的自然语言问题，生成准确的 SQL 查询语句。

## 规则
1. 只生成 SELECT 查询，禁止生成任何修改数据的语句
2. 只能使用"可用数据库表"中提供的表和字段，禁止使用不存在的表或字段
3. 必须严格遵守字段含义说明，不要猜测字段含义
4. 输出格式：只输出 SQL 代码，不要任何解释，不要 markdown 代码块标记
5. 如果问题模糊无法确定，生成最合理的 SQL 并在注释中说明假设条件
6. 按周统计时，周起始日期必须去掉时分秒；MySQL 可使用 DATE(DATE_SUB(created_at, INTERVAL WEEKDAY(created_at) DAY)) AS week_start，禁止直接用 DATE_SUB(created_at, INTERVAL WEEKDAY(created_at) DAY) 分组
"""

    def build_sql_generation_prompt(
        self,
        question: str,
        schema_text: str,
        fewshot_text: str = "",
        error_feedback: str = "",
        retry_index: int = 0,
    ) -> list[dict]:
        """
        构建 SQL 生成的 Prompt（messages 格式）
        :param question: 用户问题
        :param schema_text: 格式化后的 Schema 文本
        :param fewshot_text: Few-shot 示例文本
        :param error_feedback: 上次执行的错误信息（重试时使用）
        :param retry_index: 当前重试次数
        """
        user_content_parts = []

        # 1. Schema 上下文
        user_content_parts.append(f"## 可用数据库表\n\n{schema_text}")

        # 2. Few-shot 示例（如果有）
        if fewshot_text:
            user_content_parts.append(fewshot_text)

        # 3. 用户问题
        user_content_parts.append(f"## 用户问题\n{question}")

        # 4. 错误反馈（重试时）
        if error_feedback and retry_index > 0:
            user_content_parts.append(
                f"## ⚠️ 上次生成的 SQL 执行出错，请修正\n"
                f"错误信息：{error_feedback}\n"
                f"请仔细检查字段名、表名、JOIN 条件，重新生成正确的 SQL。"
            )

        return [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(user_content_parts)},
        ]

    def build_confidence_prompt(self, question: str, sql: str) -> list[dict]:
        """构建置信度自评估 Prompt"""
        return [
            {"role": "system", "content": "你是一个 SQL 审核专家。"},
            {
                "role": "user",
                "content": (
                    f"请评估以下 SQL 是否正确回答了用户问题，"
                    f"返回 0~1 之间的置信度数字，只输出数字，不要解释。\n\n"
                    f"用户问题: {question}\n\n"
                    f"SQL:\n{sql}"
                ),
            },
        ]
