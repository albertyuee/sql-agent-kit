"""配置加载与新用户开箱体验的测试。

用标准库 unittest，不引入额外依赖。运行：
    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sql_agent._config import load_settings  # noqa: E402


SETTINGS_YAML = """
agent:
  confidence_threshold: 0.6
  max_retry: 3
  max_tables_in_prompt: 10
executor:
  max_query_rows: 500
llm:
  provider: siliconflow
  siliconflow:
    model: Qwen/Qwen2.5-72B-Instruct
  openai:
    model: gpt-4o
fewshot:
  store_path: ./data/fewshot.json
feedback:
  log_path: ./logs/queries.jsonl
"""


class ProviderOverrideTests(unittest.TestCase):
    """配置页把 provider 写进 .env，Agent 必须真的按它派发客户端。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name) / "config"
        self.config_dir.mkdir()
        (self.config_dir / "settings.yaml").write_text(
            SETTINGS_YAML, encoding="utf-8"
        )
        self.env_file = Path(self._tmp.name) / ".env"

    def load(self, env_content: str, environ: dict | None = None):
        self.env_file.write_text(env_content, encoding="utf-8")
        # load_dotenv 不会删掉已存在的环境变量，必须显式清干净
        clean = {k: v for k, v in os.environ.items() if k != "LLM_PROVIDER"}
        clean.update(environ or {})
        with mock.patch.dict(os.environ, clean, clear=True):
            return load_settings(
                config_dir=str(self.config_dir), env_file=str(self.env_file)
            )

    def test_env_var_overrides_the_provider_in_settings_yaml(self):
        settings = self.load(
            "LLM_PROVIDER=openai\nOPENAI_API_KEY=sk-test\n",
        )

        self.assertEqual(settings["llm"]["provider"], "openai")

    def test_falls_back_to_settings_yaml_when_env_is_silent(self):
        settings = self.load("OPENAI_API_KEY=sk-test\n")

        self.assertEqual(settings["llm"]["provider"], "siliconflow")

    def test_other_llm_settings_survive_the_override(self):
        settings = self.load("LLM_PROVIDER=qwen\n")

        self.assertEqual(settings["llm"]["provider"], "qwen")
        self.assertEqual(settings["llm"]["openai"]["model"], "gpt-4o")
        self.assertEqual(settings["llm"]["siliconflow"]["model"],
                         "Qwen/Qwen2.5-72B-Instruct")


class BuildAgentUsesSharedLoaderTests(unittest.TestCase):
    """build_agent 必须走 load_settings，否则 SQL Agent 和多 Agent 节点
    会读两份不同的配置，provider 覆盖只生效一半。"""

    def test_build_agent_goes_through_load_settings(self):
        source = (REPO_ROOT / "sql_agent" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn(
            "load_settings", source,
            "build_agent 没有复用 load_settings，provider 覆盖不会生效",
        )


class EnvExampleTests(unittest.TestCase):
    """.env.example 是新用户唯一的配置模板，它错了新人就跑不起来。"""

    @classmethod
    def setUpClass(cls):
        cls.path = REPO_ROOT / ".env.example"
        cls.text = cls.path.read_text(encoding="utf-8")

    def parsed(self) -> dict[str, str]:
        """按 backend/routers/config.py 的规则解析，保证和配置页看到的一致。"""
        env = {}
        for line in self.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
        return env

    def test_default_provider_has_an_api_key_line(self):
        """settings.yaml 默认用 siliconflow，模板里就必须有它的 Key，
        否则照抄模板的新用户第一次提问必然 401。"""
        with open(REPO_ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
            default_provider = yaml.safe_load(f)["llm"]["provider"]

        env = self.parsed()
        self.assertEqual(env.get("LLM_PROVIDER"), default_provider,
                         "模板里的 LLM_PROVIDER 和 settings.yaml 默认值不一致")

        key_name = {
            "siliconflow": "SILICONFLOW_API_KEY",
            "openai": "OPENAI_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "bailian": "BAILIAN_API_KEY",
        }[default_provider]

        self.assertIn(key_name, env, f"模板缺少默认 provider 需要的 {key_name}")

    def test_no_value_carries_an_inline_comment(self):
        """配置页读 .env 时不剥行内注释，会把注释整串当成值。"""
        offenders = {
            key: value
            for key, value in self.parsed().items()
            if "#" in value
        }

        self.assertEqual(offenders, {}, "这些配置项的值里混进了行内注释")

    def test_defaults_to_sqlite_so_no_database_install_is_needed(self):
        self.assertEqual(
            self.parsed().get("DB_TYPE"), "sqlite",
            "模板默认不是 sqlite，新用户会被迫先装一个数据库",
        )
        self.assertIn("DB_SQLITE_PATH", self.parsed())

    def test_still_documents_the_mysql_options(self):
        """默认走 sqlite，但 MySQL 的配置项不能删，要留成注释。"""
        for key in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"):
            self.assertIn(key, self.text, f"模板里找不到 {key}")


if __name__ == "__main__":
    unittest.main()
