"""
sql-agent-kit 统一入口
用法：
    from sql_agent import build_agent
    agent = build_agent()
    result = agent.query("上个月销售额最高的商品是什么？")
"""

import os
import yaml
from dotenv import load_dotenv

from .single.core import SQLAgent, QueryResult
from ._config import load_settings
from .llm import get_llm_client
from .schema.loader import SchemaLoader
from .schema.annotator import SchemaAnnotator
from .executor.db_connector import create_db_engine
from .executor.runner import SQLRunner
from .fewshot.store import FewShotStore
from .graph.pipeline import build_pipeline


def build_agent(
    config_dir: str = "./config",
    data_dir: str = "./data",
    env_file: str = ".env",
) -> SQLAgent:
    """
    工厂函数：读取配置，组装所有模块，返回可用的 SQLAgent
    """
    # 加载环境变量
    load_dotenv(env_file)

    # 读取配置文件。
    # settings 必须走 load_settings 而不是自己读 yaml，这样 .env 的
    # LLM_PROVIDER 覆盖才会生效，也才能和多 Agent 节点读到同一份配置。
    settings = load_settings(config_dir=config_dir, env_file=env_file)
    tables_path = os.path.join(data_dir, "tables.yaml")
    annotations_path = os.path.join(data_dir, "schema_annotations.yaml")

    with open(tables_path, "r", encoding="utf-8") as f:
        tables_config = yaml.safe_load(f)
    allowed_tables = tables_config.get("allowed_tables", [])

    # 组装各模块
    llm = get_llm_client(settings["llm"])
    engine = create_db_engine()
    schema_loader = SchemaLoader(engine, allowed_tables)
    annotator = SchemaAnnotator(annotations_path)
    runner = SQLRunner(
        engine,
        timeout=settings["executor"]["query_timeout"],
        max_rows=settings["executor"]["max_rows"],
    )
    fewshot_store = FewShotStore(settings["fewshot"]["store_path"])

    return SQLAgent(
        llm=llm,
        schema_loader=schema_loader,
        annotator=annotator,
        runner=runner,
        fewshot_store=fewshot_store,
        max_retry=settings["agent"]["max_retry"],
        confidence_threshold=settings["agent"]["confidence_threshold"],
        max_tables=settings["agent"]["max_tables_in_prompt"],
        log_path=settings["feedback"]["log_path"],
    )


__all__ = ["build_agent", "build_pipeline", "SQLAgent", "QueryResult"]
