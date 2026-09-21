"""
共享配置加载器
各 Agent 节点用此函数读取 settings.yaml，避免重复代码
"""

import os
import yaml
from dotenv import load_dotenv


def load_settings(config_dir: str = "./config", env_file: str = ".env") -> dict:
    """加载 settings.yaml，自动处理相对路径（兼容从任意工作目录启动）"""
    load_dotenv(env_file)

    # 优先使用环境变量指定的 config 目录
    config_dir = os.environ.get("SQL_AGENT_CONFIG_DIR", config_dir)

    # 若相对路径不存在，尝试从当前文件向上两级查找
    if not os.path.isabs(config_dir) and not os.path.exists(config_dir):
        here = os.path.dirname(__file__)   # sql_agent/
        candidate = os.path.join(here, "..", config_dir)
        if os.path.exists(candidate):
            config_dir = candidate

    settings_path = os.path.join(config_dir, "settings.yaml")
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    # .env 的 LLM_PROVIDER 覆盖 settings.yaml。
    # 配置页把 provider 写进 .env，而这里原本只认 settings.yaml，
    # 导致在界面上切换 provider 不生效。统一在这里覆盖，
    # SQL Agent 和 planner/judge/summary/chart 才会读到同一个 provider。
    provider = os.environ.get("LLM_PROVIDER")
    if provider and isinstance(settings.get("llm"), dict):
        settings["llm"]["provider"] = provider

    return settings


def load_table_names(data_dir: str = "./data") -> list:
    """从 tables.yaml 加载白名单表名列表"""
    data_dir = os.environ.get("SQL_AGENT_DATA_DIR", data_dir)

    if not os.path.isabs(data_dir) and not os.path.exists(data_dir):
        here = os.path.dirname(__file__)
        candidate = os.path.join(here, "..", data_dir)
        if os.path.exists(candidate):
            data_dir = candidate

    tables_path = os.path.join(data_dir, "tables.yaml")
    with open(tables_path, "r", encoding="utf-8") as f:
        tables_config = yaml.safe_load(f)
    return tables_config.get("allowed_tables", [])
